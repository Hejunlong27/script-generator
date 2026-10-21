# -*- coding: utf-8 -*-
"""三层校验。

L1 结构：键名/类型/枚举/必填 —— 机器可判，不通过就是错
L2 引用：所有 ID 必须在要素库里，人物名必须来自账本 —— 这是「要素引用一致」的执行点
L3 规则：红线 / 不可做 / 语域 / 爽点刻度 —— 部分靠关键词启发式，标 warn 不标 error

设计立场：**错误必须可定位、可回喂**。
每条 issue 都带 where（JSON 路径）和 hint（怎么改），可以直接拼进修复 prompt。
"""

from __future__ import annotations

import re
from typing import Any

from .models import Library

RANGES_ORDER = ("0-3s", "3-12s", "12-40s", "40-54s", "54-60s")   # 仅在 style 未定义时间轴时兜底
KINDS_ORDER = ("open_hook", "setup", "escalation", "reversal", "cliff")
REGISTERS = ("敬语", "中性", "熟稔", "粗鲁", "宗教语域")
ID_PATTERN = re.compile(r"\bMX-[A-Z]{3}-[A-Z]?\d{2,3}\b")

# 「不可做」里抽关键词时的停用片段
_STOP = ("不", "不要", "不得", "不许", "不能", "永远", "从不", "无法", "自己", "任何", "别人", "当众")


