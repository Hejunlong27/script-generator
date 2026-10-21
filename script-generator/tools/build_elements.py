#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 material-library（Markdown 表格 + JSON）编译成机器可读的要素包。

输出：data/elements/{market}.elements.json
用法：python tools/build_elements.py [--market MX] [--src ../material-library]

为什么不直接让 LLM 读 Markdown：
  1. Markdown 表格无法做引用校验（引用错了没人发现）
  2. 每次生成都要把 100KB 文本塞进上下文，成本高且不稳定
  3. 素材加一条要改 prompt，改一次全库重跑

编译一次，之后所有生成都走结构化数据。素材库仍是唯一事实源（source of truth）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT.parent / "material-library"

# picker 模块字母 -> (要素类型码, 要素类型 key, 中文名)
PICKER_MODULES = {
    "A": ("LOC", "city", "城市背景"),
    "B": ("COR", "core", "剧情内核"),
    "C": ("ARC", "arc", "主线骨架"),
    "D": ("CHR", "character", "主角人设"),
    "E": ("SPT", "support", "对手与配角"),
    "F": ("CFT", "conflict", "冲突内核"),
    "G": ("HOK", "hook", "开场钩子"),
    "H": ("END", "ending", "结局走向"),
}

# 表头 -> attrs 键。按名称映射，不按列序（C 模块的列序和别的模块不一样）
HEADER_MAP = {
    "城市 / 街区": "place",
    "一句话画面": "visual",
    "内核（一句话）": "one_line",
    "主欲望": "desire",
    "骨架（起点 → 中点反转 → 终点）": "skeleton",
    "配内核": "pairs_core",
    "身份": "identity",
    "表面": "surface",
    "缺陷（可演）": "flaw",
    "秘密": "secret",
    "槽位": "slot",
    "与主角的关系": "relation",
    "不可做": "never_do",
    "冲突": "conflict",
    "层级": "layer",
    "怎么演（具体动作）": "staging",
    "3 秒画面": "visual",
    "必配": "requires_desc",
    "结局（怎么收 + 代价）": "ending",
    "情绪": "emotion",
}

TAG_RE = re.compile(r"\b(CLA|FAM|REL|CRI|SEX|MON|IDE|OCC|VIO|SUP)\b")
CODE_RE = re.compile(r"^([A-H])(\d{2})$")


def parse_md_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """抽出所有 Markdown 表格 -> [(headers, rows)]"""
    tables, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):  # 分隔行
                continue
            cur.append(cells)
        else:
            if len(cur) >= 2:
                tables.append((cur[0], cur[1:]))
            cur = []
    if len(cur) >= 2:
        tables.append((cur[0], cur[1:]))
    return tables


def split_tags(raw: str) -> list[str]:
    return TAG_RE.findall(raw or "")


def parse_arc_structure_types(text: str) -> dict[str, str]:
    """C 模块末尾的『结构类型分布』表：上升/下降/反转/交易/对撞/群体"""
    out: dict[str, str] = {}
    for headers, rows in parse_md_tables(text):
        if not headers or headers[0] != "类型":
            continue
        for row in rows:
            if len(row) < 2:
                continue
            kind = row[0]
            for code in re.findall(r"C\d{2}", row[1]):
                out[code] = kind
    return out


def build_picker(src: Path, market: str) -> list[dict]:
    pdir = src / market.lower() / "picker"
    if not pdir.is_dir():
        return []
    items: list[dict] = []
    # 文件名形如 01-city.md / 03-arc.md，序号 01..08 对应模块 A..H
    order = "ABCDEFGH"
    for f in sorted(pdir.glob("[0-9][0-9]-*.md")):
        try:
            idx = int(f.name[:2]) - 1
            letter = order[idx]
        except (ValueError, IndexError):
            continue
        if letter not in PICKER_MODULES:
            continue
        type_code, type_key, type_cn = PICKER_MODULES[letter]
        text = f.read_text(encoding="utf-8")
        structure = parse_arc_structure_types(text) if letter == "C" else {}

        for headers, rows in parse_md_tables(text):
            if not headers or headers[0] != "编号":
                continue
            for row in rows:
                code = row[0].strip()
                m = CODE_RE.match(code)
                if not m or m.group(1) != letter:
                    continue
                attrs: dict = {}
                tags: list[str] = []
                for h, cell in zip(headers[1:], row[1:]):
                    if h == "标签":
                        tags = split_tags(cell)
                        continue
                    key = HEADER_MAP.get(h)
                    if key is None:
                        continue
                    if key in ("desire", "emotion", "slot", "layer"):
                        attrs[key] = cell
                        continue
                    attrs[key] = cell
                # 有的模块把标签列写成「标签」以外的名字，兜底再扫一遍
                if not tags:
                    tags = split_tags(row[-1])

                item = {
                    "id": f"{market}-{type_code}-{code}",
                    "type": type_key,
                    "type_cn": type_cn,
                    "market": market,
                    "layer": "L2" if type_code in ("COR", "ARC", "CHR", "SPT", "CFT", "END", "HOK") else "L1",
                    "name": (
                        attrs.get("place")
                        or attrs.get("identity")
                        or attrs.get("conflict")
                        or attrs.get("one_line")
                        or attrs.get("skeleton")
                        or attrs.get("ending")
                        or attrs.get("visual")
                        or code
                    ),
                    "tags": tags,
                    "attrs": attrs,
                    "source": f"picker/{f.name}#{code}",
                }
                # picker 的 G 模块全是开场钩，补上 kind，否则按 kind 筛选时会被漏掉
                if type_key == "hook" and letter == "G":
                    item["attrs"]["kind"] = "开场钩"
                if code in structure:
                    item["attrs"]["structure_type"] = structure[code]
                if "pairs_core" in attrs:
                    item["attrs"]["pairs_core"] = re.findall(r"B\d{2}", attrs["pairs_core"])
                items.append(item)
    return items


