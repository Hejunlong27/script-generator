# -*- coding: utf-8 -*-
"""管线编排：五阶段，每阶段的输入输出都明确。

  INPUT    载入要素库 / 风格包 / 蓝图 -> 校验 -> 自动补齐        (阻断型错误在这里暴露)
  COMPILE  蓝图 -> N 张组装卡（确定性，不调模型）
  RENDER   组装卡 -> 单集剧本（LLM 或模板降级）
  VALIDATE 结构 / 引用 / 规则三层校验，失败回喂修复
  EXPORT   md / json / csv / fountain

对外只暴露 run() / preview() / catalog()，server 与 CLI 都走这里。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

from .compiler import Compiler
from .errors import BlueprintError, LLMError, SGError
from .exporter import export_all
from .llm import LLMClient, probe
from .models import Blueprint, Library
from .registry import ELEMENT_TYPES, as_public_list
from .renderer import Renderer, template_episode
from .validator import validate_episode, validate_script

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MAX_REPAIR = 2


# ------------------------------------------------------------------ 资源
def list_styles() -> list[dict]:
    out = []
    for f in sorted((DATA / "styles").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"id": d.get("id", f.stem), "name": d.get("name", f.stem),
                    "market": d.get("market", ""), "description": d.get("description", ""),
                    "order": d.get("order", 99),
                    "segments": [s["name"] for s in d.get("segments", [])]})
    out.sort(key=lambda x: (x["order"], x["id"]))
    return out


def load_style(style_id: str) -> dict:
    f = DATA / "styles" / f"{style_id}.json"
    if not f.is_file():
        for cand in (DATA / "styles").glob("*.json"):
            d = json.loads(cand.read_text(encoding="utf-8"))
            if d.get("id") == style_id:
                return d
        raise BlueprintError(f"风格包不存在: {style_id}", hint="检查 data/styles 下的 json",
                             where="meta.style")
    return json.loads(f.read_text(encoding="utf-8"))


def list_libraries() -> list[str]:
    out: set[str] = set()
    for p in (DATA / "elements").glob("*.json"):
        n = p.name
        if n.endswith(".elements.json"):
            out.add(n[: -len(".elements.json")])
        elif n.endswith(".elements.sample.json"):
            out.add(n[: -len(".elements.sample.json")])
    return sorted(out)


def catalog(market: str = "MX") -> dict:
    """给前端：注册表 + 要素清单 + 风格 + 模型状态。"""
    lib = Library.load(market)
    by_type: dict[str, list[dict]] = {}
    for el in lib.all():
        by_type.setdefault(el.type, []).append({
            "id": el.id, "name": el.name, "name_local": el.name_local,
            "tags": el.tags, "s_level": el.s_level, "layer": el.layer,
            "attrs": el.attrs, "source": el.source,
        })
    for v in by_type.values():
        v.sort(key=lambda x: x["id"])
    return {
        "market": market,
        "markets": list_libraries(),
        "slots": as_public_list(),
        "types": {k: {"code": s.code, "cn": s.cn, "layer": s.layer, "role": s.role} for k, s in ELEMENT_TYPES.items()},
        "elements": by_type,
        "counts": lib.counts(),
        "market_base": lib.market_base,
        "styles": list_styles(),
        "llm": probe(),
    }


# ------------------------------------------------------------------ 主流程
def _emit(progress: Callable | None, stage: str, pct: int, message: str, **extra) -> None:
    if progress:
        try:
            progress({"stage": stage, "pct": pct, "message": message, **extra})
        except Exception:  # noqa: BLE001  进度回调不能影响主流程
            pass


def run(
    blueprint: dict,
    *,
    mode: str = "auto",              # auto | llm | template
    episodes: list[int] | None = None,
    workers: int = 4,
    allow_degrade: bool = True,
    max_repair: int = MAX_REPAIR,
    progress: Callable | None = None,
    outdir: str | Path | None = None,
    export: bool = False,
) -> dict:
    t0 = time.time()
    _emit(progress, "input", 2, "载入要素库")

    bp = Blueprint.from_dict(blueprint)
    market = bp.meta.get("market", "MX")
    lib = Library.load(market)
    style = load_style(bp.meta.get("style", "mx-telenovela"))
    bp.apply_style_defaults(style)

    # ---- INPUT ----
    in_issues = bp.validate(lib)
    hard = [i for i in in_issues if i["level"] == "error"]
    if hard:
        raise BlueprintError(
            "蓝图校验未通过：" + "；".join(i["message"] for i in hard[:3]),
            hint=hard[0].get("hint", ""), where=hard[0].get("where", ""),
            issues=in_issues,
        )
    bp, filled = bp.autofill(lib)
    if filled:
        in_issues = bp.validate(lib)     # 补齐后重算，避免把「未选」当成问题报出来
        _emit(progress, "input", 5, "已自动补齐：" + "、".join(f["cn"] for f in filled))

    # ---- COMPILE ----
    _emit(progress, "compile", 8, "编译分集组装卡")
    comp = Compiler(lib, bp, style).compile()
    cards = comp.cards
    want = sorted(set(episodes)) if episodes else [c["episode"] for c in cards]
    cards = [c for c in cards if c["episode"] in want]

    # ---- RENDER + VALIDATE ----
    client = LLMClient()
    renderer = Renderer(style, lib, comp.plan, comp.ledger, comp.resolved, client=client, mode=mode)
    eff_mode = renderer.effective_mode
    total = len(cards)
    _emit(progress, "render", 10, f"开始生成 {total} 集（{eff_mode} 模式）",
          total=total, mode=eff_mode)

    results: dict[int, dict] = {}
    failures: list[dict] = []
    lock = threading.Lock()
    done = 0

    def one(card: dict, prev: list[dict]) -> tuple[dict | None, list[dict], str]:
        """单集：生成 -> 校验 -> 修复，失败可降级。"""
        issues: list[dict] = []
        mode_used = eff_mode
        ep: dict | None = None
        try:
            ep = renderer.render(card, prev)
        except SGError as e:
            issues.append({"level": "error", "code": e.code, "message": e.message,
                           "hint": e.hint, "where": f"ep{card['episode']}"})
        except Exception as e:  # noqa: BLE001
            issues.append({"level": "error", "code": "GEN_UNKNOWN", "message": str(e)[:200],
                           "hint": "", "where": f"ep{card['episode']}"})

        if ep is not None:
            ep_issues = validate_episode(ep, card, lib, comp.ledger, style)
            hard = [i for i in ep_issues if i["level"] == "error"]
            tries = 0
            while hard and tries < max_repair and renderer.effective_mode == "llm":
                tries += 1
                try:
                    ep = renderer.repair(card, prev, ep, ep_issues)
                except SGError:
                    break
                ep_issues = validate_episode(ep, card, lib, comp.ledger, style)
                hard = [i for i in ep_issues if i["level"] == "error"]
            issues = ep_issues
            if hard and allow_degrade:
                # 降级阶梯最后一级：模板稿（结构必然合规）
                ep = template_episode(card, comp.ledger, style)
                ep["generator"] = {"mode": "template", "degraded": True,
                                   "note": f"LLM 稿校验未过（{len(hard)} 项），已降级为模板稿",
                                   "failed_issues": [i["message"] for i in hard[:5]]}
                mode_used = "template"
                issues = validate_episode(ep, card, lib, comp.ledger, style)
                issues.append({"level": "warn", "code": "GEN_DEGRADED",
                               "message": "本集由模板降级生成，文采待润色", "where": f"ep{card['episode']}", "hint": ""})

        if ep is None:
            raise LLMError("本集无输出", hint="重跑该集或切到 template 模式",
                           where=f"ep{card['episode']}")
        ep["_checks"] = {
            "passed": not [i for i in issues if i["level"] == "error"],
            "errors": sum(1 for i in issues if i["level"] == "error"),
            "warnings": sum(1 for i in issues if i["level"] == "warn"),
            "issues": issues,
        }
        ep["beat"]["level"] = card["beat"]["escalation_level"]
        ep["arc_segment"] = card["arc_segment"]
        ep["scene_refs"] = card["scene_refs"]
        ep["props"] = card["props"]
        return ep, issues, mode_used

    # 串行上下文依赖：前情提要要用上一集结果，所以按窗口批处理而不是完全并行
    window = max(1, workers)
    content_mode = eff_mode == "llm"
    for start in range(0, len(cards), window):
        batch = cards[start:start + window]
        if content_mode:
            # 同批并行：每集的前情取"上一批"的最后两集，保证上下文稳定
            prev = [results[e] for e in sorted(results)[-2:]]
            with ThreadPoolExecutor(max_workers=window) as pool:
                futs = {pool.submit(one, c, prev): c for c in batch}
                for fut in as_completed(futs):
                    card = futs[fut]
                    try:
                        ep, issues, _ = fut.result()
                        results[card["episode"]] = ep
                    except SGError as e:
                        failures.append({"episode": card["episode"], **e.to_dict()})
                    except Exception as e:  # noqa: BLE001
                        failures.append({"episode": card["episode"], "code": "GEN_UNKNOWN", "message": str(e)[:200]})
                    with lock:
                        done += 1
                        pct = 10 + int(75 * done / max(1, total))
                        _emit(progress, "render", pct, f"已完成 {done}/{total} 集", done=done, total=total)
        else:
            for card in batch:
                prev = [results[e] for e in sorted(results)[-2:]]
                try:
                    ep, issues, _ = one(card, prev)
                    results[card["episode"]] = ep
                except SGError as e:
                    failures.append({"episode": card["episode"], **e.to_dict()})
                except Exception as e:  # noqa: BLE001
                    failures.append({"episode": card["episode"], "code": "GEN_UNKNOWN", "message": str(e)[:200]})
                done += 1
                pct = 10 + int(75 * done / max(1, total))
                _emit(progress, "render", pct, f"已完成 {done}/{total} 集", done=done, total=total)

    # ---- 组装 Script ----
    _emit(progress, "validate", 88, "全局校验与引用核查")
    episodes_out = [results[k] for k in sorted(results)]
    element_index = {c.id: {"name": c.name, "type": c.type, "type_cn": c.type_cn,
                            "s_level": c.s_level, "tags": c.tags}
                     for c in lib.all()}
    script = {
        "schema_version": "1.0",
        "meta": {
            **bp.meta,
            "elements": bp.elements,
            "constraints": bp.constraints,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": eff_mode,
            "episodes_generated": len(episodes_out),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "plan": comp.plan,
        "ledger": comp.ledger,
        "characters": list(comp.ledger.get("characters", {}).values()),
        "episodes": episodes_out,
        "element_index": element_index,
        "library_counts": {k: [1] * v for k, v in lib.counts().items()},
        "input_issues": in_issues,
        "autofilled": filled,
        "failures": failures,
        "llm": client.cfg.describe(),
    }
    script["checks"] = validate_script(script, cards, lib, comp.ledger)

    _emit(progress, "export", 95, "导出文件")
    paths = {}
    if export:
        try:
            paths = export_all(script, outdir or (ROOT / "out"))
            script["files"] = paths
        except OSError as e:
            script.setdefault("failures", []).append({"episode": 0, "code": "SYS_EXPORT", "message": str(e)})

    _emit(progress, "done", 100,
          f"完成：{len(episodes_out)} 集，{script['checks']['errors']} 个阻断项",
          passed=script["checks"]["passed"])
    return script


# ------------------------------------------------------------------ 预览
def preview_prompt(blueprint: dict, episode: int = 1) -> dict:
    """把「某一集实际发给模型的 prompt」原样吐出来。UI 用来解释输入格式。"""
    bp = Blueprint.from_dict(blueprint)
    lib = Library.load(bp.meta.get("market", "MX"))
    in_issues = bp.validate(lib)
    if [i for i in in_issues if i["level"] == "error"]:
        raise BlueprintError("蓝图校验未通过", hint=in_issues[0].get("hint", ""),
                             where=in_issues[0].get("where", ""), issues=in_issues)
    bp, filled = bp.autofill(lib)
    style = load_style(bp.meta.get("style", "mx-telenovela"))
    bp.apply_style_defaults(style)
    comp = Compiler(lib, bp, style).compile()
    card = next((c for c in comp.cards if c["episode"] == episode), comp.cards[0])
    r = Renderer(style, lib, comp.plan, comp.ledger, comp.resolved, mode="template")
    system, user = r.builder.build(card, [])
    return {"episode": card["episode"], "system": system, "user": user,
            "card": card, "autofilled": filled, "issues": in_issues,
            "est_tokens": len(system) // 2 + len(user) // 2}


def estimate(blueprint: dict) -> dict:
    """成本预估：集数 / 调用次数 / 输入输出 token 量级。"""
    bp = Blueprint.from_dict(blueprint)
    lib = Library.load(bp.meta.get("market", "MX"))
    bp, _ = bp.autofill(lib)
    style = load_style(bp.meta.get("style", "mx-telenovela"))
    bp.apply_style_defaults(style)
    comp = Compiler(lib, bp, style).compile()
    pv = preview_prompt(bp.to_dict(), 1)
    n = int(bp.meta["episode_count"])
    in_tok = pv["est_tokens"]
    secs = int(bp.meta.get("episode_seconds") or 60)
    return {
        "episodes": n,
        "episode_seconds": secs,
        "calls_min": n,
        "calls_max": n * (1 + MAX_REPAIR),
        "input_tokens": in_tok * n,
        "output_tokens": (secs * 12 + 200) * n,
        "cards": len(comp.cards),
        "llm": probe(),
        "segments": comp.plan.get("segments"),
        "beat_ladder": comp.plan.get("beat_ladder"),
        "cost_summary": comp.plan.get("cost_summary"),
    }
