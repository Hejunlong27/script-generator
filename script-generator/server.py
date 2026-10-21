#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖 Web 服务（stdlib http.server）。

  python server.py            -> http://127.0.0.1:8765
  python server.py --port 9000

API（全部 JSON）：
  GET  /api/catalog?market=MX      要素库 + 槽位注册表 + 风格 + 模型状态
  POST /api/validate               蓝图校验 + 自动补齐 + 编译预览（不调模型，秒回）
  POST /api/preview                看某一集真正发给模型的 prompt
  POST /api/generate               起一个生成任务，返回 job_id
  GET  /api/job/<id>               轮询进度与结果
  GET  /api/download?path=...      下载导出文件
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import pipeline
from core.errors import SGError

def _res_dir(sub: str) -> Path:
    """打包后静态资源在解包目录，产物写到 exe 旁边（可写）。"""
    import sys
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / sub
    return Path(__file__).resolve().parent / sub


ROOT = Path(__file__).resolve().parent
WEB = _res_dir("web")
OUT = (Path(sys.executable).parent / "out") if getattr(sys, "frozen", False) \
      else ROOT / "out"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "ScriptGen/1.0"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # noqa: D401
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, e: Exception, status=400):
        if isinstance(e, SGError):
            self._json({"error": e.to_dict()}, status)
        else:
            self._json({"error": {"code": "SYS_UNKNOWN", "message": str(e),
                                  "hint": "", "trace": traceback.format_exc()[-1500:]}}, 500)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise SGError(f"请求体不是合法 JSON: {e}") from e

    def _file(self, path: Path):
        if not path.is_file():
            self._json({"error": {"code": "SYS_NOT_FOUND", "message": f"文件不存在: {path.name}"}}, 404)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".md", ".json", ".csv", ".fountain", ".txt"):
            ctype = "text/plain; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if path.suffix in (".md", ".json", ".csv", ".fountain"):
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    # ---------- 路由 ----------
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._file(WEB / "index.html")
            elif u.path.startswith("/api/catalog"):
                self._json(pipeline.catalog((q.get("market") or ["MX"])[0].upper()))
            elif u.path.startswith("/api/config"):
                self._json(self._config_payload())
            elif u.path.startswith("/api/job/"):
                jid = u.path.rsplit("/", 1)[-1]
                with JOBS_LOCK:
                    job = JOBS.get(jid)
                if not job:
                    self._json({"error": {"code": "SYS_NOT_FOUND", "message": "任务不存在或已过期"}}, 404)
                    return
                with job["lock"]:
                    snap = {"id": jid, "status": job["status"], "pct": job["pct"],
                            "message": job["message"], "log": job["log"][-40:],
                            "error": job["error"]}
                if job["status"] == "done":
                    snap["script"] = job["script"]
                self._json(snap)
            elif u.path.startswith("/api/download"):
                rel = (q.get("path") or [""])[0]
                target = (ROOT / rel).resolve() if rel else None
                if not target or ROOT not in target.parents:
                    self._json({"error": {"code": "SYS_FORBIDDEN", "message": "路径越界"}}, 403)
                    return
                self._file(target)
            else:
                # 静态资源
                rel = u.path.lstrip("/")
                target = (WEB / rel).resolve()
                if WEB in target.parents and target.is_file():
                    self._file(target)
                else:
                    self._json({"error": {"code": "SYS_NOT_FOUND", "message": f"未知路径 {u.path}"}}, 404)
        except SGError as e:
            self._err(e)
        except Exception as e:  # noqa: BLE001
            self._err(e)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/validate":
                self._validate(body)
            elif u.path == "/api/preview":
                self._json(pipeline.preview_prompt(body.get("blueprint") or {},
                                                   int(body.get("episode") or 1)))
            elif u.path == "/api/estimate":
                self._json(pipeline.estimate(body.get("blueprint") or {}))
            elif u.path == "/api/config":
                self._save_config(body)
            elif u.path == "/api/config/test":
                from core.llm import test_connection
                self._json(test_connection())
            elif u.path == "/api/generate":
                self._generate(body)
            else:
                self._json({"error": {"code": "SYS_NOT_FOUND", "message": f"未知接口 {u.path}"}}, 404)
        except SGError as e:
            self._err(e)
        except Exception as e:  # noqa: BLE001
            self._err(e)

    # ---------- 业务 ----------
    def _config_payload(self) -> dict:
        from core.llm import CONFIG_PATH, LLMConfig, load_saved_config
        saved = load_saved_config()
        cfg = LLMConfig.from_sources()
        return {
            "llm": cfg.describe(),
            "source": "env" if os.environ.get("SG_LLM_API_KEY") else ("file" if saved.get("api_key") else "none"),
            "config_path": str(CONFIG_PATH),
            # 不回传 key 明文，只给「有没有」和尾号
            "has_key": bool(cfg.api_key),
            "key_hint": ("***" + cfg.api_key[-4:]) if cfg.api_key else "",
            "saved": {k: v for k, v in saved.items() if k != "api_key"},
            "env_locked": bool(os.environ.get("SG_LLM_API_KEY")),
        }

    def _save_config(self, body: dict) -> None:
        from core.llm import save_config
        try:
            save_config(body.get("llm") or {})
        except OSError as e:
            raise SGError(f"配置写入失败: {e}", hint="检查目录写权限") from e
        self._json(self._config_payload())

    def _validate(self, body: dict):
        bp = body.get("blueprint") or {}
        from core.models import Blueprint, Library

        b = Blueprint.from_dict(bp)
        lib = Library.load(b.meta.get("market", "MX"))
        issues = b.validate(lib)
        filled = []
        if not [i for i in issues if i["level"] == "error"]:
            b2, filled = b.autofill(lib)
            if filled:
                b = b2
        # 补齐后再校验一次：把「未选」之类的前置状态换成补完的真实结果
        if filled:
            issues = b.validate(lib)
        out = {"issues": issues, "autofilled": filled,
               "ok": not [i for i in issues if i["level"] == "error"]}
        if out["ok"]:
            bp2 = b.to_dict()
            out["blueprint"] = bp2
            try:
                out["estimate"] = pipeline.estimate(bp2)
            except SGError as e:
                out["issues"].append({"level": "warn", "code": e.code,
                                      "message": e.message, "hint": e.hint})
        self._json(out)

    def _generate(self, body: dict):
        bp = body.get("blueprint") or {}
        eps = body.get("episodes")
        if isinstance(eps, str):
            eps = _parse_range(eps)
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "status": "running", "pct": 0, "message": "排队中",
               "log": [], "error": None, "script": None, "lock": threading.Lock()}
        with JOBS_LOCK:
            JOBS[jid] = job
            if len(JOBS) > 20:
                for k in list(JOBS)[:len(JOBS) - 20]:
                    JOBS.pop(k, None)

        def progress(ev: dict):
            with job["lock"]:
                job["pct"] = ev.get("pct", job["pct"])
                job["message"] = ev.get("message", job["message"])
                job["log"].append(f"{ev.get('stage')}: {ev.get('message')}")

        def work():
            try:
                script = pipeline.run(
                    bp,
                    mode=body.get("mode") or "auto",
                    episodes=eps,
                    workers=int(body.get("workers") or 4),
                    max_repair=int(body.get("max_repair") or 2),
                    allow_degrade=body.get("allow_degrade", True),
                    progress=progress,
                    outdir=OUT,
                    export=True,
                )
                with job["lock"]:
                    job["script"] = script
                    job["status"] = "done"
                    job["pct"] = 100
                    job["message"] = "完成"
            except SGError as e:
                with job["lock"]:
                    job["status"] = "failed"
                    job["error"] = e.to_dict()
                    job["message"] = e.message
            except Exception as e:  # noqa: BLE001
                with job["lock"]:
                    job["status"] = "failed"
                    job["error"] = {"code": "SYS_UNKNOWN", "message": str(e),
                                    "trace": traceback.format_exc()[-2000:]}
                    job["message"] = str(e)

        threading.Thread(target=work, daemon=True).start()
        self._json({"job_id": jid})


def _parse_range(s: str) -> list[int] | None:
    """'1-6' / '1,3,5' / '1-3,7' -> [1,2,3,7]"""
    out: list[int] = []
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, bb = part.partition("-")
            try:
                out += list(range(int(a), int(bb) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return sorted(set(out)) or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"剧本生成器已启动： http://{args.host}:{args.port}")
    print(f"要素库： {', '.join(pipeline.list_libraries()) or '(空，先跑 tools/build_elements.py)'}")
    print(f"LLM： {json.dumps(pipeline.probe(), ensure_ascii=False)}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