def _norm_main(raw: dict, market: str, type_key: str, type_code: str, cn: str, layer: str) -> dict:
    """主库 JSON 条目 -> 统一要素结构。保留原始字段在 attrs 里，不做信息丢失。"""
    eid = raw["id"]
    tags: list[str] = []
    for k in ("class", "cat", "kind", "role", "pkg"):
        v = raw.get(k)
        if isinstance(v, str) and v in (
            "老钱", "新钱", "中产", "工薪", "贫困", "混合",
            "身份符号", "情感信物", "剧情道具", "生活质感",
            "当众打脸", "身份揭穿", "反向示弱", "沉默碾压", "错位信息", "追妻火葬场", "打脸升级",
        ):
            tags.append(v)
    attrs = {k: v for k, v in raw.items() if k not in ("id", "name", "name_local", "local", "s_level", "s")}
    return {
        "id": eid,
        "type": type_key,
        "type_cn": cn,
        "market": market,
        "layer": layer,
        "name": raw.get("name", eid),
        "name_local": raw.get("name_local") or raw.get("local") or "",
        "s_level": raw.get("s_level") or raw.get("s") or "S1",
        "tags": tags,
        "attrs": attrs,
        "source": f"{market.lower()}/{attrs.get('_file', '')}".rstrip("/"),
    }


def build_main(src: Path, market: str) -> list[dict]:
    """主库 JSON：场景 / 物品 / 钩子 / 爽点 / 人设"""
    d = src / market.lower()
    out: list[dict] = []

    p = d / "02-scenes-objects.json"
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        for sc in data.get("scenes", []):
            sc["_file"] = "02-scenes-objects.json"
            item = _norm_main(sc, market, "scene", "SCN", "场景", "L1")
            item["attrs"]["cast_max"] = sc.get("cast")
            item["attrs"]["ai_difficulty"] = (
                "高" if (sc.get("cast") or 1) >= 4 else ("中" if (sc.get("cast") or 1) == 3 else "低")
            )
            item["tags"] = list({t for t in item["tags"] + ([sc.get("class")] if sc.get("class") else [])})
            out.append(item)
        for ob in data.get("objects", []):
            ob["_file"] = "02-scenes-objects.json"
            item = _norm_main(ob, market, "prop", "OBJ", "物品", "L1")
            item["tags"] = [ob.get("cat", "")]
            out.append(item)

    p = d / "03-hooks-beats.json"
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        for grp, kind in (("hooks_open", "开场钩"), ("hooks_cliff", "集尾悬念"), ("hooks_turn", "转折钩")):
            for hk in data.get(grp, []):
                hk["_file"] = "03-hooks-beats.json"
                hk["kind"] = kind
                item = _norm_main(hk, market, "hook", "HOK", "钩子", "L2")
                item["attrs"]["required_refs"] = hk.get("need", [])
                out.append(item)
        for bt in data.get("beats", []):
            bt["_file"] = "03-hooks-beats.json"
            item = _norm_main(bt, market, "beat", "BEAT", "爽点", "L2")
            item["attrs"]["required_refs"] = bt.get("need", [])
            out.append(item)

    p = d / "04-characters.json"
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        for ch in data.get("characters", []):
            ch["_file"] = "04-characters.json"
            item = _norm_main(ch, market, "character", "CHR", "人设卡", "L2")
            item["tags"] = [ch.get("role", "")]
            item["attrs"]["required_refs"] = re.findall(
                r"MX-[A-Z]{3}-\d{3}", ch.get("visual_symbol", "") or ""
            )
            out.append(item)

    return out


