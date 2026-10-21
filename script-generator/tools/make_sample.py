#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从完整要素包里抽一个「示例包」，给开源仓库用。

完整素材库不随源码公开（那是核心资产），仓库里只放能跑通的最小集。
抽样规则：题材包连同它推荐的 core/arc 一起带，保证引用闭合、一 clone 就能出稿。

  python tools/make_sample.py                 # 生成 MX.elements.sample.json
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "elements" / "MX.elements.json"
DST = ROOT / "data" / "elements" / "MX.elements.sample.json"

KEEP_PACKAGES = ["MX-PKG-001", "MX-PKG-006"]      # 顶替新娘 / 抱错孩子，都是 0 缺口
KEEP = {
    "character": ["MX-CHR-D01", "MX-CHR-D03", "MX-CHR-D06", "MX-CHR-D11",
                  "MX-CHR-D17", "MX-CHR-D23"],
    "support": ["MX-SPT-E01", "MX-SPT-E02", "MX-SPT-E05", "MX-SPT-E06",
                "MX-SPT-E16", "MX-SPT-E18", "MX-SPT-E20", "MX-SPT-E22"],
    "conflict": ["MX-CFT-F01", "MX-CFT-F03", "MX-CFT-F04", "MX-CFT-F05",
                 "MX-CFT-F09", "MX-CFT-F11", "MX-CFT-F13", "MX-CFT-F16",
                 "MX-CFT-F19", "MX-CFT-F22"],
    "ending": ["MX-END-H01", "MX-END-H03", "MX-END-H06", "MX-END-H15",
               "MX-END-H16", "MX-END-H21"],
    "city": ["MX-LOC-A01", "MX-LOC-A03", "MX-LOC-A11", "MX-LOC-A15",
             "MX-LOC-A20", "MX-LOC-A30"],
    "scene": ["MX-SCN-001", "MX-SCN-002", "MX-SCN-003", "MX-SCN-004",
              "MX-SCN-008", "MX-SCN-009", "MX-SCN-012", "MX-SCN-014",
              "MX-SCN-021", "MX-SCN-026", "MX-SCN-031", "MX-SCN-034"],
    "prop": ["MX-OBJ-001", "MX-OBJ-002", "MX-OBJ-004", "MX-OBJ-006",
             "MX-OBJ-007", "MX-OBJ-008", "MX-OBJ-010", "MX-OBJ-018",
             "MX-OBJ-019", "MX-OBJ-021", "MX-OBJ-022", "MX-OBJ-049"],
}
# 钩子 / 爽点按「够用」抽：钩子三类各 4，爽点覆盖 1–10 刻度
HOOK_TAKE = 4
BEAT_TAKE = 14


def main() -> int:
    if not SRC.is_file():
        print(f"[FATAL] 完整要素包不存在: {SRC}")
        return 2
    pack = json.loads(SRC.read_text(encoding="utf-8"))
    els = pack["elements"]
    by_id = {e["id"]: e for e in els}

    keep_ids: set[str] = set(KEEP_PACKAGES)
    for v in KEEP.values():
        keep_ids |= set(v)

    # 题材包推荐的 core/arc 必须一起带，否则自动补齐抽不到
    for pid in KEEP_PACKAGES:
        pkg = by_id.get(pid)
        if not pkg:
            continue
        keep_ids |= set(pkg["attrs"].get("preset_cores") or [])
        keep_ids |= set(pkg["attrs"].get("preset_arcs") or [])

    # 示例蓝图里引用的 ID 一律保底保留 —— README 的第一条命令要能直接跑通
    bp_path = ROOT / "examples" / "blueprint-bag1.json"
    if bp_path.is_file():
        import re
        bp_txt = bp_path.read_text(encoding="utf-8")
        keep_ids |= {m for m in re.findall(r"MX-[A-Z]{3}-[A-Z]?\d{2,3}", bp_txt)}

    # 钩子：三类各取前 N 个（含类型内的开场钩）
    for kind in ("开场钩", "集尾悬念", "转折钩"):
        got = 0
        for e in els:
            if e["type"] != "hook" or e.get("attrs", {}).get("kind") != kind:
                continue
            keep_ids.add(e["id"])
            got += 1
            if got >= HOOK_TAKE:
                break

    # 爽点：按刻度均匀取，保证梯度算法有得选
    beats = sorted((e for e in els if e["type"] == "beat"),
                   key=lambda e: (e["attrs"].get("lvl") or 0, e["id"]))
    if beats:
        step = max(1, len(beats) // BEAT_TAKE)
        for e in beats[::step][:BEAT_TAKE]:
            keep_ids.add(e["id"])

    out_els = [e for e in els if e["id"] in keep_ids]
    missing = sorted(keep_ids - {e["id"] for e in out_els})
    if missing:
        print(f"[WARN] 以下 ID 在完整库里不存在，已跳过: {missing}")

    counts: dict[str, int] = {}
    for e in out_els:
        counts[e["type"]] = counts.get(e["type"], 0) + 1

    sample = {
        "schema_version": pack.get("schema_version", "1.0"),
        "market": pack.get("market", "MX"),
        "generated_from": "示例包（完整素材库不随源码公开）",
        "market_base": pack.get("market_base", {}),
        "counts": counts,
        "elements": out_els,
    }
    DST.write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[OK] {DST.name}  共 {len(out_els)} 条")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"     {k:10s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
