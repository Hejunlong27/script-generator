#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面版入口：双击即用。

打包成 exe 后，双击这个程序会：
  1. 找一个空闲端口起本地服务
  2. 自动打开浏览器
  3. 关掉这个窗口就退出

没有 Python 环境、不装任何东西也能跑。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

TITLE = "剧本生成器"


def _free_port(start: int = 8765, tries: int = 20) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    return False


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from http.server import ThreadingHTTPServer

    import server as srv

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.Handler)
    except OSError as e:
        print(f"\n[启动失败] 端口 {port} 无法监听：{e}")
        input("按回车退出…")
        return 1

    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("=" * 58)
    print(f"  {TITLE}   by 何小疯27")
    print("=" * 58)
    print(f"  地址：{url}")
    print(f"  产物：{Path(srv.OUT)}")
    print()
    print("  浏览器没自动打开的话，手动复制上面的地址。")
    print("  关闭这个窗口即退出。")
    print("=" * 58)

    if _wait_ready(port):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    else:
        print("\n  [!] 服务启动较慢，请手动打开上面的地址。")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
