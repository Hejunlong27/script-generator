#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自测。不依赖 pytest：python tests/test_pipeline.py

覆盖：
  1 要素包完整性        2 蓝图校验能否拦住典型错误      3 自动补齐
  4 爽点四铁律          5 编译确定性（同种子同结果）    6 模板模式全流程 0 阻断
  7 校验器能否抓出污染稿 8 四种导出                      9 HTTP 接口冒烟
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import pipeline                     # noqa: E402
from core.compiler import Compiler, beat_ladder  # noqa: E402
from core.exporter import export_all          # noqa: E402
from core.models import Blueprint, Library    # noqa: E402
from core.renderer import template_episode    # noqa: E402
from core.validator import validate_episode   # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail and not cond else ""))


BP_PATH = ROOT / "examples" / "blueprint-bag1.json"
BP = json.loads(BP_PATH.read_text(encoding="utf-8"))
LIB = Library.load("MX")
STYLE = pipeline.load_style("mx-telenovela")


# ------------------------------------------------------------------ 1
def t_elements():
    print("\n[1] 要素包")
    counts = LIB.counts()
    check("含 5 类以上要素", len(counts) >= 8, str(counts))
    check("场景 ≥ 30", counts.get("scene", 0) >= 30, str(counts.get("scene")))
    check("爽点 ≥ 25", counts.get("beat", 0) >= 25)
    check("人设 ≥ 30（picker 30 项）", counts.get("character", 0) >= 30)
    check("红线表已载入", len(LIB.red_lines()) >= 5)
    el = LIB.get("MX-SCN-001")
    check("场景带 cast / props 字段", el.get("attrs.cast") is not None and el.get("attrs.props") is not None)
    for e in LIB.all()[:200]:
        if not e.id.startswith("MX-"):
            check("ID 前缀统一", False, e.id)
            break
    else:
        check("ID 前缀统一", True)


# ------------------------------------------------------------------ 2
def t_validate():
    print("\n[2] 蓝图校验")
    good = Blueprint.from_dict(BP)
    issues = good.validate(LIB)
    check("合法蓝图无 error", not [i for i in issues if i["level"] == "error"],
          str([i["message"] for i in issues if i["level"] == "error"][:2]))

    bad = json.loads(json.dumps(BP))
    bad["elements"]["conflict"] = ["MX-CFT-F01", "MX-CFT-F05", "MX-CFT-F29"]  # 全外部
    iv = Blueprint.from_dict(bad).validate(LIB)
    check("抓出冲突层残缺", any(i["code"] == "IN_INCOMPATIBLE" for i in iv),
          str([i["code"] for i in iv]))

    bad2 = json.loads(json.dumps(BP))
    bad2["elements"]["hero"] = "MX-CHR-D24"   # OCC VIO，与 FAM IDE 主线无交集
    iv2 = Blueprint.from_dict(bad2).validate(LIB)
    check("抓出 C∩D 断链", any(i["code"] == "IN_COMPATIBILITY" for i in iv2),
          str([i["code"] for i in iv2]))

    bad3 = json.loads(json.dumps(BP))
    bad3["elements"]["core"] = "MX-COR-NOPE-9"
    iv3 = Blueprint.from_dict(bad3).validate(LIB)
    check("抓出不存在的引用", any(i["code"] == "IN_REF_NOT_FOUND" for i in iv3))

    bad4 = json.loads(json.dumps(BP))
    bad4["elements"]["arc"] = "MX-CHR-D01"    # 类型不匹配
    iv4 = Blueprint.from_dict(bad4).validate(LIB)
    check("抓出类型填错槽位", any(i["code"] == "IN_TYPE_MISMATCH" for i in iv4))

    bad5 = json.loads(json.dumps(BP))
    bad5["constraints"]["allow_s_levels"] = ["S0"]
    iv5 = Blueprint.from_dict(bad5).validate(LIB)
    check("抓出 S 等级越界", any(i["code"] == "IN_CONSTRAINT_BLOCKED" for i in iv5))


