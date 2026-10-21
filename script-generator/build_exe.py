#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 Windows 桌面版（单文件 exe）。

  python build_exe.py

产物：dist/ScriptGenerator.exe
把 data/（含完整素材库）和 web/ 一起打进去，用户下载即用。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("缺少 PyInstaller：pip install pyinstaller")
        return 2

    for d in ("build/work", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    args = [
        str(ROOT / "launcher.py"),
        "--onefile",
        "--name", "ScriptGenerator",
        "--add-data", f"{ROOT / 'data'};data",
        "--add-data", f"{ROOT / 'web'};web",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "work"),
        "--specpath", str(ROOT / "build"),
        "--noconfirm", "--clean", "--log-level", "WARN",
    ]
    vf = ROOT / "build" / "version.txt"
    if vf.is_file():
        args += ["--version-file", str(vf)]

    pyi.run(args)
    exe = ROOT / "dist" / "ScriptGenerator.exe"
    if exe.is_file():
        print(f"\n[OK] {exe}  ({exe.stat().st_size / 1048576:.1f} MB)")
        return 0
    print("\n[FAIL] 未生成 exe")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