def timeline_spec(style: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """时间轴以风格包为准 —— 60 秒和 90 秒的段长不同，校验器不能写死。

    这是之前埋的一个坑：段长硬编码在 validator 里，一改单集时长，所有集都会报
    「range 应为 15-50s」，而模型输出的其实是对的。
    """
    tl = (style or {}).get("timeline") or []
    if len(tl) == 5 and all("range" in t and "kind" in t for t in tl):
        return tuple(t["range"] for t in tl), tuple(t["kind"] for t in tl)
    return RANGES_ORDER, KINDS_ORDER


def _issue(level: str, code: str, message: str, where: str = "", hint: str = "") -> dict:
    return {"level": level, "code": code, "message": message, "where": where, "hint": hint}


def _s(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


# ------------------------------------------------------------------ 单集
def validate_episode(ep: dict, card: dict, lib: Library, ledger: dict,
                     style: dict) -> list[dict]:
    issues: list[dict] = []
    if not isinstance(ep, dict):
        return [_issue("error", "VAL_SHAPE", "单集输出不是 JSON 对象", "$", "要求模型只输出 JSON 对象")]

    # ---------- L1 结构 ----------
    if ep.get("episode") != card["episode"]:
        issues.append(_issue("error", "VAL_SHAPE", f"episode 应为 {card['episode']}，实际 {ep.get('episode')}",
                             "$.episode", "照抄组装卡集数"))
    if not _s(ep.get("title")).strip():
        issues.append(_issue("error", "VAL_SHAPE", "缺少本集标题", "$.title", "补一个 ≤12 字的中文标题"))
    if not _s(ep.get("logline")).strip():
        issues.append(_issue("warn", "VAL_SHAPE", "缺少 logline（一句话概括）", "$.logline", "补一句话"))

    segs = ep.get("segments")
    ranges, kinds = timeline_spec(style)
    if not isinstance(segs, list) or len(segs) != len(ranges):
        issues.append(_issue("error", "VAL_SEGMENTS",
                             f"segments 必须正好 {len(ranges)} 段，实际 "
                             f"{len(segs) if isinstance(segs, list) else '非数组'}",
                             "$.segments", "严格按 " + " / ".join(ranges) + " 分段"))
    else:
        for i, (sg, rng, kind) in enumerate(zip(segs, ranges, kinds)):
            if _s(sg.get("range")) != rng:
                issues.append(_issue("error", "VAL_SEGMENTS",
                                     f"第 {i + 1} 段 range 应为 {rng}，实际 {sg.get('range')}",
                                     f"$.segments[{i}].range", "range 必须照抄"))
            if _s(sg.get("kind")) != kind:
                issues.append(_issue("error", "VAL_SEGMENTS",
                                     f"第 {i + 1} 段 kind 应为 {kind}，实际 {sg.get('kind')}",
                                     f"$.segments[{i}].kind", "kind 必须照抄"))
            txt = _s(sg.get("text")).strip()
            if len(txt) < 4:
                issues.append(_issue("warn", "VAL_SEGMENTS", f"第 {i + 1} 段内容过短（{len(txt)} 字）",
                                     f"$.segments[{i}].text", "用一句画面或台词写实"))

    scenes = ep.get("scenes")
    allowed_scenes = set(card.get("scene_refs") or [])
    allowed_cast = {c["chr_ref"] for c in card.get("cast", [])}
    allowed_props = set(card.get("props") or [])

    if not isinstance(scenes, list) or not scenes:
        issues.append(_issue("error", "VAL_SHAPE", "scenes 不能为空", "$.scenes", "至少 1 场"))
    else:
        if len(scenes) > int((style.get("max_scene") or 2)):
            issues.append(_issue("warn", "VAL_COST", f"本集 {len(scenes)} 场，换景成本偏高",
                                 "$.scenes", "短剧单集 1–2 场"))
        for i, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                issues.append(_issue("error", "VAL_SHAPE", f"scenes[{i}] 不是对象", f"$.scenes[{i}]"))
                continue
            sref = _s(sc.get("scene_ref"))
            if allowed_scenes and sref not in allowed_scenes:
                issues.append(_issue("error", "VAL_REF_SCENE",
                                     f"场景 {sref or '(空)'} 不在本集可选列表 {sorted(allowed_scenes)}",
                                     f"$.scenes[{i}].scene_ref", "只能照抄给定的场景 ID"))
            cast_refs = sc.get("cast_refs") or []
            if not isinstance(cast_refs, list):
                issues.append(_issue("error", "VAL_SHAPE", f"scenes[{i}].cast_refs 必须是数组", f"$.scenes[{i}].cast_refs"))
                cast_refs = []
            for c in cast_refs:
                if c not in allowed_cast:
                    issues.append(_issue("error", "VAL_REF_CAST",
                                         f"人物 {c} 不在本集出场名单 {sorted(allowed_cast)}",
                                         f"$.scenes[{i}].cast_refs", "不得新增角色"))
            for p in (sc.get("props") or []):
                if p and p not in allowed_props:
                    issues.append(_issue("warn", "VAL_REF_PROP",
                                         f"道具 {p} 不在本集指定道具 {sorted(allowed_props)}",
                                         f"$.scenes[{i}].props", "只能用指定道具"))
            lines = sc.get("lines")
            if not isinstance(lines, list) or not lines:
                issues.append(_issue("warn", "VAL_SHAPE", f"scenes[{i}] 没有台词",
                                     f"$.scenes[{i}].lines", "至少给 2 句对话"))
                continue
            for j, ln in enumerate(lines):
                sp = _s(ln.get("speaker_ref"))
                if sp not in cast_refs:
                    issues.append(_issue("error", "VAL_REF_SPEAKER",
                                         f"台词说话人 {sp or '(空)'} 不在本场 cast_refs {cast_refs}",
                                         f"$.scenes[{i}].lines[{j}].speaker_ref", "说话人必须是本场在场者"))
                expect_name = (ledger.get("characters", {}).get(sp) or {}).get("name_zh", "")
                got_name = _s(ln.get("speaker"))
                if expect_name and got_name and got_name != expect_name:
                    issues.append(_issue("error", "VAL_NAME_DRIFT",
                                         f"人物名不一致：{sp} 应为「{expect_name}」，实际「{got_name}」",
                                         f"$.scenes[{i}].lines[{j}].speaker", "名字只能来自人物账本"))
                if not _s(ln.get("text")).strip():
                    issues.append(_issue("error", "VAL_SHAPE", f"台词为空",
                                         f"$.scenes[{i}].lines[{j}].text", "补台词"))
                reg = _s(ln.get("register"))
                if reg and reg not in REGISTERS:
                    issues.append(_issue("warn", "VAL_REGISTER", f"语域 {reg} 不在枚举内",
                                         f"$.scenes[{i}].lines[{j}].register", f"用 {REGISTERS} 之一"))

    # 爽点 / 钩子引用
    beat = ep.get("beat") or {}
    if _s(beat.get("ref")) != _s(card["beat"]["beat_ref"]):
        issues.append(_issue("error", "VAL_REF_BEAT",
                             f"爽点引用不符：应为 {card['beat']['beat_ref']}，实际 {beat.get('ref') or '(空)'}",
                             "$.beat.ref", "不得更换本集爽点"))
    if not _s(beat.get("info_gap")).strip():
        issues.append(_issue("warn", "VAL_SHAPE", "缺少信息差说明", "$.beat.info_gap", "写清观众知道什么、谁不知道"))
    hook = ep.get("hook") or {}
    if _s(hook.get("ref")) != _s(card["hook"]["cliff_ref"]):
        issues.append(_issue("error", "VAL_REF_HOOK",
                             f"集尾钩子引用不符：应为 {card['hook']['cliff_ref']}，实际 {hook.get('ref') or '(空)'}",
                             "$.hook.ref", "不得更换本集钩子"))

    # ---------- L2 引用一致性（全局扫 ID） ----------
    blob = _flatten_text(ep)
    for m in set(ID_PATTERN.findall(blob)):
        if not lib.has(m):
            issues.append(_issue("error", "VAL_REF_UNKNOWN", f"出现要素库里不存在的 ID: {m}",
                                 "$", "不得自造 ID；只引用提示里给出的 ID"))
    for cid, info in (ledger.get("characters") or {}).items():
        if info["name_es"] and re.search(rf"\b{re.escape(info['name_es'])}\b", blob):
            pass  # 西语名允许出现在台词里

    # ---------- L3 规则 ----------
    low = blob
    for rl in lib.red_lines():
        key = re.sub(r"（.*?）|\(.*?\)", "", rl.get("name", "")).strip()
        if key and len(key) >= 2 and key in low:
            issues.append(_issue("error", "VAL_REDLINE", f"命中红线：{rl.get('name')}",
                                 "$", rl.get("note", "改写或规避")))
    for banned in ("海洛因", "可卡因", "贩毒集团"):
        if banned in low:
            issues.append(_issue("error", "VAL_REDLINE", f"命中平台拒审词：{banned}", "$", "改用暗示或删除"))

    issues.extend(_never_do_check(ep, card, ledger))
    return issues


def _never_do_check(ep: dict, card: dict, ledger: dict) -> list[dict]:
    """启发式：把「不可做」里的关键词拿去扫动作描述。只报警不阻断（会误判）。"""
    out: list[dict] = []
    action_text = " ".join(
        _s(sc.get("action")) for sc in (ep.get("scenes") or []) if isinstance(sc, dict)
    )
    for c in card.get("cast", []):
        if c["presence"] != "在场":
            continue
        info = ledger.get("characters", {}).get(c["chr_ref"]) or {}
        for rule in (info.get("never_do") or []):
            if not isinstance(rule, str):
                continue
            kws = [w for w in re.split(r"[，。、；,;：:\s]+", rule) if 2 <= len(w) <= 6]
            for kw in kws:
                core = kw
                for st in _STOP:
                    core = core.replace(st, "")
                core = core.strip("，。、的")
                if len(core) >= 2 and core in action_text:
                    out.append(_issue("warn", "VAL_CHARACTER",
                                      f"{info.get('name_zh', c['chr_ref'])} 的动作可能违反不可做清单：{rule}",
                                      "$.scenes[].action", "改成符合人设的表达（不哭/不失态等）"))
                    break
    return out


def _flatten_text(obj: Any) -> str:
    buf: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            buf.append(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("generator",):
                    continue
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return " ".join(buf)


# ------------------------------------------------------------------ 全局
def validate_script(script: dict, cards: list[dict], lib: Library, ledger: dict) -> dict:
    issues: list[dict] = []
    eps = script.get("episodes") or []
    want = {c["episode"] for c in cards}
    got = {e.get("episode") for e in eps if isinstance(e, dict)}
    missing = sorted(want - got)
    if missing:
        issues.append(_issue("error", "VAL_MISSING", f"缺少 {len(missing)} 集：{missing[:10]}", "$.episodes",
                             "重跑缺失集"))
    # 同名不同人 / 同人不同名
    name2id: dict[str, str] = {}
    for cid, info in (ledger.get("characters") or {}).items():
        nm = info.get("name_zh")
        if nm in name2id and name2id[nm] != cid:
            issues.append(_issue("error", "VAL_NAME_DRIFT", f"两个角色共用同一个名字「{nm}」", "$.characters",
                                 "换名字池，避免观众混淆"))
        name2id[nm] = cid
    # 引用覆盖
    ids = set()
    for e in eps:
        if isinstance(e, dict):
            ids |= set(ID_PATTERN.findall(_flatten_text({k: v for k, v in e.items() if k != "generator"})))
    unknown = sorted(i for i in ids if not lib.has(i))
    if unknown:
        issues.append(_issue("error", "VAL_REF_UNKNOWN", f"全剧出现未知 ID: {unknown[:8]}", "$", "清理或补入要素库"))

    errors = [i for i in issues if i["level"] == "error"]
    return {
        "passed": not errors,
        "errors": len(errors),
        "warnings": sum(1 for i in issues if i["level"] == "warn"),
        "issues": issues,
    }
