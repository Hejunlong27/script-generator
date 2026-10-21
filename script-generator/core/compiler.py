# -*- coding: utf-8 -*-
"""编译器：Blueprint -> 每集组装卡（EpisodeCard）。

**关键设计：LLM 不参与结构决策。**
  集数分配、爽点梯度、场景成本、人物出场、钩子配对全部由这里确定性算出来。
  LLM 只负责「把填好的槽位写成人能看的戏」。

这就是「输出结构统一、要素引用一致」的保证方式——
不是靠 prompt 里写"请保持结构一致"，而是靠结构在 LLM 之前就已经定死。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BlueprintError
from .models import Blueprint, Element, Library, clusters_of
from .registry import ELEMENT_TYPES

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ------------------------------------------------------------------ 结果容器
@dataclass
class CompileResult:
    cards: list[dict] = field(default_factory=list)
    ledger: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    style: dict = field(default_factory=dict)
    resolved: dict = field(default_factory=dict)


# ------------------------------------------------------------------ 姓名分配
def _infer_gender(el: Element, names_cfg: dict) -> str:
    blob = " ".join(str(v) for v in [el.name, *el.attrs.values()])
    f = sum(blob.count(k) for k in names_cfg.get("female_hint", []))
    m = sum(blob.count(k) for k in names_cfg.get("male_hint", []))
    return "female" if f >= m else "male"


def assign_names(chars: list[Element], seed: int, market: str) -> dict[str, dict]:
    """按元素 ID 哈希确定性分配西语名 + 中文音译，保证同一蓝图每次生成名字一致。"""
    path = DATA_DIR / "names" / f"{market}.json"
    if path.is_file():
        cfg = json.loads(path.read_text(encoding="utf-8"))
    else:
        cfg = {"female": [{"es": "Ana", "zh": "安娜"}], "male": [{"es": "Juan", "zh": "胡安"}],
               "surnames": [{"es": "Ruiz", "zh": "鲁伊斯"}], "female_hint": [], "male_hint": []}

    used: set[str] = set()
    out: dict[str, dict] = {}
    for el in chars:
        gender = _infer_gender(el, cfg)
        pool = cfg.get(gender) or cfg.get("female") or [{"es": "Ana", "zh": "安娜"}]
        bob = random.Random(f"{seed}:{el.id}")
        cands = [p for p in pool if p["es"] not in used] or pool
        nm = cands[bob.randrange(len(cands))]
        used.add(nm["es"])
        sur = cfg["surnames"][bob.randrange(len(cfg["surnames"]))]
        out[el.id] = {"name_es": nm["es"], "name_zh": nm["zh"],
                      "surname_es": sur["es"], "surname_zh": sur["zh"]}
    return out


# ------------------------------------------------------------------ 集数分段
def split_segments(n: int, style: dict) -> list[dict]:
    segs = style.get("segments") or [{"name": "全剧", "ratio": 1.0, "goal": "", "event_hint": ""}]
    counts, acc = [], 0
    for s in segs:
        c = max(1, round(n * float(s.get("ratio", 1.0 / len(segs)))))
        counts.append(c)
        acc += c
    # 修正取整误差
    while acc > n and max(counts) > 1:
        i = counts.index(max(counts))
        counts[i] -= 1
        acc -= 1
    while acc < n:
        counts[counts.index(max(counts))] += 1
        acc += 1

    out, start = [], 1
    for s, c in zip(segs, counts):
        out.append({**s, "start": start, "end": start + c - 1, "count": c})
        start += c
    return out


# ------------------------------------------------------------------ 爽点梯度
def beat_ladder(n: int, seed: int) -> list[int]:
    """四铁律：相邻差≤2 / 每 10 集≥1 个≥7 / 9-10 全季≤3 / 1-3 连续≤4。

    先按「每 10 集一个峰值」铺，再做双向夹逼修复。
    纯函数，同输入必同输出 —— 保证可复现。
    """
    if n <= 0:
        return []
    levels: list[int] = []
    prev = 3
    for s in range(0, n, 10):
        blk_len = min(10, n - s)
        is_last = s + 10 >= n
        peak = 9 if is_last and blk_len >= 3 else 7 + (1 if (s // 10) % 3 == 2 else 0)
        base = max(1, min(prev, 4))
        pattern = [base - 1, base - 1, base, base + 1, base + 2, base + 3, peak - 1, peak, peak - 2, peak - 3]
        seq = [max(1, min(10, v)) for v in pattern][:blk_len]
        # 单块内部也夹逼一次
        for i in range(1, len(seq)):
            seq[i] = max(seq[i - 1] - 2, min(seq[i - 1] + 2, seq[i]))
        levels.extend(seq)
        prev = seq[-1]

    # 全局夹逼（防止块与块之间跳变）
    for _ in range(2):
        for i in range(1, n):
            levels[i] = max(levels[i - 1] - 2, min(levels[i - 1] + 2, levels[i]))
        for i in range(n - 2, -1, -1):
            levels[i] = max(levels[i + 1] - 2, min(levels[i + 1] + 2, levels[i]))

    # 铁律二：每个 10 集块至少一个 >=7（抬峰时向前铺 ramp，避免制造跳变）
    for _ in range(2):
        fixed = True
        for s in range(0, n, 10):
            blk = list(range(s, min(n, s + 10)))
            if blk and max(levels[i] for i in blk) < 7:
                idx = blk[min(len(blk) - 1, len(blk) * 7 // 10)]
                levels[idx] = 7
                for j in range(idx - 1, s - 1, -1):
                    levels[j] = max(levels[j], levels[j + 1] - 2)
                fixed = False
        if fixed:
            break
    # 抬峰后再夹一遍，确保没有新跳变
    for _ in range(2):
        for i in range(1, n):
            levels[i] = max(levels[i - 1] - 2, min(levels[i - 1] + 2, levels[i]))
        for i in range(n - 2, -1, -1):
            levels[i] = max(levels[i + 1] - 2, min(levels[i + 1] + 2, levels[i]))

    # 铁律三：9-10 全季 <=3
    hi = [i for i, v in enumerate(levels) if v >= 9]
    if len(hi) > 3:
        rng = random.Random(seed)
        keep = set(sorted(hi, key=lambda i: -levels[i])[:3])
        for i in hi:
            if i not in keep:
                levels[i] = 8

    # 铁律四：1-3 连续不得超过 4 集
    run = 0
    for i, v in enumerate(levels):
        if v <= 3:
            run += 1
            if run > 4:
                levels[i] = min(10, levels[i - 1] + 1) if i else 4
                run = 1
        else:
            run = 0

    return [max(1, min(10, v)) for v in levels]


# ------------------------------------------------------------------ 编译器
class Compiler:
    def __init__(self, lib: Library, bp: Blueprint, style: dict):
        self.lib = lib
        self.bp = bp
        self.style = style
        self.seed = int(bp.meta.get("seed", 20261020))
        self.rng = random.Random(self.seed)
        self.cons = bp.constraints
        self.issues: list[dict] = []
        self._scene_use: dict[str, int] = {}
        self._prop_use: dict[str, int] = {}
        self._prefer_scenes: set[str] = set()
        # 60 秒以内的短形态：换景和第三人上场的门槛都要抬高
        self.seconds = int(bp.meta.get("episode_seconds") or 60)
        self.short_form = self.seconds <= 60
        self.cross_scene_lvl = 7 if self.short_form else 6
        self.third_cast_lvl = 8 if self.short_form else 6
        self._beat_use: dict[str, int] = {}

    # ---------------- 主流程 ----------------
    def compile(self) -> CompileResult:
        n = int(self.bp.meta["episode_count"])
        res = self.bp.resolve(self.lib)
        core: Element | None = res.get("core")
        arc: Element | None = res.get("arc")
        hero: Element | None = res.get("character")
        supports: list[Element] = res.get("support") or []
        conflicts: list[Element] = res.get("conflict") or []
        city: Element | None = res.get("city")
        ending: Element | None = res.get("ending")
        package = res.get("package")
        if isinstance(package, Element):          # 未补齐时可能是空列表
            self._prefer_scenes = set(package.get("attrs.scenes") or [])
        open_hook: Element | None = res.get("open_hook")

        if not core or not hero:
            raise BlueprintError("缺少核心要素（core / hero），无法编译", where="elements")

        # 1) 人名账本
        chars = [hero] + supports
        name_map = assign_names(chars, self.seed, self.lib.market)
        ledger = {"characters": {}, "scenes": {}, "props": {}, "hooks": {}, "beats": {}}
        for idx, el in enumerate(chars):
            nm = name_map[el.id]
            role = "主角" if idx == 0 else ("对手" if idx == 1 else f"配角{idx - 1}")
            ledger["characters"][el.id] = {
                "name_es": nm["name_es"], "name_zh": nm["name_zh"],
                "full_zh": f"{nm['name_zh']}·{nm['surname_zh']}",
                "full_es": f"{nm['name_es']} {nm['surname_es']}",
                "role": role,
                "identity": el.get("attrs.identity") or el.name,
                "surface": el.get("attrs.surface") or "",
                "flaw": el.get("attrs.flaw") or "",
                "secret": el.get("attrs.secret") or "",
                "speech": el.get("attrs.speech") or "",
                "never_do": el.get("attrs.never_do") or [],
                "function_arc": el.get("attrs.function_arc") or [],
                "element": el.id,
            }

        # 2) 分段 + 爽点梯度
        segments = split_segments(n, self.style)
        ladder = beat_ladder(n, self.seed)
        beats = self._beat_pool()

        # 3) 场景/物品池（按约束过滤）
        scene_pool = self._scene_pool()
        prop_pool = self.lib.of_type("prop")
        hook_pool = self.lib.of_type("hook")

        # 4) 语域与称谓转折点
        register_plan = self._register_plan(segments)

        recent_scenes: list[str] = []
        recent_hooks: list[str] = []
        used_beats: list[str] = []
        crowd_used = 0
        cards: list[dict] = []

        # 5) 逐集编译
        for ep in range(1, n + 1):
            seg = self._segment_of(segments, ep)
            seg_idx = segments.index(seg) + 1
            target_lvl = ladder[ep - 1]

            beat = self._pick_beat(beats, target_lvl, used_beats, core, conflicts)
            if beat:
                used_beats.append(beat.id)
            scene, is_crowd = self._pick_scene(scene_pool, recent_scenes, beat, hero, core, crowd_used)
            if scene:
                recent_scenes.append(scene.id)
                recent_scenes = recent_scenes[-5:]
                self._scene_use[scene.id] = self._scene_use.get(scene.id, 0) + 1
                if is_crowd:
                    crowd_used += 1
            # 高潮集允许第二个场景（换景一次），其余集单场景控成本
            scene2 = None
            if target_lvl >= self.cross_scene_lvl and int(self.cons.get("max_scene_per_episode", 2)) >= 2:
                scene2, c2 = self._pick_scene(scene_pool, recent_scenes, beat, hero, core,
                                              crowd_used, exclude=[scene.id] if scene else [])
                if scene2:
                    recent_scenes = (recent_scenes + [scene2.id])[-5:]
                    self._scene_use[scene2.id] = self._scene_use.get(scene2.id, 0) + 1
                    if c2:
                        crowd_used += 1
            props = self._pick_props(scene, hero, prop_pool, ep)

            if ep == 1 and open_hook:
                oh = open_hook
            else:
                oh = self._pick_hook(hook_pool, "开场钩", recent_hooks, core)
            cliff = self._pick_hook(hook_pool, "集尾悬念", recent_hooks, core, exclude=[oh.id] if oh else [])
            if oh:
                recent_hooks = (recent_hooks + [oh.id])[-6:]
            if cliff:
                recent_hooks = (recent_hooks + [cliff.id])[-6:]

            register, address_shift = register_plan(ep, seg_idx)
            cast = self._cast(hero, supports, ep, seg_idx, target_lvl)

            card = self._build_card(
                ep=ep, n=n, seg=seg, seg_idx=seg_idx, target_lvl=target_lvl,
                beat=beat, scene=scene, scene2=scene2, props=props, open_hook=oh, cliff=cliff,
                cast=cast, register=register, address_shift=address_shift,
                core=core, arc=arc, hero=hero, supports=supports,
                conflicts=conflicts, city=city, ending=ending,
                ledger=ledger, is_crowd=is_crowd,
            )
            cards.append(card)
            for pid in card["props"]:
                if pid in self.lib._by_id:
                    pel = self.lib.get(pid)
                    ledger["props"][pid] = {"name": pel.name, "local": pel.get("attrs.local") or pel.name_local}
            if scene:
                ledger["scenes"][scene.id] = {"name": scene.name, "local": scene.name_local or scene.name}
            for h in (oh, cliff):
                if h:
                    ledger["hooks"][h.id] = {"kind": h.get("attrs.kind"), "text": h.get("attrs.text") or h.get("attrs.visual") or h.name}
            if beat:
                ledger["beats"][beat.id] = {"kind": beat.get("attrs.kind"), "lvl": beat.get("attrs.lvl"), "text": beat.get("attrs.text")}

        plan = {
            "title": self.bp.meta.get("title"),
            "episode_seconds": self.bp.meta.get("episode_seconds", 90),
            "output_language": self.bp.meta.get("output_language", "zh"),
            "seed": self.seed,
            "market": self.lib.market,
            "episode_count": n,
            "style": self.style.get("id"),
            "segments": [{"name": s["name"], "start": s["start"], "end": s["end"], "goal": s.get("goal")} for s in segments],
            "beat_ladder": ladder,
            "beat_ladder_check": self._ladder_check(ladder),
            "register_timeline": self._register_timeline(cards),
            "cost_summary": {
                "distinct_scenes": len({s for c in cards for s in c["scene_refs"]}),
                "crowd_scenes": crowd_used,
                "avg_cast": round(sum(len([x for x in c["cast"] if x["presence"] == "在场"]) for c in cards) / max(1, n), 2),
                "distinct_props": len({p for c in cards for p in c["props"]}),
            },
            "element_usage": self._usage(cards),
        }
        return CompileResult(cards=cards, ledger=ledger, plan=plan, issues=self.issues,
                             style=self.style, resolved=res)

    # ---------------- 池与打分 ----------------
    def _beat_pool(self) -> list[Element]:
        lo = int(self.cons.get("beat_level_min", 1))
        hi = int(self.cons.get("beat_level_max", 10))
        out = []
        for b in self.lib.of_type("beat"):
            lvl = b.get("attrs.lvl")
            if isinstance(lvl, int) and lo <= lvl <= hi:
                out.append(b)
        return out or self.lib.of_type("beat")

    def _scene_pool(self) -> list[Element]:
        allow = set(self.cons["allow_s_levels"])
        max_cast = int(self.cons.get("max_cast_per_scene", 3))
        out = []
        for s in self.lib.of_type("scene"):
            if s.s_level not in allow:
                continue
            cast = s.get("attrs.cast") or s.get("attrs.cast_max") or 2
            try:
                cast = int(cast)
            except (TypeError, ValueError):
                cast = 2
            if cast > max_cast:
                continue
            out.append(s)
        return out or self.lib.of_type("scene")

    def _tag_score(self, el: Element, refs: list[str]) -> int:
        return len(set(el.tags) & set(refs))

    def _pick_beat(self, beats: list[Element], target: int, used: list[str],
                   core: Element, conflicts: list[Element]) -> Element | None:
        if not beats:
            return None
        ref_tags = list(core.tags) + [t for c in conflicts for t in c.tags]

        def key(b: Element):
            lvl = b.get("attrs.lvl") or 1
            dist = abs(int(lvl) - target)
            reuse = 4 * self._beat_use.get(b.id, 0) + (6 if b.id in used[-4:] else 0)
            return (dist + reuse, -self._tag_score(b, ref_tags), b.id)

        best = sorted(beats, key=key)[0]
        self._beat_use[best.id] = self._beat_use.get(best.id, 0) + 1
        return best

    def _pick_scene(self, pool: list[Element], recent: list[str], beat: Element | None,
                    hero: Element, core: Element, crowd_used: int,
                    exclude: list[str] | None = None) -> tuple[Element | None, bool]:
        if not pool:
            return None, False
        exclude = exclude or []
        ref_tags = list(core.tags) + list(hero.tags)
        beat_kind = (beat.get("attrs.kind") if beat else "") or ""
        max_crowd = int(self.cons.get("max_crowd_scenes", 3))
        cands = [s for s in pool if s.id not in exclude] or pool

        def key(s: Element):
            cast = int(s.get("attrs.cast") or 2)
            fits = s.get("attrs.fits") or []
            crowd = cast >= 4
            crowd_pen = 8 if (crowd and crowd_used >= max_crowd) else 0
            return (
                crowd_pen
                + self._reuse_adj(s.id)
                + (6 if (recent and s.id == recent[-1]) else 0)                  # 只避「上一集就用过」
                - 3 * self._tag_score(s, ref_tags)
                - (3 if beat_kind and beat_kind in fits else 0)
                - (5 if s.id in self._prefer_scenes else 0),
                cast,
                s.id,
            )

        best = sorted(cands, key=key)[0]
        return best, int(best.get("attrs.cast") or 2) >= 4

    def _reuse_adj(self, sid: str) -> int:
        """布景摊销：搭好的景要反复用，这才是短剧的成本逻辑。

        用过 1–3 次：-2（复用得越多越便宜，因为布景已摊销）
        第 4 次起：每次 +5（同一场景用太多次会单调）
        实测：30 集用 10–13 个景，60 集用 16–18 个景
        """
        use = self._scene_use.get(sid, 0)
        if use == 0:
            return 0
        if use <= 3:
            return -2
        return (use - 3) * 5

    def _pick_props(self, scene: Element | None, hero: Element, pool: list[Element],
                    ep: int = 1) -> list[str]:
        out: list[str] = []
        # 1) 主角视觉符号物（伏笔，优先级最高）
        for rid in (hero.get("attrs.required_refs") or []):
            if rid in self.lib._by_id and rid not in out:
                out.append(rid)
        # 2) 场景自带道具 -> 匹配到物品库（先精确，再子串）
        names = (scene.get("attrs.props") if scene else []) or []
        exact = {p.name: p.id for p in pool}
        for nm in names:
            pid = exact.get(nm)
            if not pid:
                pid = next((p.id for p in pool if nm and (nm in p.name or p.name in nm)), None)
            if pid and pid not in out:
                out.append(pid)
        # 3) 兜底 / 补足：按标签挑，但按集数轮换，避免全剧只有一个道具
        if len(out) < 2:
            ranked = sorted(pool, key=lambda p: (-self._tag_score(p, hero.tags), self._prop_use.get(p.id, 0), p.id))
            for p in ranked:
                if p.id not in out:
                    out.append(p.id)
                if len(out) >= 2:
                    break
        for pid in out:
            self._prop_use[pid] = self._prop_use.get(pid, 0) + 1
        return out[:3]

    def _pick_hook(self, pool: list[Element], kind: str, recent: list[str],
                   core: Element, exclude: list[str] | None = None) -> Element | None:
        exclude = exclude or []
        cands = [h for h in pool if (h.get("attrs.kind") == kind) and h.id not in exclude]
        if not cands:
            cands = [h for h in pool if h.id not in exclude] or pool
        if not cands:
            return None

        def key(h: Element):
            return (10 if h.id in recent else 0, -self._tag_score(h, core.tags), h.id)

        return sorted(cands, key=key)[0]

    def _cast(self, hero: Element, supports: list[Element], ep: int, seg_idx: int,
              target_lvl: int = 5) -> list[dict]:
        """主角必在场；配角轮换，控制成本。
        低成本集（lvl<=4）只留主角 + 1 人，把群戏预算留给高潮集。
        """
        budget = 3 if target_lvl >= self.third_cast_lvl else 2
        budget = min(budget, int(self.cons.get("max_cast_per_scene", 3)))
        cast = [{"chr_ref": hero.id, "presence": "在场", "note": "本集主线视角"}]
        room = max(0, budget - 1)
        for i, sp in enumerate(supports):
            if room <= 0:
                cast.append({"chr_ref": sp.id, "presence": "仅提及",
                             "note": "不上场，压低成本；只在对话里被提到"})
                continue
            on_stage = (i == 0 and seg_idx <= 2) or (ep + i) % 2 == 0 or seg_idx >= 3
            if on_stage and room > 0:
                cast.append({"chr_ref": sp.id, "presence": "在场",
                             "note": f"{sp.get('attrs.slot') or '配角'}·{sp.get('attrs.relation') or ''}".strip("·")})
                room -= 1
            else:
                cast.append({"chr_ref": sp.id, "presence": "声音" if (ep % 3 == 0) else "仅提及",
                             "note": "只出现在对话里"})
        return cast

    # ---------------- 语域 ----------------
    def _register_plan(self, segments: list[dict]):
        """usted→tú 的切换点写在段 2 后 1/3；退回写在段 4 开头（=决裂）。"""
        seg2 = segments[1] if len(segments) > 1 else segments[0]
        seg4 = segments[3] if len(segments) > 3 else None
        shift_ep = seg2["start"] + int(seg2["count"] * 0.66)
        retreat_ep = seg4["start"] if seg4 else None

        def plan(ep: int, seg_idx: int) -> tuple[str, str]:
            if seg_idx <= 1:
                return "敬语", "全片一律 usted，只有管家无意识越界时说 tú"
            if seg_idx == 2:
                if ep < shift_ep:
                    return "敬语", "仍是 usted，男主在等一个由头"
                if ep == shift_ep:
                    return "敬语→熟稔", "『Ya no me digas patrón. Dime tú.』——全剧称谓转折点"
                return "熟稔", "已改成 tú，吵架时才会退回 usted"
            if seg_idx == 3:
                return "熟稔", "tú 已稳定，用于标定关系"
            if seg_idx >= 4 and retreat_ep and ep == retreat_ep:
                return "敬语", "他改回 usted —— 一个词完成决裂，比任何台词都狠"
            if seg_idx >= 4 and retreat_ep and ep > retreat_ep:
                return ("熟稔" if seg_idx >= 5 else "敬语",
                        "段五重新回到 tú，但再也不是当初那个 tú" if seg_idx >= 5 else "双方刻意用回 usted 保持距离")
            return "敬语", "退回到 usted"

        return plan

    # ---------------- 卡片组装 ----------------
    def _segment_of(self, segments: list[dict], ep: int) -> dict:
        for s in segments:
            if s["start"] <= ep <= s["end"]:
                return s
        return segments[-1]

    def _episode_task(self, seg: dict, ep: int, conflicts: list[Element]) -> str:
        hints = [h.strip() for h in str(seg.get("event_hint", "")).split("/") if h.strip()]
        if not hints:
            hints = ["推进主线"]
        pos = (ep - seg["start"]) % len(hints)
        # 三层冲突按集轮换，避免全剧都是同一条「外部」阻力
        conf = conflicts[(ep - 1) % len(conflicts)] if conflicts else None
        base = f"{hints[pos]}"
        if conf:
            base += f"；阻力来自「{conf.get('attrs.conflict')}」（{conf.get('attrs.layer')}层）"
            st = conf.get("attrs.staging")
            if st:
                base += f"，具体演法：{st}"
        return base

    def _build_card(self, **kw) -> dict:
        ep, n = kw["ep"], kw["n"]
        seg, seg_idx = kw["seg"], kw["seg_idx"]
        beat, scene, props = kw["beat"], kw["scene"], kw["props"]
        scene2 = kw.get("scene2")
        oh, cliff = kw["open_hook"], kw["cliff"]
        core, arc, city, ending = kw["core"], kw["arc"], kw["city"], kw["ending"]
        conflicts = kw["conflicts"]
        ledger = kw["ledger"]
        cast = kw["cast"]

        tl_ranges = self.style.get("timeline") or [
            {"range": "0-3s", "kind": "open_hook"}, {"range": "3-12s", "kind": "setup"},
            {"range": "12-40s", "kind": "escalation"}, {"range": "40-54s", "kind": "reversal"},
            {"range": "54-60s", "kind": "cliff"},
        ]
        reg = kw["register"]
        shift = kw["address_shift"]
        beat_lvl = int(beat.get("attrs.lvl") or kw["target_lvl"]) if beat else kw["target_lvl"]
        cast_on = [c["chr_ref"] for c in cast if c["presence"] == "在场"]
        cast_names = "、".join(ledger["characters"][c]["name_zh"] for c in cast_on if c in ledger["characters"])
        n_names = len([c for c in cast_on if c in ledger["characters"]])
        setup_scene = scene
        beat_kind = (beat.get("attrs.kind") if beat else "") or ""
        beat_text = (beat.get("attrs.text") if beat else "") or ""
        oh_text = ((oh.get("attrs.visual") or oh.get("attrs.text")) if oh else "") or ""
        cliff_text = (cliff.get("attrs.text") if cliff else "") or ""
        conf0 = conflicts[(ep - 1) % len(conflicts)] if conflicts else None

        # 五段内容种子：给 LLM 的骨架提示，也是降级时的正文
        seeds = {
            "open_hook": {"range": tl_ranges[0]["range"], "hook_ref": oh.id if oh else "",
                          "content": oh_text or "开场反常画面"},
            "setup": {"range": tl_ranges[1]["range"],
                      "scene_ref": setup_scene.id if setup_scene else "",
                      "cast_refs": cast_on,
                      "content": f"{(setup_scene.name if setup_scene else '场景')}，"
                                 f"{(setup_scene.get('attrs.light') if setup_scene else '夜') or '夜'}。"
                                 f"{cast_names}在场，{'只有两个人说话' if n_names <= 2 else '第三个人始终不开口'}。"},
            "escalation": {"range": tl_ranges[2]["range"],
                           "conflict_ref": conf0.id if conf0 else "",
                           "scene_ref": scene2.id if scene2 else "",
                           "content": (f"{conf0.get('attrs.staging')}。" if conf0 else "")
                                      + ("换景：" + scene2.name + "。" if scene2 else "")
                                      + "必须让观众比角色多知道一件事。"},
            "reversal": {"range": tl_ranges[3]["range"], "beat_ref": beat.id if beat else "",
                         "content": beat_text or "用动作完成一次翻转"},
            "cliff": {"range": tl_ranges[4]["range"], "hook_ref": cliff.id if cliff else "",
                      "content": cliff_text or "新信息入场"},
        }

        ep_title = self._episode_title(ep, seg, beat, scene, ledger)

        return {
            "episode": ep,
            "market": self.lib.market,
            "arc_ref": arc.id if arc else "",
            "arc_segment": seg["name"],
            "segment_index": seg_idx,
            "segment_range": f"{seg['start']}-{seg['end']}",
            "episode_task": self._episode_task(seg, ep, conflicts),
            "title_hint": ep_title,
            "timeline": seeds,
            "cast": cast,
            "scene_refs": [s.id for s in (scene, scene2) if s],
            "props": props,
            "camera": self._camera(scene, scene2),
            "dialogue_notes": {
                "register": reg,
                "address_shift": shift,
                "language": self.style.get("language_note", ""),
                "taboo_check": True,
            },
            "beat": {"beat_ref": beat.id if beat else "", "kind": beat_kind,
                     "escalation_level": beat_lvl,
                     "info_gap": self._info_gap(core, conflicts)},
            "hook": {"cliff_ref": cliff.id if cliff else "",
                     "promise": self._promise(core, seg, ending)},
            "compliance": self._compliance_check(
                [x for x in [oh, cliff, scene, beat] if x] + conflicts,
                oh_text + cliff_text + beat_text
            ),
            "cost": {
                "cast_scenes": len([c for c in cast if c["presence"] == "在场"]),
                "crowd_scenes": 1 if kw["is_crowd"] else 0,
                "ai_difficulty": (scene.get("attrs.ai_difficulty") if scene else "低") or "低",
            },
            "id_ledger": {
                "characters": {c: ledger["characters"][c] for c in ledger["characters"]},
                "scenes": {s: ledger["scenes"][s] for s in ledger["scenes"]},
            },
            "degrade_seed": {
                "open_hook": oh_text,
                "setup": f"{(scene.name if scene else '')}，{(scene.get('attrs.light') if scene else '夜') or '夜'}。",
                "escalation": (conf0.get("attrs.staging") if conf0 else "") or "冲突升级。",
                "reversal": beat_text,
                "cliff": cliff_text,
                "register_es": (self.style.get("degrade_templates", {}).get("line_register", {}) or {}).get(reg, "usted"),
            },
        }

    def _episode_title(self, ep: int, seg: dict, beat: Element | None, scene: Element | None,
                       ledger: dict) -> str:
        kind = (beat.get("attrs.kind") if beat else "") or ""
        loc = scene.name if scene else seg["name"]
        base = f"{kind}·{loc}" if kind else f"{seg['name']}·{loc}"
        return f"E{ep:02d} {base}"[:28]

    def _camera(self, scene: Element | None, scene2: Element | None = None) -> list[str]:
        cast = int(scene.get("attrs.cast") or 2) if scene else 2
        base = ["近景对峙", "空镜情绪"] if cast <= 2 else ["近景对峙", "中景群像"]
        if scene2:
            base.append("背影走位")
        return base

    def _info_gap(self, core: Element, conflicts: list[Element]) -> str:
        inner = next((c for c in conflicts if c.get("attrs.layer") == "内部"), None)
        body = inner.get("attrs.conflict") if inner else core.get("attrs.desire") or "身份"
        return f"观众知道主角正在被「{body}」这件事拖着，场上的对手不知道她知道。"

    def _promise(self, core: Element, seg: dict, ending: Element | None) -> str:
        end = ending.get("attrs.ending") if ending else ""
        return f"本集埋的点会在「{seg['name']}」段末回收；全剧承诺：{end or core.name}"

    # ---------------- 红线 ----------------
    def _compliance_check(self, elements: list[Element], blob: str) -> dict:
        hits, checked = [], []
        for rl in self.lib.red_lines():
            name = rl.get("name", "")
            checked.append(name)
            key = re.sub(r"（.*?）|\(.*?\)", "", name).strip()
            if key and len(key) >= 2 and key in blob:
                hits.append({"rule": name, "evidence": key, "note": rl.get("note", "")})
        forb = [t for t in (self.cons.get("forbidden_topics") or []) if t]
        for t in forb:
            if t in blob:
                hits.append({"rule": f"禁用主题：{t}", "evidence": t, "note": "来自蓝图 constraints.forbidden_topics"})
        return {"red_line_passed": not hits, "checked_items": checked, "hits": hits,
                "notes": "自动扫描关键词，不能替代人工复核" if hits else ""}

    # ---------------- 审计 ----------------
    def _ladder_check(self, ladder: list[int]) -> dict:
        n = len(ladder)
        adj = max((abs(ladder[i] - ladder[i - 1]) for i in range(1, n)), default=0)
        blocks = [max(ladder[s:s + 10]) for s in range(0, n, 10)]
        run = best = 0
        for v in ladder:
            run = run + 1 if v <= 3 else 0
            best = max(best, run)
        return {
            "max_adjacent_delta": adj,
            "rule_adjacent": adj <= 2,
            "min_peak_per_10": min(blocks) if blocks else 0,
            "rule_block_peak": all(b >= 7 for b in blocks),
            "count_9_10": sum(1 for v in ladder if v >= 9),
            "rule_high_cap": sum(1 for v in ladder if v >= 9) <= 3,
            "max_low_run": best,
            "rule_low_run": best <= 4,
        }

    def _register_timeline(self, cards: list[dict]) -> list[dict]:
        out, last = [], None
        for c in cards:
            r = c["dialogue_notes"]["register"]
            if r != last:
                out.append({"episode": c["episode"], "register": r,
                            "note": c["dialogue_notes"]["address_shift"]})
                last = r
        return out

    def _usage(self, cards: list[dict]) -> dict:
        from collections import Counter
        scenes = Counter(s for c in cards for s in c["scene_refs"])
        beats = Counter(c["beat"]["beat_ref"] for c in cards)
        props = Counter(p for c in cards for p in c["props"])
        return {
            "scenes": dict(scenes.most_common()),
            "beats": dict(beats.most_common()),
            "props": dict(props.most_common()),
            "distinct_scenes": len(scenes),
            "distinct_beats": len(beats),
        }