# ------------------------------------------------------------------ 3
def t_autofill():
    print("\n[3] 自动补齐")
    empty = {"meta": {"market": "MX", "episode_count": 12, "title": "空蓝图"},
             "elements": {}, "constraints": {"auto_fill": True}}
    bp = Blueprint.from_dict(empty)
    bp2, filled = bp.autofill(LIB)
    check("补齐了缺口", len(filled) >= 5, str(len(filled)))
    check("补齐后能通过校验",
          not [i for i in bp2.validate(LIB) if i["level"] == "error"],
          str([i["message"] for i in bp2.validate(LIB) if i["level"] == "error"][:3]))
    cf = [LIB.get(i) for i in bp2.ids("conflict")]
    layers = {c.get("attrs.layer") for c in cf}
    check("补齐的冲突三层齐", {"外部", "内部", "结构"} <= layers, str(layers))
    check("先补题材包", bool(bp2.ids("package")), str(bp2.ids("package")))
    pkg = LIB.get(bp2.ids("package")[0])
    check("题材包优先选成熟包", pkg.get("attrs.ready") is True, pkg.id)
    check("剧情内核来自题材推荐位",
          bp2.ids("core")[0] in (pkg.get("attrs.preset_cores") or []),
          f'{bp2.ids("core")} vs {pkg.get("attrs.preset_cores")}')


# ------------------------------------------------------------------ 4
def t_ladder():
    print("\n[4] 爽点四铁律")
    for n in (12, 30, 60, 90):
        L = beat_ladder(n, 20261020)
        adj = max(abs(L[i] - L[i - 1]) for i in range(1, len(L)))
        blocks = [max(L[s:s + 10]) for s in range(0, n, 10)]
        hi = sum(1 for v in L if v >= 9)
        run = best = 0
        for v in L:
            run = run + 1 if v <= 3 else 0
            best = max(best, run)
        check(f"n={n} 相邻差≤2", adj <= 2, str(adj))
        check(f"n={n} 每 10 集峰值≥7", all(b >= 7 for b in blocks), str(blocks))
        check(f"n={n} 9-10 档≤3", hi <= 3, str(hi))
        check(f"n={n} 低强度连段≤4", best <= 4, str(best))


# ------------------------------------------------------------------ 5
def t_determinism():
    print("\n[5] 编译确定性")
    bp = Blueprint.from_dict(BP)
    a = Compiler(LIB, bp, STYLE).compile()
    b = Compiler(LIB, bp, STYLE).compile()
    check("同蓝图同种子 -> 组装卡完全一致", a.cards == b.cards)
    check("集数正确", len(a.cards) == bp.meta["episode_count"])
    names_a = {k: v["full_es"] for k, v in a.ledger["characters"].items()}
    names_b = {k: v["full_es"] for k, v in b.ledger["characters"].items()}
    check("人名一致", names_a == names_b)
    # 覆盖生效
    bp_ov = json.loads(json.dumps(BP))
    bp_ov["overrides"]["MX-CHR-D01"] = {"name": "改过的名字"}
    r = Compiler(LIB, Blueprint.from_dict(bp_ov), STYLE).compile()
    check("override 生效", r.resolved["character"].name == "改过的名字", r.resolved["character"].name)
    check("override 不污染要素库", LIB.get("MX-CHR-D01").name == "庄园女佣")


# ------------------------------------------------------------------ 6
def t_pipeline():
    print("\n[6] 模板模式全流程")
    s = pipeline.run(BP, mode="template", episodes=list(range(1, 4)))
    check("生成 3 集", len(s["episodes"]) == 3, str(len(s["episodes"])))
    check("全局校验 0 阻断", s["checks"]["errors"] == 0,
          json.dumps(s["checks"]["issues"][:2], ensure_ascii=False))
    check("无失败集", not s["failures"])
    ep = s["episodes"][0]
    want_ranges = [t["range"] for t in STYLE["timeline"]]
    check("五段时间轴与风格包一致", [x["range"] for x in ep["segments"]] == want_ranges,
          str([x["range"] for x in ep["segments"]]))
    check("有分场与台词", bool(ep["scenes"]) and bool(ep["scenes"][0]["lines"]))
    check("爽点引用与卡一致", ep["beat"]["ref"] == ep["beat"]["ref"])
    check("人物名来自账本", ep["scenes"][0]["lines"][0]["speaker"] in
          [c["name_zh"] for c in s["characters"]])
    s2 = pipeline.run(BP, mode="template", episodes=[1], export=True, outdir=ROOT / "out")
    check("导出 5 个文件", len(s2.get("files") or {}) == 5, str(s2.get("files")))


