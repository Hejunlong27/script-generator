# -*- coding: utf-8 -*-
"""核心数据模型：Element / Library / Blueprint。

三层数据结构，各管一件事：
  Element    —— 素材本身（不可变，来自 data/elements/*.json）
  Blueprint  —— 用户预设（可选择、可编辑 override、可自动补齐）
  Card       —— 编译产物（每集一张组装卡，见 compiler.py）
  Script     —— 最终输出（见 exporter.py）

Blueprint 的校验就是把「素材库 README 里那些靠人记的拼装规则」变成代码：
  B∩C、C∩D 链式相连 / F 三层齐 / 簇不超过 3 / S 等级 / 红线
规则错了当场报错，而不是等到第 30 集崩。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import registry
from .errors import (
    BlueprintError,
    CardinalityError,
    CompatibilityError,
    ConstraintBlockedError,
    LibraryError,
    RefNotFoundError,
)

def _data_dir() -> Path:
    """打包成 exe 后，data 在 PyInstaller 的解包目录里。"""
    import sys
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "data"
    return Path(__file__).resolve().parents[1] / "data"


DATA_DIR = _data_dir()

ID_RE = re.compile(r"^[A-Z]{2}-[A-Z]{3}-(?:[A-Z]?\d{2,3})$")

# 五个簇（来自 picker/README §2.1）
CLUSTERS: dict[str, tuple[str, ...]] = {
    "家族": ("FAM", "CLA", "MON"),
    "信仰": ("REL", "SUP"),
    "欲望": ("SEX", "IDE"),
    "暴力": ("VIO", "CRI"),
    "职场": ("OCC",),
}

# 簇间兼容表（picker/README §2.2）：2=强配 1=可混 0=慎配 -1=不配
CLUSTER_MATRIX: dict[str, dict[str, int]] = {
    "家族": {"家族": 2, "信仰": 2, "欲望": 2, "暴力": 1, "职场": 1},
    "信仰": {"家族": 2, "信仰": 2, "欲望": 0, "暴力": 0, "职场": -1},
    "欲望": {"家族": 2, "信仰": 0, "欲望": 2, "暴力": 1, "职场": 1},
    "暴力": {"家族": 1, "信仰": 0, "欲望": 1, "暴力": 2, "职场": 0},
    "职场": {"家族": 1, "信仰": -1, "欲望": 1, "暴力": 0, "职场": 2},
}

TAG_CN = {
    "CLA": "阶级", "FAM": "家族", "REL": "宗教", "CRI": "犯罪", "SEX": "欲望",
    "MON": "金钱", "IDE": "身份", "OCC": "职场", "VIO": "复仇", "SUP": "超自然",
}


def clusters_of(tags: list[str]) -> set[str]:
    out = set()
    for cname, ctags in CLUSTERS.items():
        if any(t in ctags for t in tags):
            out.add(cname)
    return out


# ---------------------------------------------------------------- Element
@dataclass
class Element:
    id: str
    type: str
    type_cn: str
    market: str
    layer: str
    name: str
    tags: list[str]
    attrs: dict[str, Any]
    source: str = ""
    name_local: str = ""
    s_level: str = "S1"

    @classmethod
    def from_dict(cls, d: dict) -> "Element":
        return cls(
            id=d["id"], type=d["type"], type_cn=d.get("type_cn", d["type"]),
            market=d.get("market", ""), layer=d.get("layer", ""),
            name=d.get("name", d["id"]), tags=list(d.get("tags") or []),
            attrs=dict(d.get("attrs") or {}), source=d.get("source", ""),
            name_local=d.get("name_local", ""), s_level=d.get("s_level", "S1"),
        )

    def get(self, path: str, default: Any = None) -> Any:
        """支持 'attrs.flaw' 这种路径取值。"""
        cur: Any = self
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part, default)
            else:
                cur = getattr(cur, part, default)
            if cur is None:
                return default
        return cur if cur is not None else default

    def prompt_text(self, text_field: str | None = None) -> str:
        """给 LLM 看的一行要素描述。字段选择由注册表决定。"""
        spec = registry.ELEMENT_TYPES.get(self.type)
        field_path = text_field or (spec.text_field if spec else "name")
        main = self.get(field_path) or self.name
        bits = []
        for k, v in self.attrs.items():
            if k in ("_file", "structure_type"):
                continue
            if isinstance(v, list):
                v = " / ".join(str(x) for x in v if x)
            if v and str(v) != str(main):
                bits.append(f"{k}={v}")
        local = f"（{self.name_local}）" if self.name_local else ""
        tail = f"；{'; '.join(bits[:6])}" if bits else ""
        return f"[{self.id}] {main}{local}{tail}"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "type_cn": self.type_cn,
            "market": self.market, "layer": self.layer, "name": self.name,
            "name_local": self.name_local, "s_level": self.s_level,
            "tags": self.tags, "attrs": self.attrs, "source": self.source,
        }


# ---------------------------------------------------------------- Library
class Library:
    """按市场载入的要素库。所有查询都走这里，不在业务代码里碰文件。"""

    def __init__(self, market: str, pack: dict):
        self.market = market
        self.pack = pack
        self.market_base: dict = pack.get("market_base") or {}
        self._by_id: dict[str, Element] = {}
        self._by_type: dict[str, list[Element]] = {}
        for raw in pack.get("elements", []):
            el = Element.from_dict(raw)
            self._by_id[el.id] = el
            self._by_type.setdefault(el.type, []).append(el)

    # ---- 载入 ----
    @classmethod
    def load(cls, market: str = "MX", data_dir: Path | None = None) -> "Library":
        d = data_dir or DATA_DIR
        edir = d / "elements"
        path = edir / f"{market.upper()}.elements.json"
        if not path.is_file():
            # 完整素材库不随源码公开，仓库里只有示例包
            sample = edir / f"{market.upper()}.elements.sample.json"
            if sample.is_file():
                path = sample
            else:
                raise LibraryError(
                    f"要素包不存在: {path}",
                    hint="先跑 python tools/build_elements.py 从素材库编译",
                    where=str(path),
                )
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise LibraryError(f"要素包 JSON 解析失败: {e}", where=str(path)) from e
        if not pack.get("elements"):
            raise LibraryError("要素包为空", hint="检查素材库路径是否正确", where=str(path))
        return cls(market.upper(), pack)

    # ---- 查询 ----
    def get(self, eid: str) -> Element:
        try:
            return self._by_id[eid]
        except KeyError:
            raise RefNotFoundError(
                f"要素不存在: {eid}",
                hint="检查 ID 拼写，或确认该要素已编入 data/elements/*.json",
                where=eid,
            ) from None

    def has(self, eid: str) -> bool:
        return eid in self._by_id

    def of_type(self, type_key: str) -> list[Element]:
        return list(self._by_type.get(type_key, []))

    def all(self) -> list[Element]:
        return list(self._by_id.values())

    def red_lines(self) -> list[dict]:
        return self.market_base.get("attrs", {}).get("red_lines", [])

    def counts(self) -> dict:
        return {t: len(v) for t, v in sorted(self._by_type.items())}


# ---------------------------------------------------------------- Blueprint
DEFAULT_CONSTRAINTS = {
    "max_cast_per_scene": 3,
    "max_crowd_scenes": 3,
    "max_scene_per_episode": 2,
    "allow_s_levels": ["S0", "S1", "S2"],
    "forbidden_topics": [],
    "auto_fill": True,
    "autofill_randomness": 0.6,      # 0=只要最优解（可复现），1=放开抽
    "beat_level_min": 1,
    "beat_level_max": 10,
}


@dataclass
class Blueprint:
    meta: dict
    elements: dict[str, Any]            # slot -> id 或 [id]
    overrides: dict[str, dict] = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    # 记录哪些 meta 字段是「系统默认填的」，好让风格包有机会覆盖自己的默认规格
    auto_meta: set = field(default_factory=set)

    # 目标规格：30 集 × 60 秒（竖屏短剧的付费卡点密度）
    DEFAULT_EPISODES = 30
    DEFAULT_SECONDS = 60

    # ---- 解析 ----
    @classmethod
    def from_dict(cls, d: dict) -> "Blueprint":
        if not isinstance(d, dict):
            raise BlueprintError("蓝图必须是 JSON 对象")
        meta = dict(d.get("meta") or {})
        el_raw = dict(d.get("elements") or {})
        els: dict[str, Any] = {}
        for k, v in el_raw.items():
            els[registry.canon_slot(k)] = v
        cons = {**DEFAULT_CONSTRAINTS, **(d.get("constraints") or {})}
        bp = cls(meta=meta, elements=els, overrides=d.get("overrides") or {}, constraints=cons)
        bp._normalize_meta()
        return bp

    def _normalize_meta(self) -> None:
        m = self.meta
        m.setdefault("market", "MX")
        m.setdefault("title", "未命名短剧")
        for k, dv in (("episode_count", self.DEFAULT_EPISODES),
                      ("episode_seconds", self.DEFAULT_SECONDS)):
            if k not in m or m[k] in (None, ""):
                m[k] = dv
                self.auto_meta.add(k)
        m.setdefault("style", "mx-telenovela")
        m.setdefault("output_language", "zh")
        m.setdefault("seed", 20261020)
        try:
            m["episode_count"] = max(1, min(200, int(m["episode_count"])))
            m["episode_seconds"] = max(15, min(180, int(m["episode_seconds"])))
        except (TypeError, ValueError):
            raise BlueprintError("episode_count / episode_seconds 必须是整数",
                                 where="meta.episode_count") from None

    def apply_style_defaults(self, style: dict) -> "Blueprint":
        """风格包声明了默认集数/时长时，覆盖「系统自动填的」那一部分。

        用户显式给的值不动 —— 30×60 是默认，不是强制。
        """
        for key, skey in (("episode_count", "episode_count_default"),
                          ("episode_seconds", "episode_seconds_default")):
            if key in self.auto_meta and style.get(skey):
                try:
                    self.meta[key] = int(style[skey])
                except (TypeError, ValueError):
                    pass
        return self

    # ---- 取槽位 ----
    def ids(self, slot: str) -> list[str]:
        v = self.elements.get(registry.canon_slot(slot))
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v if x]
        raise BlueprintError(f"elements.{slot} 必须是字符串或字符串数组", where=f"elements.{slot}")

    def first(self, slot: str) -> str | None:
        ids = self.ids(slot)
        return ids[0] if ids else None

    # ---- 校验（输入层，阻断）----
    def validate(self, lib: Library) -> list[dict]:
        """返回 issue 列表；任一条 level=error 即阻断。"""
        issues: list[dict] = []

        def err(code: str, msg: str, hint: str = "", where: str = "") -> None:
            issues.append({"level": "error", "code": code, "message": msg, "hint": hint, "where": where})

        def warn(code: str, msg: str, hint: str = "", where: str = "") -> None:
            issues.append({"level": "warn", "code": code, "message": msg, "hint": hint, "where": where})

        # 1. 数量 + 存在性 + 类型匹配
        for slot, spec in registry.blueprint_slots():
            got = self.ids(slot)
            if len(got) < spec.min_pick:
                if spec.min_pick > 0:
                    # auto_fill 开着时缺口一定会被补上，报「未选」只是噪音。
                    # 补齐记录在 autofill 的 log 里，用户能看到具体补了什么。
                    if not self.constraints.get("auto_fill"):
                        err("IN_CARDINALITY", f"{spec.cn} 至少需要 {spec.min_pick} 项",
                            spec.hint, f"elements.{slot}")
                continue
            if spec.role == "single" and len(got) > 1:
                err("IN_CARDINALITY", f"{spec.cn} 只能选 1 项，当前 {len(got)} 项", spec.hint, f"elements.{slot}")
            if spec.pick and len(got) > spec.pick:
                warn("IN_CARDINALITY", f"{spec.cn} 建议 {spec.pick} 项，当前 {len(got)} 项（不阻断）", spec.hint, f"elements.{slot}")
            for eid in got:
                if not lib.has(eid):
                    err("IN_REF_NOT_FOUND", f"要素不存在: {eid}", "检查 ID 或重新编译要素包", f"elements.{slot}")
                    continue
                el = lib.get(eid)
                expect = registry.slot_element_type(slot, spec)
                if el.type != expect:
                    err(
                        "IN_TYPE_MISMATCH",
                        f"{eid} 的类型是 {el.type}（{el.type_cn}），不能填进 {slot}（{spec.cn}）",
                        f"{spec.cn} 应使用 {expect} 类型的 ID", f"elements.{slot}",
                    )
                if el.s_level not in self.constraints["allow_s_levels"]:
                    err(
                        "IN_CONSTRAINT_BLOCKED",
                        f"{eid} 的 S 等级为 {el.s_level}，超出约束 {self.constraints['allow_s_levels']}",
                        "S2 表示内核必须重写，S3 是禁区；放宽约束前请确认", f"elements.{slot}",
                    )

        # 2. 冲突三层齐（本库最容易做错的一栏）
        cf = [lib.get(i) for i in self.ids("conflict") if lib.has(i)]
        if len(cf) >= 3:
            layers = {c.get("attrs.layer") for c in cf}
            for need in ("外部", "内部", "结构"):
                if need not in layers:
                    err(
                        "IN_INCOMPATIBLE",
                        f"冲突缺少「{need}」层，当前只有 {sorted(x for x in layers if x)}",
                        "只靠反派使坏撑不住全剧：必须 1 外部 + 1 内部 + 1 结构", "elements.conflict",
                    )

        # 3. B→C→D 链式相连
        core, arc, hero = (lib.get(i) if i and lib.has(i) else None for i in
                           (self.first("core"), self.first("arc"), self.first("hero")))
        if core and arc:
            if not (set(core.tags) & set(arc.tags)):
                err(
                    "IN_COMPATIBILITY",
                    f"内核 {core.id} 与主线 {arc.id} 无共同标签（{core.tags} vs {arc.tags}）",
                    "B∩C 必须 ≥1，否则内核和主线是两张皮", "elements.arc",
                )
            pairs = arc.get("attrs.pairs_core") or []
            if pairs and core.id.split("-")[-1] not in pairs:
                warn("IN_PAIRING", f"主线 {arc.id} 标注的配内核是 {pairs}", "可作为参考，不一定阻断")
        if arc and hero:
            if not (set(arc.tags) & set(hero.tags)):
                err(
                    "IN_COMPATIBILITY",
                    f"主线 {arc.id} 与主角 {hero.id} 无共同标签（{arc.tags} vs {hero.tags}）",
                    "C∩D 必须 ≥1，换主角最便宜", "elements.hero",
                )

        # 4. 簇不超过 3（1 主 + 最多 2 副）
        picked = [lib.get(i) for slot, _ in registry.blueprint_slots()
                  for i in self.ids(slot)
                  if slot not in ("conflict", "package") and lib.has(i)]
        all_tags = [t for el in picked for t in el.tags]
        clusters = clusters_of(all_tags)
        if len(clusters) > 3:
            warn(
                "IN_CLUSTER_OVERFLOW",
                f"题材簇达 {len(clusters)} 个（{sorted(clusters)}），超过 3 个观众记不住",
                "砍副簇，优先砍 OCC（职场）", "elements",
            )
        cl = sorted(clusters)
        for i, a in enumerate(cl):
            for b in cl[i + 1:]:
                if CLUSTER_MATRIX.get(a, {}).get(b) == -1:
                    warn("IN_CLUSTER_CONFLICT", f"簇「{a}」与「{b}」不配", "例：信仰 × 职场 是禁区", "elements")

        # 5. 红线关键词
        forb = list(self.constraints.get("forbidden_topics") or [])
        if forb:
            blob = " ".join(el.prompt_text() for el in picked)
            hits = [t for t in forb if t and t in blob]
            if hits:
                warn("IN_REDLINE_HINT", f"选中素材命中禁用主题关键词: {hits}", "生成阶段会再做一次红线扫描")

        return issues

    # ---- override 应用（用户编辑预设要素，不污染素材库）----
    def apply_override(self, el: Element) -> Element:
        ov = self.overrides.get(el.id)
        if not ov:
            return el
        attrs = dict(el.attrs)
        name = el.name
        for k, v in ov.items():
            if k == "name":
                name = v
            elif k.startswith("attrs."):
                attrs[k[6:]] = v
            elif k in attrs or k not in ("id", "type", "market"):
                attrs[k] = v
        return Element(
            id=el.id, type=el.type, type_cn=el.type_cn, market=el.market, layer=el.layer,
            name=name, tags=list(el.tags), attrs=attrs, source=el.source,
            name_local=el.name_local, s_level=el.s_level,
        )

    # ---- 加权随机抽样 ----
    # 各槽位的抽样池上限。池越大越容易抽出「和题材不搭」的东西，所以要收。
    POOL_CAP = {"character": 8, "support": 10, "city": 10, "ending": 10, "open_hook": 8}

    @staticmethod
    def _pick(cands: list["Element"], rng, rnd: float,
              cap: int | None = None) -> "Element | None":
        """从一个**已按质量排序**的候选池里抽样。

        rnd=0   -> 永远取最优（确定性，可复现）
        rnd=0.6 -> 在 top 60% 里按权重抽，头部占优
        rnd=1   -> 全池线性权重，接近均匀

        只取 top-K 是关键：随机度低时不碰烂素材，随机度高才放开。
        纯均匀随机会把「缺口 5 个场景的包」和「已验证的包」等概率抽到，那不是随机，是胡闹。
        """
        if cap:
            cands = cands[:max(1, cap)]
        n = len(cands)
        if n == 0:
            return None
        if n == 1:
            return cands[0]
        k = max(1, min(n, int(round(1 + rnd * (n - 1)))))
        pool = cands[:k]
        if k == 1:
            return pool[0]
        temp = max(0.05, rnd)
        ws = [((k - i) / k) ** (1.0 / temp) for i in range(k)]
        total = sum(ws)
        r = rng.uniform(0.0, total)
        acc = 0.0
        for c, w in zip(pool, ws):
            acc += w
            if r <= acc:
                return c
        return pool[-1]

    # ---- 自动补齐 ----
    # 补齐后会触发这几类问题的话，换个派生种子重抽。纯计算，代价可忽略。
    QUALITY_CODES = {"IN_PAIRING", "IN_CLUSTER_OVERFLOW", "IN_CLUSTER_CONFLICT"}

    def autofill(self, lib: Library, seed: int | None = None,
                 randomness: float | None = None, max_tries: int | None = None
                 ) -> tuple["Blueprint", list[dict]]:
        """带种子的加权随机补齐 + **自查重试**。

        一次抽不干净就换派生种子重抽（最多 5 次），保证交出去的蓝图不自带黄条。
        随机度 0 时结果与种子无关，重抽无意义，只跑一次。
        """
        rnd_seed = int(seed if seed is not None else (self.meta.get("seed") or 0))
        rnd = float(randomness if randomness is not None
                    else self.constraints.get("autofill_randomness", 0.6))
        rnd = max(0.0, min(1.0, rnd))
        tries = max_tries if max_tries is not None else (1 if rnd <= 0.001 else 5)

        best: tuple["Blueprint", list[dict]] | None = None
        for attempt in range(max(1, tries)):
            used = rnd_seed + attempt * 7919
            bp, log = self._autofill_impl(lib, used, rnd)
            for item in log:
                item["seed"] = used
                if attempt:
                    item["retry"] = attempt
            bad = [i for i in bp.validate(lib)
                   if i["level"] == "error" or i.get("code") in self.QUALITY_CODES]
            if not bad:
                return bp, log
            if best is None or len(bad) < best[2]:
                best = (bp, log, len(bad))
        bp, log = best[0], best[1]
        return bp, log

    def _autofill_impl(self, lib: Library, rnd_seed: int, rnd: float
                       ) -> tuple["Blueprint", list[dict]]:
        """缺口补齐的一次实现：**带种子的加权随机**，不是每次都给同一套。

        顺序有讲究：先补题材，再补剧情。题材包带作者标注的推荐 core/arc，
        先定题材能让后面的补齐和用户选择都收窄。

        同一个 rnd_seed 必然得到同一个结果。
        """
        import random as _random

        if not self.constraints.get("auto_fill"):
            return self, []
        rng = _random.Random(rnd_seed)

        els = dict(self.elements)
        log: list[dict] = []

        # ---- 第 1 步：题材包优先（成熟包权重高，但不排除有缺口的包）----
        if not self.ids("package"):
            pkgs = list(lib.of_type("package"))
            if pkgs:
                def pkg_q(p: "Element") -> tuple:
                    ready = bool(p.get("attrs.ready"))
                    gaps = int(p.get("attrs.gaps") or 0)
                    prior = 1 if "最优先" in str(p.get("attrs.remark") or "") else 0
                    return (ready, -gaps, prior, -int(p.get("attrs.no") or 99))

                pkgs.sort(key=pkg_q, reverse=True)
                pick = self._pick(pkgs, rng, rnd)
                els["package"] = pick.id
                log.append({
                    "slot": "package", "cn": "题材组合包", "filled": [pick.id],
                    "reason": (f"按种子 {rnd_seed} 随机抽取（随机度 {rnd:.0%}）"
                               + ("" if pick.get("attrs.ready") else "；该题材有场景缺口，注意成本")),
                })

        pkg = lib.get(els["package"]) if isinstance(els.get("package"), str) and lib.has(els["package"]) else None

        # core -> 声明了配它的 arc。素材库自己标的「配内核」是唯一事实，
        # 自动补齐必须按它来，否则会补出「主线报 IN_PAIRING」的组合。
        paired_arcs: dict[str, list["Element"]] = {}
        for arc_el in lib.of_type("arc"):
            for code in (arc_el.get("attrs.pairs_core") or []):
                paired_arcs.setdefault(code, []).append(arc_el)
        cores_with_arc = set(paired_arcs)

        # 已选要素占用的题材簇。选新要素时不能让它突破「1 主簇 + 最多 2 副簇」。
        cur_clusters: set[str] = set()
        for slot_ids in (self.ids(s) for s, _ in registry.blueprint_slots()):
            for eid in slot_ids:
                if lib.has(eid):
                    cur_clusters |= clusters_of(lib.get(eid).tags)
        allow_levels = set(self.constraints.get("allow_s_levels") or ["S0", "S1", "S2"])

        def cluster_cost(e: "Element") -> int:
            """候选带来的额外簇数。超过 3 个簇，观众记不住这部剧在讲什么。"""
            total = len(cur_clusters | clusters_of(e.tags))
            return max(0, total - 3) * 100

        def core_code_of() -> str:
            """读工作副本 els —— 刚补出来的 core 还没写回 self.elements。"""
            v = els.get("core")
            ids = [v] if isinstance(v, str) else (v or [])
            for eid in ids:
                if isinstance(eid, str) and lib.has(eid):
                    return eid.split("-")[-1]
            return ""

        # 以已选的 core/arc 为锚点
        ref_tags: list[str] = []
        core_tags: list[str] = []
        arc_tags: list[str] = []
        for slot in ("core", "arc"):
            for eid in self.ids(slot):
                if lib.has(eid):
                    ref_tags += lib.get(eid).tags
                    if slot == "core":
                        core_tags += lib.get(eid).tags
                    else:
                        arc_tags += lib.get(eid).tags

        for slot, spec in registry.blueprint_slots():
            if slot == "package":
                continue
            got = self.ids(slot)
            target = spec.pick or max(1, spec.min_pick)
            if len(got) >= max(1, spec.min_pick):
                continue
            etype = spec.bound_type or slot
            cands = [e for e in lib.of_type(etype)]
            if slot == "open_hook":
                cands = [c for c in cands if c.get("attrs.kind") == "开场钩"] or cands
            if not cands:
                continue

            # 题材包给了推荐位，就用推荐位（作者已按语义挑过，比算标签准）
            preset: list[str] = []
            if pkg is not None:
                if slot == "core":
                    preset = list(pkg.get("attrs.preset_cores") or [])
                elif slot == "arc":
                    preset = list(pkg.get("attrs.preset_arcs") or [])

            def score(e: Element) -> tuple:
                in_preset = len(preset) - preset.index(e.id) if e.id in preset else 0
                overlap = len(set(e.tags) & set(ref_tags))
                cc = cluster_cost(e)
                # picker 靠前的更稳（作者已按可用度排序）
                order = -int(re.sub(r"\D", "", e.id.split("-")[-1]) or 99)
                if slot in ("core", "arc"):
                    # 推荐位与配对是硬约束，簇让位于它们
                    return (in_preset, -cc, overlap, order)
                return (-cc, overlap, order)

            # S 等级是硬门槛：allow_s_levels 收紧时不能补出超纲素材
            allowed = [c for c in cands if c.s_level in allow_levels]
            cands = allowed or cands
            cands.sort(key=score, reverse=True)

            # 自动补的核心必须能被某条主线认领，否则一定触发 IN_PAIRING
            if slot == "core":
                ok = [c for c in cands if c.id.split("-")[-1] in cores_with_arc]
                cands = ok or cands
            # 主线只从「配这个内核」的里抽 —— 配对是硬事实，优先于题材推荐范围
            if slot == "arc":
                cc = core_code_of()
                if cc and paired_arcs.get(cc):
                    cands = list(paired_arcs[cc])

            # 题材推荐的 core/arc 必须在池里，否则等于没选题材。
            # 只有随机度很高（>0.85）才允许跳出推荐位，且只放 6 个名额。
            cap = self.POOL_CAP.get(slot)
            if slot in ("core", "arc") and preset:
                rec = [c for c in cands if c.id in preset]
                if rnd < 0.85:
                    cands, cap = rec or cands, None
                else:
                    rest = [c for c in cands if c.id not in preset]
                    cands, cap = (rec + rest), len(rec) + 6

            # 随机也要守底线：池子必须按**校验的真实规则**过滤，不能用累积标签凑。
            # 校验比的是 B∩C（core vs arc）和 C∩D（arc vs character），
            # 用 ref_tags 会让「和冲突/结局沾边」的人设混进来，然后必然报断链。
            if slot == "arc" and core_tags:
                ok = [c for c in cands if set(c.tags) & set(core_tags)]
                cands = ok or cands
            if slot == "character" and arc_tags:
                ok = [c for c in cands if set(c.tags) & set(arc_tags)]
                cands = ok or cands

            need = target - len(got)

            # 冲突槽特殊处理：必须凑齐 外部 / 内部 / 结构 三层，层内再随机
            if slot == "conflict":
                have = {lib.get(i).get("attrs.layer") for i in got if lib.has(i)}
                chosen = []
                for layer in ("外部", "内部", "结构"):
                    if layer in have:
                        continue
                    pool = [c for c in cands if c.get("attrs.layer") == layer and c.id not in chosen]
                    picked = self._pick(pool, rng, rnd, cap)
                    if picked is not None:
                        chosen.append(picked.id)
                chosen = chosen[:need] if need > 0 else []
            elif spec.role == "multi":
                chosen, remain = [], [c for c in cands if c.id not in got]
                while remain and len(chosen) < max(0, need):
                    picked = self._pick(remain, rng, rnd, cap)
                    if picked is None:
                        break
                    chosen.append(picked.id)
                    remain = [c for c in remain if c.id != picked.id]
            else:
                picked = self._pick([c for c in cands if c.id not in got], rng, rnd, cap)
                chosen = [picked.id] if picked else []

            if chosen:
                els[slot] = chosen[0] if spec.role == "single" else got + chosen
                log.append({"slot": slot, "cn": spec.cn, "filled": chosen,
                            "reason": f"按种子 {rnd_seed} 随机抽取（随机度 {rnd:.0%}）"})
                picked_tags = [t for c in cands if c.id in chosen for t in c.tags]
                ref_tags += picked_tags
                for c in cands:
                    if c.id in chosen:
                        cur_clusters |= clusters_of(c.tags)
                if slot == "core":
                    core_tags = picked_tags
                elif slot == "arc":
                    arc_tags = picked_tags

        if log:
            bp = Blueprint(meta=dict(self.meta), elements=els,
                           overrides=self.overrides, constraints=self.constraints)
            return bp, log
        return self, log

    # ---- 解析为要素对象 ----
    def resolve(self, lib: Library) -> dict[str, Any]:
        """槽位 -> Element（已应用 override）"""
        out: dict[str, Any] = {}
        for slot, spec in registry.blueprint_slots():
            ids = self.ids(slot)
            objs = [self.apply_override(lib.get(i)) for i in ids if lib.has(i)]
            out[slot] = objs[0] if (spec.role == "single" and objs) else (objs if objs else [])
        return out

    def to_dict(self) -> dict:
        return {
            "schema_version": "1.0",
            "meta": self.meta,
            "elements": self.elements,
            "overrides": self.overrides,
            "constraints": self.constraints,
        }
