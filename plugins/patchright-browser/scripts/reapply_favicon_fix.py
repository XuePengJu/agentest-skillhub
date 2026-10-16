#!/usr/bin/env python3
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""重新给 patchright 驱动打「放行 favicon 请求」补丁（升级 patchright 后必须重跑）。

背景（2026-10-16 定位）：
    patchright（内置 CDP-Patches）会**默认开启全局请求拦截**，而 Playwright 驱动里
    对 favicon 请求有这段逻辑：

        if (request._isFavicon) { route?.abort("aborted"); return; }   // 上游：route 为空 → 休眠

    上游 Playwright 因为没有开拦截，`route` 恒为 null，这段等于空转，favicon 请求照常走网；
    patchright 下 `route` 非空 → **每个以 /favicon.ico 结尾的请求被真的 abort**：
      - 浏览器存不进任何 .ico 图标 → 标签页全变灰色地球
      - PNG/SVG 图标（bing、gitcode、fehelper、HN、本地 favicon.svg）不受影响
      - 页面里 JS `fetch('.../favicon.ico')` 也会 "Failed to fetch"

补丁把 abort 换成 continue：等价于上游「不拦截时放行」的行为，favicon 请求正常出网并被存库。

用法：
    python3 "$SKILL_DIR/scripts/reapply_favicon_fix.py" [--check]

验证：新 profile 打开 https://www.baidu.com/ 静置 ~20s，
      <profile>/Default/Favicons 里应出现 https://www.baidu.com/favicon.ico
"""
import argparse
import os
import shutil
import sys

NEEDLE_ABORT = '''        if (request2._isFavicon) {
          route2?.abort("aborted").catch(() => {
          });
          return;
        }'''
NEEDLE_FIXED = '''        if (request2._isFavicon) {
          route2?.continue({}).catch(() => {
          });
          return;
        }'''


def driver_paths():
    try:
        import patchright  # noqa: F401
    except Exception as e:  # pragma: no cover
        print(f"[x] 无法 import patchright: {e}")
        sys.exit(2)
    base = os.path.dirname(os.path.abspath(__import__("patchright").__file__))
    pkg = os.path.join(base, "driver", "package", "lib", "coreBundle.js")
    return pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查状态，不修改")
    args = ap.parse_args()

    path = driver_paths()
    if not os.path.exists(path):
        print(f"[x] 找不到驱动文件: {path}")
        sys.exit(2)

    src = open(path, encoding="utf-8").read()
    if NEEDLE_FIXED in src:
        print(f"[=] 补丁已在位: {path}")
        return
    if NEEDLE_ABORT not in src:
        print("[!] 未匹配到预期代码（patchright 可能已改版）。请人工检查这段:"
              "\n    if (request2._isFavicon) { route2?.abort(\"aborted\") ... }")
        sys.exit(3)
    if args.check:
        print(f"[!] 补丁缺失（需要重打）: {path}")
        sys.exit(1)

    bak = path + ".bak-faviconfix"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"[+] 已备份原文件 -> {bak}")
    open(path, "w", encoding="utf-8").write(src.replace(NEEDLE_ABORT, NEEDLE_FIXED))
    print(f"[√] 补丁已写入: {path}")
    print("    生效条件：重启 patchright 浏览器（服务进程需重启才会重新加载驱动）")


if __name__ == "__main__":
    main()