# 组合包 -> 剧情内核的人工推荐。
#
# **只标 core，不标 arc。** arc 由 `arc.attrs.pairs_core` 反查生成——
# 配对关系的唯一事实源是素材库自己标的「配内核」，手写第二份必然错位。
# （曾经手写过，8 个包里有 4 个 core 与 arc 对不上，随机补齐时就报 IN_PAIRING。）
PACKAGE_PRESETS = {
    1: {"cores": ["B03", "B01", "B29"],
        "note": "库作者验证过的组合（README 示例一）"},
    2: {"cores": ["B24", "B09", "B21"],
        "note": "「想离开的人」角度，避开毒枭美化"},
    3: {"cores": ["B07", "B17"],
        "note": "为签证结婚，本地共识，动机不用解释"},
    4: {"cores": ["B13", "B27"],
        "note": "本土最强差异化；仪式细节必须当地人复核"},
    5: {"cores": ["B10", "B16"],
        "note": "服化道成本最高，场景缺口 5 个"},
    6: {"cores": ["B23", "B20"],
        "note": "成本最低的一包：孩子 + 室内 + 少道具"},
    7: {"cores": ["B14", "B19", "B17"],
        "note": "身份悬疑优于案件悬疑，这是本地偏好"},
    8: {"cores": ["B01", "B25"],
        "note": "缺口大但补起来便宜（片场都是现代室内）"},
}


def build_genres(src: Path, market: str) -> list[dict]:
    """01-genres.md §一：45 个平台标签映射（A/B/C/D 四级）。

    不进蓝图槽位，只作为「题材」层的说明与风险标记。
    """
    f = src / market.lower() / "01-genres.md"
    if not f.is_file():
        return []
    out: list[dict] = []
    for headers, rows in parse_md_tables(f.read_text(encoding="utf-8")):
        if not headers or headers[0] != "#" or "标签" not in headers:
            continue
        for row in rows:
            if len(row) < 5 or not row[0].strip().isdigit():
                continue
            n = int(row[0].strip())
            grade = row[3].replace("*", "").strip()
            out.append({
                "id": f"{market}-GEN-{n:03d}",
                "type": "genre",
                "type_cn": "平台题材标签",
                "market": market,
                "layer": "L2",
                "name": row[1].strip(),
                "tags": [g for g in ("A", "B", "C", "D") if g in grade],
                "attrs": {
                    "label": row[1].strip(),
                    "local": row[2].strip().strip("`"),
                    "grade": grade,
                    "note": row[4].strip(),
                },
                "source": f"01-genres.md#标签{n}",
            })
    return out


def core_to_arcs(elements: list[dict]) -> dict[str, list[str]]:
    """core 短码 -> 声明了配它的 arc 短码。以素材库标注为唯一事实。"""
    rev: dict[str, list[str]] = {}
    for e in elements:
        if e.get("type") != "arc":
            continue
        code = e["id"].split("-")[-1]
        for b in (e.get("attrs", {}).get("pairs_core") or []):
            rev.setdefault(b, []).append(code)
    return rev