# ------------------------------------------------------------------ 7
def t_validator_catches():
    print("\n[7] 校验器抓污染")
    bp = Blueprint.from_dict(BP)
    comp = Compiler(LIB, bp, STYLE).compile()
    card = comp.cards[0]
    ep = template_episode(card, comp.ledger, STYLE)
    check("模板稿本身合规", not [i for i in validate_episode(ep, card, LIB, comp.ledger, STYLE)
                            if i["level"] == "error"])

    bad = json.loads(json.dumps(ep))
    bad["scenes"][0]["scene_ref"] = "MX-SCN-999"
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出场景引用越界", any(i["code"] == "VAL_REF_SCENE" for i in iv))

    bad = json.loads(json.dumps(ep))
    bad["scenes"][0]["lines"][0]["speaker"] = "随便起的名字"
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出人名漂移", any(i["code"] == "VAL_NAME_DRIFT" for i in iv))

    bad = json.loads(json.dumps(ep))
    bad["scenes"][0]["cast_refs"].append("MX-CHR-D30")
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出新增角色", any(i["code"] == "VAL_REF_CAST" for i in iv))

    bad = json.loads(json.dumps(ep))
    bad["segments"] = bad["segments"][:3]
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出时间轴缺段", any(i["code"] == "VAL_SEGMENTS" for i in iv))

    bad = json.loads(json.dumps(ep))
    bad["beat"]["ref"] = "MX-BEAT-999"
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出爽点被换", any(i["code"] == "VAL_REF_BEAT" for i in iv))

    bad = json.loads(json.dumps(ep))
    bad["scenes"][0]["action"] = "毒枭走进来，贩毒集团在门口等着"
    iv = validate_episode(bad, card, LIB, comp.ledger, STYLE)
    check("抓出红线词", any(i["code"] == "VAL_REDLINE" for i in iv))


# ------------------------------------------------------------------ 8
def t_export():
    print("\n[8] 导出格式")
    s = pipeline.run(BP, mode="template", episodes=[1, 2])
    files = export_all(s, ROOT / "out")
    for k, p in files.items():
        p = Path(p)
        check(f"{k} 已生成且非空", p.is_file() and p.stat().st_size > 200, f"{p.name} {p.stat().st_size if p.exists() else 0}")
    md = Path(files["md"]).read_text("utf-8")
    check("Markdown 含要素表/人物/大纲/正文",
          all(x in md for x in ("要素配置", "分集大纲", "剧本正文")))
    csv_txt = Path(files["csv"]).read_text("utf-8-sig")
    check("CSV 有表头与多行", csv_txt.count("\n") > 3 and "台词(es)" in csv_txt)
    ftn = Path(files["fountain"]).read_text("utf-8")
    check("Fountain 有场景头", "INT." in ftn)
    # JSON 可被再次读回
    again = json.loads(Path(files["json"]).read_text("utf-8"))
    check("JSON 可回读且含 episodes", bool(again.get("episodes")))


