#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口。不起服务也能干活。

  python cli.py list                                  # 看要素库有什么
  python cli.py validate -b examples/blueprint-bag1.json
  python cli.py plan     -b examples/blueprint-bag1.json
  python cli.py prompt   -b examples/blueprint-bag1.json -e 1
  python cli.py gen      -b examples/blueprint-bag1.json -m template -r 1-6 -o out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import pipeline                     # noqa: E402
from core.errors import SGError               # noqa: E402
from core.models import Blueprint, Library    # noqa: E402

ROOT = Path(__file__).resolve().parent


def _bp(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"蓝图文件不存在: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"蓝图不是合法 JSON: {e}") from e


def cmd_list(args) -> int:
    cat = pipeline.catalog(args.market)
    print(f"市场 {cat['market']} · 共 {sum(cat['counts'].values())} 条要素")
    for k, v in sorted(cat["counts"].items(), key=lambda x: -x[1]):
        print(f"  {k:10s} {v}")
    print("\n蓝图槽位：")
    for s in cat["slots"]:
        print(f"  {s['key']:10s} {s['cn']:8s} {'选 ' + str(s['pick'] or s['min_pick']) + ' 项':8s} {s['hint']}")
    print("\n风格包：")
    for s in cat["styles"]:
        print(f"  {s['id']:16s} {s['name']}  「{' → '.join(s['segments'])}」")
    print("\n模型：" + json.dumps(cat["llm"], ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    bp = Blueprint.from_dict(_bp(args.bp))
    lib = Library.load(bp.meta.get("market", "MX"))
    issues = bp.validate(lib)
    if not issues:
        print("✓ 校验通过，无问题")
    for i in issues:
        mark = "✗" if i["level"] == "error" else "!"
        print(f"{mark} [{i['code']}] {i['message']}" + (f"\n    → {i['hint']}" if i.get("hint") else ""))
    bad = [i for i in issues if i["level"] == "error"]
    return 1 if bad else 0


def cmd_plan(args) -> int:
    est = pipeline.estimate(_bp(args.bp))
    print(f"集数 {est['episodes']} · 组装卡 {est['cards']} · 调用 {est['calls_min']}–{est['calls_max']} 次")
    print(f"预估输入 {est['input_tokens']:,} tokens · 输出 {est['output_tokens']:,} tokens")
    print("\n五段结构：")
    for s in est["segments"]:
        print(f"  {s['name']:8s} E{s['start']}-{s['end']:<3d} {s['goal']}")
    print("\n爽点梯度：" + " ".join(f"{v}" for v in est["beat_ladder"]))
    print("\n成本：" + json.dumps(est["cost_summary"], ensure_ascii=False))
    return 0


def cmd_prompt(args) -> int:
    r = pipeline.preview_prompt(_bp(args.bp), args.episode)
    print(f"=== 第 {r['episode']} 集 · 约 {r['est_tokens']} tokens ===\n")
    print("--- SYSTEM ---")
    print(r["system"])
    print("\n--- USER ---")
    print(r["user"])
    return 0


def _parse_range(s: str | None) -> list[int] | None:
    if not s:
        return None
    out: list[int] = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                out += list(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return sorted(set(out)) or None


def cmd_gen(args) -> int:
    def prog(ev: dict):
        if args.quiet:
            return
        bar = "#" * int(ev.get("pct", 0) / 4)
        sys.stderr.write(f"\r[{bar:<25}] {ev.get('pct', 0):3d}% {ev.get('message', '')[:50]:<50}")
        sys.stderr.flush()

    s = pipeline.run(_bp(args.bp), mode=args.mode, episodes=_parse_range(args.range),
                     workers=args.workers, progress=prog,
                     export=not args.no_export, outdir=args.out)
    if not args.quiet:
        sys.stderr.write("\n")
    print(f"生成 {len(s['episodes'])} 集 · {s['checks']['errors']} 阻断 / {s['checks']['warnings']} 提醒")
    for i in s["checks"]["issues"]:
        print(f"  {'✗' if i['level'] == 'error' else '!'} [{i['code']}] {i['message']}")
    if s.get("failures"):
        print("失败集：")
        for f in s["failures"]:
            print(f"  E{f['episode']}: [{f.get('code')}] {f.get('message')}")
    for k, p in (s.get("files") or {}).items():
        print(f"  {k:9s} {p}")
    return 0 if s["checks"]["passed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="结构化要素剧本生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出要素库 / 槽位 / 风格")
    p.add_argument("-m", "--market", default="MX")
    p.set_defaults(fn=cmd_list)

    for name, fn, help_ in (
        ("validate", cmd_validate, "校验蓝图"),
        ("plan", cmd_plan, "编译预览（不调模型）"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("-b", "--bp", required=True, help="蓝图 JSON 路径")
        p.set_defaults(fn=fn)

    p = sub.add_parser("prompt", help="打印某一集的完整 prompt")
    p.add_argument("-b", "--bp", required=True)
    p.add_argument("-e", "--episode", type=int, default=1)
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("gen", help="生成剧本")
    p.add_argument("-b", "--bp", required=True)
    p.add_argument("-m", "--mode", default="auto", choices=["auto", "llm", "template"])
    p.add_argument("-r", "--range", help="1-6 / 1,3,5 / 留空=全部")
    p.add_argument("-o", "--out", default=str(ROOT / "out"))
    p.add_argument("-w", "--workers", type=int, default=4)
    p.add_argument("--no-export", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(fn=cmd_gen)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except SGError as e:
        print(f"\n✗ [{e.code}] {e.message}" + (f"\n  → {e.hint}" if e.hint else ""), file=sys.stderr)
        for i in (e.ctx.get("issues") or []):
            print(f"    · {i['message']}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
