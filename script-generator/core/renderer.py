# -*- coding: utf-8 -*-
"""渲染层：组装卡 -> prompt -> 单集剧本。

两条路径，输出**同一套 JSON 结构**：
  llm      —— 真调模型（质量高）
  template —— 模板填充 + 素材原文（无 key / 调用失败时的降级，保证永远能出稿）

降级不是"出错就算了"，是设计的一部分：没有 API key 也能走完全流程、验证结构、
交付能用的初稿，再决定值不值得上模型。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLMClient
from .models import Library


# ---------------------------------------------------------------- prompt 拼装
class PromptBuilder:
    def __init__(self, style: dict, lib: Library, plan: dict, ledger: dict,
                 resolved: dict | None = None):
        self.style = style
        self.lib = lib
        self.plan = plan
        self.ledger = ledger
        self.resolved = resolved or {}

    # ---- 片段 ----
    def _characters(self, card: dict) -> str:
        ids = [c["chr_ref"] for c in card["cast"]]
        lines = []
        for cid in ids:
            c = self.ledger["characters"].get(cid)
            if not c:
                continue
            presence = next((x["presence"] for x in card["cast"] if x["chr_ref"] == cid), "在场")
            never = c.get("never_do") or []
            if isinstance(never, str):
                never = [never]
            never_txt = "；".join(f"{i + 1}) {x}" for i, x in enumerate(never)) or "（未定义）"
            lines.append(
                f"- {c['full_zh']}（{c['full_es']}）｜ID={cid}｜{c['role']}｜本集处理：{presence}\n"
                f"  身份：{c['identity']}\n"
                f"  表面人格：{c.get('surface') or '—'}\n"
                f"  真实缺陷：{c.get('flaw') or '—'}\n"
                f"  秘密：{c.get('secret') or '—'}\n"
                f"  说话方式：{c.get('speech') or '按身份调整语域'}\n"
                f"  不可做（违反即人设崩）：{never_txt}"
            )
        return "\n".join(lines)

    def _scenes(self, card: dict) -> str:
        out = []
        for sid in card["scene_refs"]:
            el = self.lib.get(sid) if self.lib.has(sid) else None
            if not el:
                continue
            out.append(
                f"- [{sid}] {el.name}" + (f"（{el.name_local}）" if el.name_local else "")
                + f"：光线 {el.get('attrs.light') or '—'}；可拍 {el.get('attrs.cast') or '—'} 人；"
                + f"道具 {'、'.join(el.get('attrs.props') or []) or '—'}"
            )
        return "\n".join(out) or "- （无，可自拟一个室内场景但必须是 1–3 人）"

    def _props(self, card: dict) -> str:
        out = []
        for pid in card["props"]:
            if not self.lib.has(pid):
                continue
            el = self.lib.get(pid)
            sig = el.get("attrs.signal") or ""
            out.append(f"- [{pid}] {el.name}" + (f"（{el.get('attrs.local') or el.name_local}）" if (el.get('attrs.local') or el.name_local) else "")
                       + (f"：{sig}" if sig else ""))
        return "\n".join(out) or "- （无指定道具；若需要，只从场景自带道具里取）"

    def _timeline(self, card: dict) -> str:
        tl = card["timeline"]
        rules = {t["kind"]: t.get("rule", "") for t in (self.style.get("timeline") or [])}
        order = ("open_hook", "setup", "escalation", "reversal", "cliff")
        lines = []
        for k in order:
            slot = tl.get(k) or {}
            ref = slot.get("hook_ref") or slot.get("beat_ref") or slot.get("conflict_ref") or slot.get("scene_ref") or ""
            ref_txt = f"［{ref}］" if ref else ""
            extra = ""
            if slot.get("scene_ref") and k == "escalation":
                extra = f"（换景到 {self._scene_name(slot['scene_ref'])}）"
            lines.append(
                f"{slot.get('range', '')}\t{k}\t{ref_txt}{slot.get('content', '')}{extra}"
                + (f"\n\t\t要求：{rules[k]}" if rules.get(k) else "")
            )
        return "\n".join(lines)

    def _scene_name(self, sid: str) -> str:
        if self.lib.has(sid):
            el = self.lib.get(sid)
            return f"{el.name}"
        return sid

    def _recap(self, prev: list[dict]) -> str:
        if not prev:
            return "本集是第一集，无前情。"
        lines = []
        for ep in prev[-2:]:
            lines.append(f"第 {ep.get('episode')} 集《{ep.get('title', '')}》：{ep.get('logline', '')}"
                         f"｜状态：{ep.get('state_after', '')}")
        return "\n".join(lines)

    # ---- 主入口 ----
    def build(self, card: dict, prev_episodes: list[dict]) -> tuple[str, str]:
        st = self.style
        core = self._element_text("core")
        arc = self._element_text("arc")
        city = self._element_text("city")
        ending = self._element_text("ending")
        supports = self._element_list("support")
        conflicts = self._element_list("conflict")

        mapping = {
            "title": plan_title(self.plan),
            "package": self._package_text(),
            "episode_count": self.plan.get("episode_count"),
            "episode_seconds": self.plan.get("episode_seconds") or 60,
            "core": core,
            "arc": arc,
            "city": city,
            "supports": supports,
            "conflicts": conflicts,
            "ending": ending,
            "episode": card["episode"],
            "seg_index": card["segment_index"],
            "segment": card["arc_segment"],
            "seg_range": card["segment_range"],
            "seg_goal": next((s.get("goal") for s in self.plan.get("segments", [])
                              if s["name"] == card["arc_segment"]), ""),
            "episode_task": card["episode_task"],
            "timeline": self._timeline(card),
            "characters": self._characters(card),
            "scenes": self._scenes(card),
            "props": self._props(card),
            "register": card["dialogue_notes"]["register"],
            "address_shift": card["dialogue_notes"]["address_shift"],
            "recap": self._recap(prev_episodes),
            "output_skeleton": json.dumps(st.get("output_skeleton") or {}, ensure_ascii=False, indent=1),
        }
        user = st.get("episode_prompt") or ""
        for k, v in mapping.items():
            user = user.replace("{{" + k + "}}", str(v) if v is not None else "")
        user = re.sub(r"\{\{\w+\}\}", "", user)
        return st.get("system_prompt", ""), user

    def _package_text(self) -> str:
        """题材包的卖点与核心钩子——给模型一个「这部剧的卖相」锚点。"""
        pkg = self.resolved.get("package")
        if not pkg:
            return "—"
        a = pkg.attrs
        bits = [f"{pkg.name}（{' + '.join(a.get('labels') or [])}）"]
        if a.get("sellpoint"):
            bits.append(f"卖点：{a['sellpoint']}")
        if a.get("core_hook"):
            bits.append(f"核心钩子：{a['core_hook']}")
        if a.get("risk"):
            bits.append(f"注意：{a['risk']}")
        return "｜".join(bits)

    def _element_text(self, slot: str) -> str:
        from .registry import ELEMENT_TYPES
        spec = ELEMENT_TYPES.get(slot)
        el = self.resolved.get(slot)
        if not el:
            return "—"
        field = spec.text_field if spec else "name"
        return el.prompt_text(field)

    def _element_list(self, slot: str) -> str:
        from .registry import ELEMENT_TYPES
        spec = ELEMENT_TYPES.get(slot)
        els = self.resolved.get(slot) or []
        if not isinstance(els, list):
            els = [els]
        field = spec.text_field if spec else "name"
        return "\n".join(f"- {e.prompt_text(field)}" for e in els) or "—"


def plan_title(plan: dict) -> str:
    return plan.get("title") or "未命名短剧"


# ---------------------------------------------------------------- 模板降级
def episode_seconds(card: dict, default: int = 60) -> int:
    """从组装卡的时间轴末段反解单集秒数，避免再传一个参数。"""
    rng = ((card.get("timeline") or {}).get("cliff") or {}).get("range", "")
    m = re.search(r"-\s*(\d+)\s*s", str(rng))
    return int(m.group(1)) if m else default


def _register_es(style: dict, register: str) -> str:
    m = (style.get("degrade_templates") or {}).get("line_register") or {}
    return m.get(register, "usted")


_LOG_TAIL = {
    1: "第一次穿上不属于自己的东西",
    2: "开始发现有人在替自己安排一切",
    3: "身边的人各知道一半，没有一个人知道全部",
    4: "被当众推回原点",
    5: "终于不再要那个位置",
}


def _logline(card: dict, seed: dict, an: dict, bn: dict) -> str:
    tail = _LOG_TAIL.get(card.get("segment_index", 1), "局势没有变，只是更紧了")
    head = seed.get("escalation") or "冲突升级"
    return f"{head.rstrip('。')}——{an['name_zh']}{tail}，{bn['name_zh']}看在眼里。"


def template_episode(card: dict, ledger: dict, style: dict) -> dict:
    """无 LLM 时的确定性出稿。结构、引用、语域全部合规，只是文采有限。"""
    tl = card["timeline"]
    seed = card["degrade_seed"]
    chars = ledger["characters"]
    on = [c["chr_ref"] for c in card["cast"] if c["presence"] == "在场"]
    a = on[0] if on else next(iter(chars))
    b = on[1] if len(on) > 1 else (list(chars)[1] if len(chars) > 1 else a)
    an, bn = chars[a], chars[b]
    es = _register_es(style, card["dialogue_notes"]["register"])
    scene1 = card["scene_refs"][0] if card["scene_refs"] else ""
    scene2 = card["scene_refs"][1] if len(card["scene_refs"]) > 1 else scene1

    polite = es == "usted"
    k = card["episode"]
    # 词汇池按集号轮换，避免 60 集读起来一模一样；语域始终正确
    questions = [
        ("¿Y esto qué es?", "这是什么？"),
        ("¿Quién le dijo que podía entrar?", "谁准您进来的？"),
        ("¿Cuánto le pagaron por esto?", "这件事他们给了您多少钱？"),
        ("¿De dónde sacó esto?", "这东西您从哪儿弄来的？"),
        ("¿Ya lo sabía? Dígame la verdad.", "您早就知道了？说实话。"),
        ("¿Por qué no me lo dijo antes?", "您为什么之前不告诉我？"),
    ]
    answers = [
        ("No tengo que explicarle nada.", "我不需要向您解释什么。"),
        ("Usted ya lo sabía. Nombre lo que falta.", "您早就知道了。缺的那部分，您说出来。"),
        ("Aquí nadie le va a creer a usted.", "在这里没人会信您。"),
        ("Se lo dije. No me escuchó.", "我说过了。是您没听。"),
        ("Eso no es asunto suyo.", "那不是您该管的事。"),
        ("Pregúntele a la familia. Ellos firmaron.", "去问这个家。是他们签的字。"),
        ("Usted no estuvo aquí esa noche. Yo sí.", "那天晚上您不在。我在。"),
    ]
    pushes = [
        ("Entonces no hay nada que hablar.", "那就没什么可谈的了。"),
        ("Si sale de esta casa, no vuelve.", "您要是走出这扇门，就别回来。"),
        ("Mañana lo arreglamos. Hoy no.", "明天再说。今天不行。"),
        ("Usted no es de los nuestros. Nunca lo fue.", "您不是我们家的人。从来都不是。"),
        ("No me mire así. Yo también perdí algo.", "别这样看我。我也丢了东西。"),
        ("Firme aquí y se acaba.", "签在这儿，就结束了。"),
    ]
    # 用「集号 + 本集爽点/钩子编号」做签名，让词汇序列不呈现简单周期
    sig = k * 37
    for key in (card["beat"]["beat_ref"], card["hook"]["cliff_ref"]):
        try:
            sig += int(str(key).split("-")[-1]) * 11
        except ValueError:
            pass
    q = questions[sig % len(questions)]
    a1 = answers[(sig // 3 + k) % len(answers)]
    p1 = pushes[(sig // 5 + k) % len(pushes)]
    a2 = answers[(sig // 7 + k * 2) % len(answers)]
    p2 = pushes[(sig // 11 + k * 3) % len(pushes)]

    def line(speaker: dict, text_es: str, gloss: str, kind: str = "dialogue") -> dict:
        return {"speaker_ref": speaker["element"], "speaker": speaker["name_zh"],
                "text": text_es, "gloss": gloss,
                "register": card["dialogue_notes"]["register"], "kind": kind}

    def stage(text: str) -> dict:
        """无台词调度行，仍挂在具体人物上，方便下游对齐。"""
        return {"speaker_ref": a, "speaker": an["name_zh"], "text": f"（{text}）",
                "gloss": "", "register": card["dialogue_notes"]["register"], "kind": "action"}

    # 60 秒装不下 10 句台词。按秒数裁：短形态留 4+2，长形态留 6+4。
    short = episode_seconds(card) <= 60
    lines1 = [
        stage("她低头做手里的事，没有抬眼"),
        line(an, q[0], q[1]),
        line(bn, a1[0], a1[1]),
    ]
    if not short:
        lines1.append(stage(f"{bn['name_zh']}把东西推到桌子中间，手指按着不松"))
    lines1 += [
        line(an, "No. Dígamelo usted." if polite else "No. Dímelo tú.", "不。您来说。"),
        line(bn, p1[0], p1[1]),
    ]
    lines2 = [line(bn, a2[0], a2[1]), stage(seed["reversal"] or "她第一次没有退")]
    if not short:
        lines2 += [line(an, "Ya no.", "不了。"), line(bn, p2[0], p2[1])]

    two_scenes = bool(card["scene_refs"]) and len(card["scene_refs"]) > 1
    scene2 = card["scene_refs"][1] if two_scenes else scene1

    scenes_out = [{
        "slug": f"{card['episode']}-1",
        "scene_ref": scene1,
        "location": ledger["scenes"].get(scene1, {}).get("name", ""),
        "time": "夜",
        "cast_refs": [a, b],
        "action": (f"{seed['setup']}{an['name_zh']}在做事，{bn['name_zh']}进来。"
                   f"两个人都没有先开口。本集任务：{card['episode_task']}"),
        "lines": lines1 + ([] if two_scenes else lines2),
        "props": card["props"][:1],
        "camera": (card["camera"] or ["近景对峙"])[0],
    }]
    if two_scenes:
        scenes_out.append({
            "slug": f"{card['episode']}-2",
            "scene_ref": scene2,
            "location": ledger["scenes"].get(scene2, {}).get("name", ""),
            "time": "夜",
            "cast_refs": [a, b],
            "action": seed["reversal"],
            "lines": lines2,
            "props": card["props"][1:2],
            "camera": (card["camera"] or ["空镜情绪"])[-1],
        })

    ep = {
        "episode": card["episode"],
        "title": card["title_hint"],
        "logline": _logline(card, seed, an, bn),
        "state_after": f"{card['arc_segment']}：{seed['reversal'] or '局势未变'}",
        "segments": [
            {"range": tl["open_hook"]["range"], "kind": "open_hook", "text": seed["open_hook"]},
            {"range": tl["setup"]["range"], "kind": "setup", "text": seed["setup"]},
            {"range": tl["escalation"]["range"], "kind": "escalation", "text": seed["escalation"]},
            {"range": tl["reversal"]["range"], "kind": "reversal", "text": seed["reversal"]},
            {"range": tl["cliff"]["range"], "kind": "cliff", "text": seed["cliff"]},
        ],
        "scenes": scenes_out,
        "beat": {"ref": card["beat"]["beat_ref"], "landing": seed["reversal"],
                 "info_gap": card["beat"]["info_gap"]},
        "hook": {"ref": card["hook"]["cliff_ref"], "promise": card["hook"]["promise"]},
        "compliance": {"risk_notes": []},
        "generator": {"mode": "template", "degraded": True,
                      "note": "未调用 LLM，按组装卡模板填充。结构合规，文采待润色。"},
    }
    return ep


# ---------------------------------------------------------------- 渲染器
class Renderer:
    def __init__(self, style: dict, lib: Library, plan: dict, ledger: dict,
                 resolved: dict | None = None,
                 client: LLMClient | None = None, mode: str = "auto"):
        self.style = style
        self.lib = lib
        self.plan = plan
        self.ledger = ledger
        self.client = client or LLMClient()
        self.mode = mode  # auto | llm | template
        self.builder = PromptBuilder(style, lib, plan, ledger, resolved)

    @property
    def effective_mode(self) -> str:
        if self.mode == "template":
            return "template"
        if self.mode == "llm":
            return "llm"
        return "llm" if self.client.cfg.configured else "template"

    def render(self, card: dict, prev_episodes: list[dict]) -> dict:
        mode = self.effective_mode
        if mode == "template":
            return template_episode(card, self.ledger, self.style)
        system, user = self.builder.build(card, prev_episodes)
        data = self.client.chat_json(system, user, label=f"ep{card['episode']}")
        data.setdefault("episode", card["episode"])
        data["generator"] = {"mode": "llm", "degraded": False,
                             "model": self.client.cfg.model}
        return data

    def repair(self, card: dict, prev_episodes: list[dict], draft: dict,
               issues: list[dict]) -> dict:
        """把校验问题回喂给模型，要求只改被指出的地方。"""
        if self.effective_mode != "llm":
            return draft
        system, user = self.builder.build(card, prev_episodes)
        problems = "\n".join(f"- [{i['code']}] {i['message']}" for i in issues if i.get("level") == "error")
        user = (
            user
            + "\n\n【上一稿存在的问题，必须逐条修正，其余保持不变】\n"
            + problems
            + "\n\n【上一稿】\n"
            + json.dumps(draft, ensure_ascii=False)
            + "\n\n只输出修正后的完整 JSON。"
        )
        data = self.client.chat_json(system, user, label=f"ep{card['episode']}-repair")
        data.setdefault("episode", card["episode"])
        data["generator"] = {"mode": "llm", "degraded": False, "repaired": True,
                             "model": self.client.cfg.model}
        return data
