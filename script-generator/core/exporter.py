# -*- coding: utf-8 -*-
"""导出层：同一份 Script，四种交付形态。

  md        人读：要素表 + 人物卡 + 分集大纲 + 分场剧本
  json      机器读：可直接进配音/分镜/审核流水线
  csv       进表格：一行一个镜头，方便排期与报价
  fountain  进影视工具：Highland / Fade In 可直接打开

导出不做任何"美化性改写"——只做格式转换。内容有错，在校验阶段就该被拦住。
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

TIME_EN = {"日": "DAY", "夜": "NIGHT", "晨昏": "DUSK", "室内暖光": "NIGHT", "室内冷光": "NIGHT", "混合": "DAY"}
TIME_CN = {"日": "日", "夜": "夜", "晨昏": "晨昏"}


def _slot_order(elements: dict) -> list[str]:
    """按注册表的 step 输出，保证导出文件里的顺序与界面一致。"""
    from .registry import blueprint_slots
    order = {k: i for i, (k, _) in enumerate(blueprint_slots())}
    return sorted(elements or {}, key=lambda k: order.get(k, 99))


def _safe_name(s: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("_") or "script"


# ------------------------------------------------------------------ Markdown
def to_markdown(script: dict) -> str:
    m = script.get("meta", {})
    plan = script.get("plan", {})
    ledger = script.get("ledger", {})
    lines: list[str] = []
    A = lines.append

    A(f"# {m.get('title', '未命名短剧')}")
    A("")
    A(f"> {m.get('market', '')} · {m.get('episode_count', 0)} 集 · 单集 {m.get('episode_seconds', 60)} 秒 · "
      f"风格 `{m.get('style', '')}` · 生成于 {m.get('generated_at', '')}")
    A("")

    # 要素表
    A("## 一、要素配置")
    A("")
    A("| 槽位 | ID | 名称 |")
    A("|---|---|---|")
    for slot in _slot_order(m.get("elements")):
        ids = m["elements"][slot]
        for eid in (ids if isinstance(ids, list) else [ids]):
            nm = _element_name(script, eid)
            A(f"| {slot} | `{eid}` | {nm} |")
    A("")

    # 人物
    A("## 二、人物")
    A("")
    for cid, c in (ledger.get("characters") or {}).items():
        A(f"### {c['full_zh']}（{c['full_es']}）· {c['role']}")
        A("")
        A(f"- `{cid}`｜身份：{c.get('identity') or '—'}")
        A(f"- 表面：{c.get('surface') or '—'}")
        A(f"- 缺陷：{c.get('flaw') or '—'}")
        A(f"- 秘密：{c.get('secret') or '—'}")
        A(f"- 说话方式：{c.get('speech') or '—'}")
        nd = c.get("never_do") or []
        if isinstance(nd, str):
            nd = [nd]
        if nd:
            A(f"- **不可做**：{'；'.join(nd)}")
        A("")

    # 分集大纲
    A("## 三、分集大纲")
    A("")
    A("| 集 | 段位 | 标题 | 爽点 | 刻度 | 集尾钩子 |")
    A("|---|---|---|---|---|---|")
    for ep in script.get("episodes") or []:
        A(f"| {ep.get('episode')} | {ep.get('arc_segment', '')} | {ep.get('title', '')} | "
          f"{(ep.get('beat') or {}).get('ref', '')} | {(ep.get('beat') or {}).get('level', '')} | "
          f"{(ep.get('hook') or {}).get('ref', '')} |")
    A("")

    # 剧本正文
    A("## 四、剧本正文")
    A("")
    for ep in script.get("episodes") or []:
        A(f"### 第 {ep.get('episode')} 集《{ep.get('title', '')}》")
        A("")
        A(f"> {ep.get('logline', '')}")
        A("")
        for sg in ep.get("segments") or []:
            A(f"- **{sg.get('range')}｜{sg.get('kind')}**：{sg.get('text', '')}")
        A("")
        for sc in ep.get("scenes") or []:
            A(f"**{sc.get('slug')}｜{sc.get('location', '')}｜{sc.get('time', '')}｜镜头：{sc.get('camera', '')}**")
            A("")
            cast = "、".join(_char_short(script, c) for c in (sc.get("cast_refs") or []))
            A(f"在场：{cast or '—'}")
            A("")
            if sc.get("action"):
                A(f"{sc['action']}")
                A("")
            for ln in sc.get("lines") or []:
                reg = f"（{ln['register']}）" if ln.get("register") else ""
                A(f"　　**{ln.get('speaker', '')}**{reg}：{ln.get('text', '')}")
                if ln.get("gloss"):
                    A(f"　　　　↳ {ln['gloss']}")
            if sc.get("props"):
                A("")
                A(f"道具：{'、'.join(_prop_name(script, p) for p in sc['props'])}")
            A("")
        comp = ep.get("compliance") or {}
        gen = ep.get("generator") or {}
        flags = []
        if gen.get("mode") == "template":
            flags.append("模板降级稿")
        if gen.get("repaired"):
            flags.append("已修复")
        if not comp.get("passed", True):
            flags.append("红线未过")
        if flags:
            A(f"> ⚠ {' / '.join(flags)}")
            A("")

    # 附录
    A("---")
    A("")
    A("## 附录：制作口径")
    A("")
    cost = (plan.get("cost_summary") or {})
    if cost:
        A(f"- 用到的场景 {cost.get('distinct_scenes')} 个（全库 {len(script.get('library_counts', {}).get('scene', []) or []) or '—'}）")
        A(f"- 群戏 {cost.get('crowd_scenes')} 场 · 平均在场人数 {cost.get('avg_cast')}")
    chk = plan.get("beat_ladder_check") or {}
    if chk:
        A(f"- 爽点梯度：相邻最大差 {chk.get('max_adjacent_delta')}（≤2）· 每 10 集峰值 ≥{chk.get('min_peak_per_10')}"
          f" · 9–10 档 {chk.get('count_9_10')} 次（≤3）")
    reg = plan.get("register_timeline") or []
    if reg:
        A("- 称谓线：" + " → ".join(f"E{r['episode']} {r['register']}" for r in reg))
    return "\n".join(lines)


def _element_name(script: dict, eid: str) -> str:
    lib_el = (script.get("element_index") or {}).get(eid)
    if lib_el:
        return lib_el.get("name", eid)
    return (script.get("ledger", {}).get("characters", {}).get(eid) or {}).get("full_zh", eid)


def _char_short(script: dict, cid: str) -> str:
    c = (script.get("ledger", {}).get("characters") or {}).get(cid)
    return c["name_zh"] if c else cid


def _prop_name(script: dict, pid: str) -> str:
    p = (script.get("ledger", {}).get("props") or {}).get(pid)
    return p.get("name", pid) if p else pid


# ------------------------------------------------------------------ CSV
def to_csv(script: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["集", "场", "场景ID", "场景", "时间", "镜头", "在场人物", "动作",
                "说话人", "语域", "台词(es)", "中文意译", "道具",
                "爽点", "爽点刻度", "集尾钩子"])
    for ep in script.get("episodes") or []:
        beat = ep.get("beat") or {}
        hook = ep.get("hook") or {}
        for sc in ep.get("scenes") or []:
            base = [
                ep.get("episode"), sc.get("slug"), sc.get("scene_ref"), sc.get("location"),
                sc.get("time"), sc.get("camera"),
                "、".join(_char_short(script, c) for c in (sc.get("cast_refs") or [])),
                sc.get("action"),
            ]
            tail = [beat.get("ref", ""), beat.get("level", ""), hook.get("ref", "")]
            if sc.get("lines"):
                for ln in sc["lines"]:
                    w.writerow(base + [ln.get("speaker", ""), ln.get("register", ""),
                                       ln.get("text", ""), ln.get("gloss", ""),
                                       "、".join(_prop_name(script, p) for p in (sc.get("props") or [])),
                                       *tail])
            else:
                w.writerow(base + ["", "", "", "",
                                   "、".join(_prop_name(script, p) for p in (sc.get("props") or [])),
                                   *tail])
    return buf.getvalue()


# ------------------------------------------------------------------ Fountain
def to_fountain(script: dict) -> str:
    m = script.get("meta", {})
    out: list[str] = [
        f"Title: {m.get('title', 'Untitled')}",
        f"Credit: 剧本生成器 / {m.get('style', '')}",
        f"Draft date: {m.get('generated_at', '')}",
        "",
        "====",
        "",
    ]
    for ep in script.get("episodes") or []:
        out += [f"# 第 {ep.get('episode')} 集《{ep.get('title', '')}》", ""]
        for sc in ep.get("scenes") or []:
            t = TIME_EN.get(sc.get("time", "夜"), "NIGHT")
            loc = sc.get("location") or sc.get("scene_ref") or ""
            out += [f"INT. {loc} - {t}", ""]
            if sc.get("action"):
                out += [sc["action"], ""]
            for ln in sc.get("lines") or []:
                speaker = (ln.get("speaker") or "").upper()
                out += [speaker]
                if ln.get("register"):
                    out += [f"({ln['register']})"]
                out += [ln.get("text", "")]
                if ln.get("gloss"):
                    out += [f"^{ln['gloss']}"]
                out += [""]
            out += [""]
        out += [""]
    return "\n".join(out)


# ------------------------------------------------------------------ HTML（交付/阅读页）
_HTML_CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--line:#e4e7ec;--line2:#eef0f3;--tx:#1a1d21;--tx2:#5b636e;
--tx3:#8b939e;--accent:#2f6df6;--accent-bg:#eef3ff;--warn:#a86400;--warn-bg:#fdf4e3;
--ok:#12805c;--ok-bg:#e9f7f1;--mono:ui-monospace,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:36px 26px 90px}
header.top{border-bottom:2px solid var(--tx);padding-bottom:14px;margin-bottom:26px}
header.top h1{margin:0 0 6px;font-size:28px;letter-spacing:-.01em}
header.top .meta{font-size:12.5px;color:var(--tx3)}
h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx3);
margin:36px 0 12px;font-weight:600}
.table{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
.table th,.table td{text-align:left;padding:7px 11px;border-bottom:1px solid var(--line2)}
.table th{background:var(--line2);color:var(--tx2);font-size:11.5px;letter-spacing:.03em}
.mono{font-family:var(--mono);font-size:11.5px;color:var(--tx2)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.card h3{margin:0 0 8px;font-size:15px}
.card ul{margin:6px 0 0;padding-left:18px;font-size:13px;color:var(--tx2)}
.ep{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}
.ep h3{margin:0 0 4px;font-size:19px}
.ep .log{color:var(--tx2);font-size:13px;margin-bottom:14px}
.tl{border-left:3px solid var(--accent-bg);padding-left:14px;margin:12px 0 18px;font-size:13px}
.tl div{margin:3px 0}
.tl b{font-family:var(--mono);font-size:11px;color:var(--tx3);font-weight:600;margin-right:8px}
.scene{margin:0 0 16px;padding-top:6px}
.scene .slug{font-family:var(--mono);font-size:12px;color:var(--accent);letter-spacing:.04em}
.scene .loc{font-weight:650;font-size:14px;margin:2px 0 2px}
.scene .sm{font-size:11.5px;color:var(--tx3);margin-bottom:8px}
.scene .act{font-size:13.5px;color:var(--tx2);margin-bottom:10px;white-space:pre-wrap}
.line{margin:0 0 9px;font-size:14px}
.line .who{font-weight:650}
.line .reg{font-size:10.5px;color:var(--tx3);margin-left:6px;border:1px solid var(--line);
border-radius:99px;padding:1px 6px}
.line .es{display:block;margin-top:1px}
.line .gl{display:block;color:var(--tx3);font-size:12.5px}
.badge{display:inline-block;font-size:11px;border-radius:99px;padding:2px 9px;
background:var(--warn-bg);color:var(--warn);border:1px solid #f3dfb8;margin-right:6px}
.badge.ok{background:var(--ok-bg);color:var(--ok);border-color:#c9ecdd}
.note{font-size:12px;color:var(--tx3);margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}
.ep,.card,.table{break-inside:avoid;border-color:#ddd}h2{break-after:avoid}}
"""