def build_packages(src: Path, market: str, arcs_rev: dict[str, list[str]] | None = None) -> list[dict]:
    """01-genres.md §三：8 个可直接开工的组合包。

    「题材」就是这一层——用户先选它，后面的 core/arc/人设都被它收窄。
    """
    f = src / market.lower() / "01-genres.md"
    if not f.is_file():
        return []
    text = f.read_text(encoding="utf-8")

    # 可拍性结论来自 09-package-check.md 的总览表
    readiness: dict[int, dict] = {}
    chk = src / market.lower() / "09-package-check.md"
    if chk.is_file():
        for headers, rows in parse_md_tables(chk.read_text(encoding="utf-8")):
            if not headers or headers[0] != "包":
                continue
            for row in rows:
                try:
                    no = int(row[0].strip())
                except (ValueError, IndexError):
                    continue
                gaps = re.sub(r"\D", "", row[3]) if len(row) > 3 else "0"
                readiness[no] = {
                    "coverage": row[2].strip() if len(row) > 2 else "",
                    "gaps": int(gaps or 0),
                    "shootability": re.sub(r"[✅⚠️️]", "", row[4]).strip() if len(row) > 4 else "",
                    "ready": (len(row) > 4 and "✅" in row[4]),
                }

    out: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^###\s*包\s*(\d+)｜(.+?)(?:（(.+?)）)?$", s)
        if m:
            if cur:
                out.append(cur)
            no = int(m.group(1))
            preset = PACKAGE_PRESETS.get(no, {})
            codes = preset.get("cores", [])
            preset_core_ids = [f"{market}-COR-{c}" for c in codes]
            # 反查：每个 core 声明配对它的 arc，天然一致，不会错位
            arc_codes: list[str] = []
            for c in codes:
                for ac in (arcs_rev or {}).get(c, []):
                    if ac not in arc_codes:
                        arc_codes.append(ac)
            preset_arc_ids = [f"{market}-ARC-{c}" for c in arc_codes]
            cur = {
                "id": f"{market}-PKG-{no:03d}",
                "type": "package",
                "type_cn": "题材组合包",
                "market": market,
                "layer": "L2",
                "name": m.group(2).strip(),
                "tags": [],
                "attrs": {
                    "no": no,
                    "remark": (m.group(3) or "").strip(),
                    "labels": [],
                    "sellpoint": "",
                    "core_hook": "",
                    "scenes": [],
                    "props": [],
                    "risk": "",
                    "advantage": "",
                    "preset_cores": preset_core_ids,
                    "preset_arcs": preset_arc_ids,
                    "preset_note": preset.get("note", ""),
                    "readiness": readiness.get(no, {}),
                },
                "source": f"01-genres.md#包{no}",
            }
            continue
        if cur is None:
            continue
        # 标签行：`替身 + 豪门 + 虐恋 + 复仇 + 伦理`
        if s.startswith("`") and "+" in s and not cur["attrs"]["labels"]:
            cur["attrs"]["labels"] = [x.strip() for x in s.strip("`").split("+") if x.strip()]
            cur["tags"] = list(cur["attrs"]["labels"])
            continue
        mb = re.match(r"^-\s*\*\*(.+?)\*\*[：:]\s*(.+)$", s)
        if mb:
            k, v = mb.group(1).strip(), mb.group(2).strip()
            if k == "卖点":
                cur["attrs"]["sellpoint"] = v
            elif k in ("核心钩子", "改写"):
                cur["attrs"]["core_hook"] = (cur["attrs"]["core_hook"] + " " + v).strip()
            elif k in ("投放钩", "投放"):
                cur["attrs"]["core_hook"] = (cur["attrs"]["core_hook"] + " 投放钩：" + v).strip()
            elif k == "场景":
                cur["attrs"]["scenes"] = re.findall(r"MX-SCN-\d{3}", v)
            elif k == "物品":
                cur["attrs"]["props"] = [x.strip() for x in re.split(r"[、,，]", v) if x.strip()]
            elif k in ("风险", "注意"):
                cur["attrs"]["risk"] = (cur["attrs"]["risk"] + " " + v).strip()
            elif k in ("优势",):
                cur["attrs"]["advantage"] = v
    if cur:
        out.append(cur)

    for p in out:
        p["attrs"]["ready"] = bool(p["attrs"]["readiness"].get("ready"))
        p["attrs"]["gaps"] = p["attrs"]["readiness"].get("gaps", 0)
    return out


def build_market(src: Path, market: str) -> dict:
    """L0 底座：从 00-market.md 抽出结构性字段 + 红线表"""
    f = src / market.lower() / "00-market.md"
    market_obj = {
        "id": f"{market}-MKT-001",
        "type": "market",
        "type_cn": "市场底座",
        "market": market,
        "layer": "L0",
        "name": market,
        "tags": [],
        "attrs": {"red_lines": [], "facts": {}, "unique_assets": []},
        "source": f"{market.lower()}/00-market.md",
    }
    if not f.is_file():
        return market_obj
    text = f.read_text(encoding="utf-8")
    for headers, rows in parse_md_tables(text):
        if headers and headers[0] == "禁区":
            market_obj["attrs"]["red_lines"] = [
                {"name": r[0], "note": r[1] if len(r) > 1 else ""} for r in rows
            ]
        elif headers and headers[0] == "项":
            market_obj["attrs"]["facts"] = {r[0]: (r[1] if len(r) > 1 else "") for r in rows}
        elif headers and headers[0] == "素材":
            market_obj["attrs"]["unique_assets"] = [
                {"name": r[0], "why": r[1] if len(r) > 1 else ""} for r in rows
            ]
    return market_obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="MX")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        print(f"[FATAL] 素材库不存在: {src}", file=sys.stderr)
        return 2

    market = args.market.upper()
    picker_items = build_picker(src, market)
    elements = (picker_items + build_main(src, market)
                + build_genres(src, market) + build_packages(src, market, core_to_arcs(picker_items)))
    mk = build_market(src, market)

    # 去重 + 稳定排序
    seen, uniq = set(), []
    for e in elements:
        if e["id"] in seen:
            print(f"[WARN] 重复 ID 已丢弃: {e['id']}", file=sys.stderr)
            continue
        seen.add(e["id"])
        uniq.append(e)
    uniq.sort(key=lambda x: (x["type"], x["id"]))

    by_type: dict[str, int] = {}
    for e in uniq:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    pack = {
        "schema_version": "1.0",
        "market": market,
        "generated_from": str(src),
        "market_base": mk,
        "counts": by_type,
        "elements": uniq,
    }
    outdir = ROOT / "data" / "elements"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{market}.elements.json"
    out.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[OK] {out}  共 {len(uniq)} 条")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"     {t:10s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
