#!/usr/bin/env python3
# Copyright 2026 xuepengju (https://agentest.vip)
# SPDX-License-Identifier: Apache-2.0
"""patchright-browser 自检：依赖、服务、浏览器、反检测基础面一次验完。

用法：python3 selftest.py [--with-server]   # --with-server 会临时拉起服务做端到端验证
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PY = sys.executable
PR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pr.py")
BASE = "http://127.0.0.1:9333"
ok = True


def check(name, fn):
    global ok
    try:
        detail = fn()
        print(f"  ok   {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:
        ok = False
        print(f"  FAIL {name} — {type(e).__name__}: {e}")


def req(path, data=None, timeout=30):
    r = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def c_patchright():
    import patchright  # noqa: F401
    return "import ok"


def c_chrome():
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
            ver = b.version
            b.close()
            return f"system chrome {ver}"
        except Exception:
            return "system chrome 不可用，将回退自带 chromium（建议跑 patchright install chromium）"


def c_server():
    with urllib.request.urlopen(BASE + "/status", timeout=2) as r:
        return json.loads(r.read()).get("running") and "控制服务在线" or "服务在但浏览器未起"


def c_e2e():
    info = req("/goto", {"url": "https://bot.sannysoft.com/"}, timeout=90)
    webdriver = req("/eval", {"expr": "navigator.webdriver"})["result"]
    shot = req("/shot", {"name": "pr-selftest.png"})
    return f"title={info['title']!r} navigator.webdriver={webdriver} 截图={shot['path']}"


print("== patchright-browser 自检 ==")
check("patchright 依赖", c_patchright)
check("浏览器内核", c_chrome)

with_server = "--with-server" in sys.argv
already = False
try:
    urllib.request.urlopen(BASE + "/status", timeout=1)
    already = True
except Exception:
    pass

if with_server and not already:
    proc = subprocess.Popen([PY, PR, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            urllib.request.urlopen(BASE + "/status", timeout=1)
            break
        except Exception:
            time.sleep(1)
    check("端到端 (goto+webdriver+截图)", c_e2e)
    try:
        req("/close")
    except Exception:
        pass
elif already:
    check("端到端 (goto+webdriver+截图)", c_e2e)
else:
    print("  skip 端到端 — 服务未运行（加 --with-server 或先启动 pr.py serve）")

print("== 结果:", "全部通过" if ok else "存在失败项", "==")
sys.exit(0 if ok else 1)