def to_html(script: dict) -> str:
    m = script.get("meta", {})
    ledger = script.get("ledger", {})
    plan = script.get("plan", {})
    P: list[str] = []
    A = P.append

    A(f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
      f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>{_h(m.get('title', '剧本'))}</title><style>{_HTML_CSS}</style></head><body><div class='wrap'>")
    A(f"<header class='top'><h1>{_h(m.get('title', '未命名短剧'))}</h1>"
      f"<div class='meta'>{_h(m.get('market', ''))} · {m.get('episode_count', 0)} 集 · "
      f"单集 {m.get('episode_seconds', 60)} 秒 · 风格 {_h(m.get('style', ''))} · "
      f"生成 {_h(m.get('generated_at', ''))} · 模式 {_h(m.get('mode', ''))}</div></header>")

    # 要素
    A("<h2>要素配置</h2><table class='table'><tr><th>槽位</th><th>ID</th><th>名称</th></tr>")
    for slot in _slot_order(m.get("elements")):
        ids = m["elements"][slot]
        for eid in (ids if isinstance(ids, list) else [ids]):
            A(f"<tr><td>{_h(slot)}</td><td class='mono'>{_h(str(eid))}</td>"
              f"<td>{_h(_element_name(script, str(eid)))}</td></tr>")
    A("</table>")

    # 人物
    A("<h2>人物</h2>")
    for cid, c in (ledger.get("characters") or {}).items():
        nd = c.get("never_do") or []
        if isinstance(nd, str):
            nd = [nd]
        L = "".join(f"<li>{_h(x)}</li>" for x in nd)
        A(f"<div class='card'><h3>{_h(c['full_zh'])}（{_h(c['full_es'])}）"
          f"<span class='mono'>{_h(cid)}</span></h3>"
          f"<ul><li>身份：{_h(c.get('identity') or '—')}</li>"
          f"<li>表面：{_h(c.get('surface') or '—')}</li>"
          f"<li>缺陷：{_h(c.get('flaw') or '—')}</li>"
          f"<li>秘密：{_h(c.get('secret') or '—')}</li>"
          f"<li>不可做：{_h('；'.join(nd) or '—')}</li></ul></div>")

    # 大纲
    A("<h2>分集大纲</h2><table class='table'><tr><th>集</th><th>段位</th><th>标题</th>"
      "<th>爽点</th><th>刻度</th><th>集尾钩</th></tr>")
    for ep in script.get("episodes") or []:
        b = ep.get("beat") or {}
        hk = ep.get("hook") or {}
        A(f"<tr><td class='mono'>{ep.get('episode')}</td><td>{_h(ep.get('arc_segment', ''))}</td>"
          f"<td>{_h(ep.get('title', ''))}</td><td class='mono'>{_h(b.get('ref', ''))}</td>"
          f"<td>{_h(str(b.get('level', '')))}</td><td class='mono'>{_h(hk.get('ref', ''))}</td></tr>")
    A("</table>")

    # 正文
    A("<h2>剧本正文</h2>")
    for ep in script.get("episodes") or []:
        gen = ep.get("generator") or {}
        badge = ""
        if gen.get("mode") == "template":
            badge = "<span class='badge'>模板降级稿 · 文采待润色</span>"
        elif gen.get("mode") == "llm":
            badge = "<span class='badge ok'>LLM 稿</span>"
        A(f"<div class='ep'><h3>第 {ep.get('episode')} 集《{_h(ep.get('title', ''))}》{badge}</h3>"
          f"<div class='log'>{_h(ep.get('logline', ''))}</div>")
        A("<div class='tl'>")
        for sg in ep.get("segments") or []:
            A(f"<div><b>{_h(sg.get('range'))}</b>{_h(sg.get('text', ''))}</div>")
        A("</div>")
        for sc in ep.get("scenes") or []:
            A(f"<div class='scene'><div class='slug'>{_h(sc.get('slug', ''))} · "
              f"{_h(sc.get('scene_ref', ''))}</div>"
              f"<div class='loc'>{_h(sc.get('location', ''))}</div>"
              f"<div class='sm'>{_h(sc.get('time', ''))} · 镜头 {_h(sc.get('camera', ''))} · "
              f"在场 {'、'.join(_h(_char_short(script, c)) for c in (sc.get('cast_refs') or []))}</div>")
            if sc.get("action"):
                A(f"<div class='act'>{_h(sc['action'])}</div>")
            for ln in sc.get("lines") or []:
                reg = f"<span class='reg'>{_h(ln.get('register', ''))}</span>" if ln.get("register") else ""
                A(f"<div class='line'><span class='who'>{_h(ln.get('speaker', ''))}</span>{reg}"
                  f"<span class='es'>{_h(ln.get('text', ''))}</span>"
                  + (f"<span class='gl'>↳ {_h(ln.get('gloss', ''))}</span>"
                     if ln.get("gloss") and ln.get("gloss") != ln.get("text") else "")
                  + "</div>")
            A("</div>")
        ck = ep.get("_checks") or {}
        if ck:
            A(f"<div class='note'>校验：{ck.get('errors', 0)} 错 / {ck.get('warnings', 0)} 警 · "
              f"爽点 {_h(str((ep.get('beat') or {}).get('level', '')))} 刻度 · "
              f"钩子 {_h((ep.get('hook') or {}).get('ref', ''))}</div>")
        A("</div>")

    # 附录
    cost = plan.get("cost_summary") or {}
    chk = plan.get("beat_ladder_check") or {}
    A("<h2>制作口径</h2><div class='card'><ul>")
    A(f"<li>场景 {cost.get('distinct_scenes', 0)} 个 · 群戏 {cost.get('crowd_scenes', 0)} 场 · "
      f"平均在场 {cost.get('avg_cast', 0)} 人</li>")
    A(f"<li>爽点梯度：相邻最大差 {chk.get('max_adjacent_delta', '—')}（铁律 ≤2）· "
      f"每 10 集峰值 ≥{chk.get('min_peak_per_10', '—')} · 9–10 档 {chk.get('count_9_10', 0)} 次（铁律 ≤3）</li>")
    reg = plan.get("register_timeline") or []
    if reg:
        A("<li>称谓线：" + " → ".join(f"E{r['episode']} {_h(r['register'])}" for r in reg) + "</li>")
    A("</ul></div></div></body></html>")
    return "\n".join(P)


def _h(s: Any) -> str:
    import html
    return html.escape(str(s if s is not None else ''))


# ------------------------------------------------------------------ 打包
def export_all(script: dict, outdir) -> dict[str, str]:
    from pathlib import Path
    d = Path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    base = _safe_name(f"{script.get('meta', {}).get('title', 'script')}_{datetime.now():%Y%m%d_%H%M%S}")
    paths = {
        "md": d / f"{base}.md",
        "html": d / f"{base}.html",
        "json": d / f"{base}.json",
        "csv": d / f"{base}.csv",
        "fountain": d / f"{base}.fountain",
    }
    paths["md"].write_text(to_markdown(script), encoding="utf-8")
    paths["html"].write_text(to_html(script), encoding="utf-8")
    paths["json"].write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    paths["csv"].write_text("\ufeff" + to_csv(script), encoding="utf-8")
    paths["fountain"].write_text(to_fountain(script), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