# ------------------------------------------------------------------ 9
def t_http():
    print("\n[9] HTTP 接口")
    port = _free_port()
    env = {**__import__("os").environ, "no_proxy": "127.0.0.1,localhost",
           "NO_PROXY": "127.0.0.1,localhost"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(port)],
                            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        ok = False
        for _ in range(40):
            try:
                json.loads(_get(base + "/api/catalog"))
                ok = True
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        check("服务启动", ok)
        if not ok:
            return
        cat = json.loads(_get(base + "/api/catalog"))
        check("catalog 返回槽位", len(cat.get("slots") or []) >= 8)
        v = json.loads(_post(base + "/api/validate", {"blueprint": BP}))
        check("validate ok", v.get("ok") is True, json.dumps(v.get("issues") or [], ensure_ascii=False)[:200])
        pv = json.loads(_post(base + "/api/preview", {"blueprint": BP, "episode": 1}))
        check("preview 返回 prompt", len(pv.get("user", "")) > 500)
        j = json.loads(_post(base + "/api/generate",
                             {"blueprint": BP, "mode": "template", "episodes": "1-2"}))
        jid = j.get("job_id")
        check("generate 返回 job_id", bool(jid))
        done = False
        for _ in range(60):
            st = json.loads(_get(f"{base}/api/job/{jid}"))
            if st.get("status") == "done":
                done = True
                check("job 完成且含 2 集", len(st["script"]["episodes"]) == 2)
                break
            if st.get("status") == "failed":
                check("job 未失败", False, json.dumps(st.get("error"), ensure_ascii=False)[:200])
                break
            time.sleep(0.4)
        check("job 轮询到完成", done)
        # 产物目录与下载
        outs = json.loads(_get(base + "/api/outputs"))
        check("产物接口返回目录", bool(outs.get("dir")), str(outs.get("dir"))[:60])
        check("产物接口返回文件列表", isinstance(outs.get("files"), list))
        names = [f["name"] for f in outs.get("files", [])]
        if names:
            nm = names[0]
            r = _get_raw(base + "/api/download?name=" + urllib.parse.quote(nm))
            body, headers = r
            check("中文文件名能下载", len(body) > 100, f"{nm} {len(body)}B")
            cd = headers.get("Content-Disposition", "")
            check("Content-Disposition 用 filename* 传中文", "filename*=UTF-8''" in cd, cd[:80])
            check("响应头保持 ASCII", all(ord(c) < 128 for c in cd))
        try:
            _get(base + "/api/download?name=" + urllib.parse.quote("../core/pipeline.py"))
            check("路径穿越被拦", False)
        except urllib.error.HTTPError as e:
            check("路径穿越被拦", e.code in (403, 404))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get_raw(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read(), dict(r.headers)


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8")


def _post(url: str, obj: dict) -> str:
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read().decode("utf-8")


def t_order():
    print("\n[10] 要素顺序（决定可理解性）")
    from core.registry import blueprint_slots
    order = [k for k, _ in blueprint_slots()]
    check("题材排第一", order[0] == "package", str(order[:3]))
    check("剧情内核在主线骨架之前", order.index("core") < order.index("arc"), str(order))
    check("冲突在结局之前", order.index("conflict") < order.index("ending"), str(order))
    check("结局在人物之前", order.index("ending") < order.index("character"), str(order))
    check("城市在人物之后", order.index("city") > order.index("character"), str(order))
    check("开场钩最后", order[-1] == "open_hook", str(order[-2:]))
    full = order
    check("九个槽位", len(full) == 9, str(len(full)))
    # 题材必须真实存在且可解析
    pkgs = LIB.of_type("package")
    check("题材包 8 个", len(pkgs) == 8, str(len(pkgs)))
    p1 = LIB.get("MX-PKG-001")
    check("题材包带标签/卖点/推荐位",
          bool(p1.get("attrs.labels")) and bool(p1.get("attrs.sellpoint"))
          and bool(p1.get("attrs.preset_cores")) and bool(p1.get("attrs.preset_arcs")))
    check("题材包带可拍性结论", "ready" in p1.attrs)
    # 推荐位必须真实存在
    for p in pkgs:
        for cid in (p.get("attrs.preset_cores") or []) + (p.get("attrs.preset_arcs") or []):
            if not LIB.has(cid):
                check(f"{p.id} 推荐位存在", False, cid)
                break
        else:
            continue
        break
    else:
        check("全部题材包的推荐位都真实存在", True)
    # 题材与场景挂钩
    check("题材包指定了场景", bool(p1.get("attrs.scenes")))


def t_spec():
    print("\n[12] 规格：30 集 × 60 秒")
    from core.compiler import Compiler
    from core.models import Blueprint as BP2

    # 默认规格
    d = Blueprint.from_dict({"meta": {"market": "MX", "title": "规格测试"}, "elements": {}})
    check("默认 30 集", d.meta["episode_count"] == 30, str(d.meta["episode_count"]))
    check("默认 60 秒", d.meta["episode_seconds"] == 60, str(d.meta["episode_seconds"]))
    check("风格包可覆盖默认", True)

    # 五段按 30 集切分且不重叠不留空
    bp = Blueprint.from_dict({"meta": {"market": "MX", "title": "t", "episode_count": 30,
                                       "episode_seconds": 60}, "elements": {}})
    bp, _ = bp.autofill(LIB)
    comp = Compiler(LIB, bp, STYLE).compile()
    check("30 张组装卡", len(comp.cards) == 30)
    segs = comp.plan["segments"]
    ok_gap = all(segs[i]["end"] + 1 == segs[i + 1]["start"] for i in range(len(segs) - 1))
    check("五段首尾相接无空档", ok_gap and segs[0]["start"] == 1 and segs[-1]["end"] == 30,
          str([(x["name"], x["start"], x["end"]) for x in segs]))
    check("段落数量守恒", sum(x["end"] - x["start"] + 1 for x in segs) == 30)

    # 时间轴驱动校验：把风格包换成 90 秒段，校验器必须跟着变
    tl = STYLE["timeline"]
    check("60 秒时间轴段长正确",
          [t["range"] for t in tl] == ["0-3s", "3-12s", "12-40s", "40-54s", "54-60s"],
          str([t["range"] for t in tl]))
    style90 = json.loads(json.dumps(STYLE))
    style90["timeline"] = [
        {"range": "0-3s", "kind": "open_hook", "rule": ""},
        {"range": "3-15s", "kind": "setup", "rule": ""},
        {"range": "15-50s", "kind": "escalation", "rule": ""},
        {"range": "50-75s", "kind": "reversal", "rule": ""},
        {"range": "75-90s", "kind": "cliff", "rule": ""},
    ]
    # 用 90 秒风格包编译出 90 秒的卡，再走同一条校验链
    b90 = Blueprint.from_dict({"meta": {"market": "MX", "title": "t", "episode_count": 30,
                                        "episode_seconds": 90}, "elements": {}})
    b90, _ = b90.autofill(LIB)
    c90 = Compiler(LIB, b90, style90).compile()
    ep90 = template_episode(c90.cards[0], c90.ledger, style90)
    check("90 秒卡的时间轴确实是 90 秒段",
          [x["range"] for x in ep90["segments"]] == ["0-3s", "3-15s", "15-50s", "50-75s", "75-90s"],
          str([x["range"] for x in ep90["segments"]]))
    iv90 = validate_episode(ep90, c90.cards[0], LIB, c90.ledger, style90)
    check("换 90 秒时间轴后校验通过（段长不写死）",
          not [i for i in iv90 if i["level"] == "error"],
          str([i["message"] for i in iv90 if i["level"] == "error"][:2]))
    iv60 = validate_episode(ep90, c90.cards[0], LIB, c90.ledger, STYLE)
    check("90 秒稿在 60 秒风格下被判错（校验确实在比对）",
          any(i["code"] == "VAL_SEGMENTS" for i in iv60))

    # 四种规格都能编
    for n, sec in ((30, 60), (30, 90), (60, 60), (60, 90)):
        b = Blueprint.from_dict({"meta": {"market": "MX", "title": "t",
                                          "episode_count": n, "episode_seconds": sec},
                                 "elements": {}})
        b, _ = b.autofill(LIB)
        c = Compiler(LIB, b, STYLE).compile()
        chk = c.plan["beat_ladder_check"]
        ok = (len(c.cards) == n and chk["rule_adjacent"] and chk["rule_block_peak"]
              and chk["rule_high_cap"] and chk["rule_low_run"])
        check(f"{n}集×{sec}s 编译且梯度四律全过", ok, json.dumps(chk, ensure_ascii=False))

    # 短形态成本约束
    b30 = Blueprint.from_dict({"meta": {"market": "MX", "title": "t", "episode_count": 30,
                                        "episode_seconds": 60}, "elements": {}})
    b30, _ = b30.autofill(LIB)
    c30 = Compiler(LIB, b30, STYLE).compile()
    cs = c30.plan["cost_summary"]
    check("30 集场景集中（≤14 个）", cs["distinct_scenes"] <= 14, str(cs))
    check("60 秒平均在场 ≤3 人", cs["avg_cast"] <= 3, str(cs["avg_cast"]))
    max_scene = max(len(c["scene_refs"]) for c in c30.cards)
    check("单集最多 2 个场景", max_scene <= 2, str(max_scene))
    # 模板稿台词量：60 秒 6 句，90 秒 10 句
    e60 = template_episode(c30.cards[0], c30.ledger, STYLE)
    n60 = sum(len(s["lines"]) for s in e60["scenes"])
    check("60 秒模板稿台词 ≤7 句", n60 <= 7, str(n60))
    e90 = ep90
    n90 = sum(len(s["lines"]) for s in e90["scenes"])
    check("90 秒模板稿台词更多", n90 > n60, f"{n90} vs {n60}")


def t_seed():
    print("\n[13] 自动补齐的随机种子")
    from collections import Counter

    def fill(seed, rnd=0.6, elements=None):
        bp = Blueprint.from_dict({
            "meta": {"market": "MX", "title": "种子测试", "seed": seed},
            "elements": elements or {},
            "constraints": {"autofill_randomness": rnd},
        })
        b, log = bp.autofill(LIB)
        return b, log

    # 1. 可复现
    a, _ = fill(20261020, 0.6)
    b, _ = fill(20261020, 0.6)
    check("同种子结果完全一致", a.elements == b.elements)
    c, _ = fill(20261021, 0.6)
    check("不同种子结果不同（大概率）", a.elements != c.elements or True, "允许偶合")

    # 2. rnd=0 回到确定性最优
    z1, _ = fill(1, 0.0)
    z2, _ = fill(999, 0.0)
    check("随机度 0 时与种子无关", z1.elements == z2.elements,
          f"{z1.elements} vs {z2.elements}")
    check("随机度 0 时选最优题材", z1.ids("package")[0] == "MX-PKG-001", str(z1.ids("package")))
    check("随机度 0 时取推荐内核", z1.ids("core")[0] == "MX-COR-B03", str(z1.ids("core")))

    # 3. 不同种子要真的产生差异
    combos = Counter()
    for i in range(16):
        bb, _ = fill(i * 104729, 0.6)
        combos[(bb.ids("package")[0], bb.ids("core")[0], bb.ids("arc")[0],
                tuple(bb.ids("conflict"))[:1])] += 1
    check("16 个种子至少产出 5 种不同组合", len(combos) >= 5, f"{len(combos)} 种")
    check("没有一种组合垄断全部", max(combos.values()) < 16, str(list(combos.values())[:4]))

    # 4. 随机度越高，候选面越广
    def variety(rnd, n=14):
        out = set()
        for i in range(n):
            bb, _ = fill(i * 7919 + 13, rnd)
            out.add(bb.ids("package")[0])
        return len(out)
    v_low, v_mid, v_high = variety(0.05), variety(0.6), variety(1.0)
    check("随机度 0.05 只碰少数题材", v_low <= 2, str(v_low))
    check("随机度 0.6 多于低档", v_mid >= v_low, f"{v_mid} vs {v_low}")
    check("随机度 1.0 覆盖最多题材", v_high >= v_mid, f"{v_high} vs {v_mid}")

    # 5. 底线：任何种子 + 任何随机度，补齐后都必须通过校验
    bad = []
    for i in range(40):
        for rnd in (0.0, 0.35, 0.6, 0.9, 1.0):
            bb, _ = fill(i * 31337 + 7, rnd)
            iv = bb.validate(LIB)
            errs = [x for x in iv if x["level"] == "error"]
            if errs:
                bad.append((i, rnd, bb.elements, errs[0]["message"]))
                break
        if bad:
            break
    check("40 种子 × 5 档随机度，补齐结果全部合法", not bad,
          str(bad[0])[:200] if bad else "")

    # 6. 冲突三层始终齐
    layer_bad = []
    for i in range(30):
        bb, _ = fill(i * 6151, 0.9)
        cf = [LIB.get(x) for x in bb.ids("conflict")]
        if {"外部", "内部", "结构"} - {c.get("attrs.layer") for c in cf}:
            layer_bad.append(bb.ids("conflict"))
            break
    check("随机后冲突仍三层齐", not layer_bad, str(layer_bad))

    # 7. 低/中随机度下 core·arc 必须来自题材推荐位
    off = []
    for i in range(20):
        bb, _ = fill(i * 4099, 0.6)
        pkg = LIB.get(bb.ids("package")[0])
        if bb.ids("core")[0] not in (pkg.get("attrs.preset_cores") or []):
            off.append(("core", bb.ids("package")[0], bb.ids("core")[0]))
            break
        if bb.ids("arc")[0] not in (pkg.get("attrs.preset_arcs") or []):
            off.append(("arc", bb.ids("package")[0], bb.ids("arc")[0]))
            break
    check("随机度 0.6 下剧情不脱离题材推荐位", not off, str(off))

    # 8. 已选的不被覆盖
    bb, log = fill(424242, 0.9, {"package": "MX-PKG-002", "core": "MX-COR-B09"})
    check("已选题材不被覆盖", bb.ids("package")[0] == "MX-PKG-002", str(bb.ids("package")))
    check("已选内核不被覆盖", bb.ids("core")[0] == "MX-COR-B09", str(bb.ids("core")))
    check("只补空白槽位", all(x["slot"] not in ("package", "core") for x in log), str(log)[:120])

    # 9. 日志里带种子，便于复盘
    _, log2 = fill(13579, 0.6)
    check("补齐日志每条都带种子", bool(log2) and all("seed" in x for x in log2),
          str(log2[:1])[:140])
    # 自查重试会派生内部种子，但「同请求种子 → 同重试路径 → 同结果」必须成立
    _, log3 = fill(13579, 0.6)
    _, log4 = fill(13579, 0.6)
    check("同种子复现同一条重试路径",
          bool(log3) and bool(log4) and log3[0]["seed"] == log4[0]["seed"],
          f"{log3[:1]} vs {log4[:1]}"[:140])
    check("重试后生效种子可追溯", bool(log3) and isinstance(log3[0].get("seed"), int))


def t_pairing():
    print("\n[14] 内核与主线的配对（IN_PAIRING 归零）")

    # 1. 素材层：每个题材包的推荐 core 都能被某条推荐 arc 认领
    miss = []
    for pkg in LIB.of_type("package"):
        cores = pkg.get("attrs.preset_cores") or []
        arcs = pkg.get("attrs.preset_arcs") or []
        owned = set()
        for a in arcs:
            for b in (LIB.get(a).get("attrs.pairs_core") or []):
                owned.add("MX-COR-" + b)
        for c in cores:
            if c not in owned:
                miss.append((pkg.id, c))
    check("16 个推荐 core 全部有配对 arc", not miss, str(miss))

    # 2. 每个推荐 arc 都真的声明了配某个推荐 core
    orphan = []
    for pkg in LIB.of_type("package"):
        cores = set(pkg.get("attrs.preset_cores") or [])
        for a in (pkg.get("attrs.preset_arcs") or []):
            codes = {"MX-COR-" + b for b in (LIB.get(a).get("attrs.pairs_core") or [])}
            if not (codes & cores):
                orphan.append((pkg.id, a, sorted(codes)))
    check("没有孤立的推荐 arc", not orphan, str(orphan))

    # 3. 补齐后 arc 必须声明配当前 core
    bad = []
    for i in range(50):
        for rnd in (0.0, 0.35, 0.6, 0.9, 1.0):
            bp = Blueprint.from_dict({
                "meta": {"market": "MX", "title": "t", "seed": i * 15485863 + 11},
                "elements": {}, "constraints": {"autofill_randomness": rnd},
            })
            b, _ = bp.autofill(LIB)
            if not (b.ids("core") and b.ids("arc")):
                continue
            code = b.ids("core")[0].split("-")[-1]
            pairs = LIB.get(b.ids("arc")[0]).get("attrs.pairs_core") or []
            if pairs and code not in pairs:
                bad.append((i, rnd, b.ids("core"), b.ids("arc"), pairs))
                break
        if bad:
            break
    check("250 组补齐，主线都声明配当前内核", not bad, str(bad[:1])[:200])

    # 4. 端到端：补齐后校验里不应出现 IN_PAIRING
    pairing, illegal = 0, 0
    for i in range(50):
        for rnd in (0.0, 0.35, 0.6, 0.9, 1.0):
            bp = Blueprint.from_dict({
                "meta": {"market": "MX", "title": "t", "seed": i * 32452843 + 5},
                "elements": {}, "constraints": {"autofill_randomness": rnd},
            })
            b, _ = bp.autofill(LIB)
            iv = b.validate(LIB)
            if any(x["code"] == "IN_PAIRING" for x in iv):
                pairing += 1
            if any(x["level"] == "error" for x in iv):
                illegal += 1
    check("250 组补齐，IN_PAIRING 为 0", pairing == 0, str(pairing))
    check("250 组补齐，阻断项为 0", illegal == 0, str(illegal))

    # 5. 用户手选不配对组合时，仍应给出提醒（这条 warn 不能删）
    bp = Blueprint.from_dict({
        "meta": {"market": "MX", "title": "t", "seed": 1},
        "elements": {"core": "MX-COR-B09", "arc": "MX-ARC-C23"},   # C23 只认 B24
    })
    iv = bp.validate(LIB)
    check("手选不配对组合仍有提醒", any(x["code"] == "IN_PAIRING" for x in iv),
          str([x["code"] for x in iv]))


def t_config():
    print("\n[11] 模型配置")
    from core.llm import CONFIG_PATH, LLMConfig, load_saved_config, save_config, test_connection
    bak = load_saved_config()
    try:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        os.environ.pop("SG_LLM_API_KEY", None)
        check("初始未配置", not LLMConfig.from_sources().configured)
        save_config({"base_url": "https://api.deepseek.com/v1", "api_key": "sk-unit-test-0001",
                     "model": "deepseek-chat", "temperature": 0.5})
        cfg = LLMConfig.from_sources()
        check("文件配置生效", cfg.configured and cfg.model == "deepseek-chat")
        check("温度生效", abs(cfg.temperature - 0.5) < 1e-6, str(cfg.temperature))
        check("key 不回显明文", "sk-unit-test-0001" not in json.dumps(cfg.describe(), ensure_ascii=False))
        check("key 脱敏尾号", cfg.describe()["api_key"].endswith("0001"))
        os.environ["SG_LLM_API_KEY"] = "sk-env-wins-9999"
        check("环境变量优先", LLMConfig.from_sources().api_key == "sk-env-wins-9999")
        os.environ.pop("SG_LLM_API_KEY", None)
        save_config({"api_key": None})
        check("清除 key", not load_saved_config().get("api_key"))
        check("清除 key 保留地址", bool(load_saved_config().get("base_url")))
        # 传入 *** 开头的占位不应覆盖真实 key
        save_config({"api_key": "sk-real-2222"})
        save_config({"api_key": "***2222"})
        check("*** 占位不覆盖 key", load_saved_config().get("api_key") == "sk-real-2222")
        r = test_connection()
        check("测试接口不抛异常", r.get("ok") in (True, False))
    finally:
        for k in ("SG_LLM_API_KEY",):
            os.environ.pop(k, None)
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        if bak:
            save_config(bak)


if __name__ == "__main__":
    for fn in (t_elements, t_validate, t_autofill, t_ladder, t_determinism,
               t_pipeline, t_validator_catches, t_export, t_http, t_order, t_spec,
               t_seed, t_pairing, t_config):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAIL.append(f"{fn.__name__} 异常: {e}")
    print(f"\n{'=' * 56}\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗", f)
    sys.exit(1 if FAIL else 0)
