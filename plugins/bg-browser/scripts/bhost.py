#!/usr/bin/env python3
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""bg-browser 宿主：在后台跑一台 Chrome，对外开放标准 CDP 端口。

职责单一 —— 只负责「把浏览器养着」。不提供操作 API（那是 screen.py 的事），
不轮询、不接管。宿主在，浏览器就在；screen.py 随时可重启，互不影响。

为什么必须由 patchright 启动（而不是自己 subprocess 起 Chrome 再 connect_over_cdp）：
patchright 的反检测补丁打在 Playwright 驱动层，自己起 Chrome 会把补丁全丢掉。

【身份边界】本技能默认使用自己的 profile（$BG_DATA/profile，默认落在技能根目录下），
与 patchright-browser 的 profile 完全隔离。但指纹层面两者是同一台机器：WebGL renderer、
canvas 哈希、字体、CPU 核数、时区全部相同，profile 隔离买到的是「账号隔离」而不是
「设备匿名」。

用法：
    python bhost.py start                    # 无头（默认，完全不显示窗口）
    python bhost.py start --headed            # 有头但离屏（顽固风控站点用）
    python bhost.py start --port 9444 --url https://example.com
    python bhost.py start --no-fix-screen      # 关掉 screen/dpr 对齐（排查用）
    python bhost.py status
    python bhost.py stop
"""
import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录：profile / pid / 日志都落这里，代码目录本身不写任何东西。
# 默认 = 技能根目录（随技能走，单机使用最省事）。
# 用 BG_DATA 指到别处即可让「代码」与「身份」分离 —— 从市场安装时，代码在
# plugins/cache/<市场>/<插件>/<版本>/ 这种**带版本号**的目录里，升版本会换目录，
# 老的 profile 不会被带过去；这时把 BG_DATA 钉到一个稳定路径就能保住登录态。
DATA_DIR = os.path.expanduser(os.environ.get("BG_DATA") or ROOT)
PROFILE = os.environ.get("BG_PROFILE") or os.path.join(DATA_DIR, "profile")
PIDFILE = os.path.join(DATA_DIR, "bhost.pid")
DEFAULT_PORT = int(os.environ.get("BG_CDP_PORT", "9444"))

# Chrome 可执行文件：BG_CHROME 优先，其次按常见安装位置探测。
# 只用来读版本号拼 UA —— 真正的启动走 patchright 的 channel="chrome"。
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chrome():
    """返回第一个存在的 Chrome 系可执行文件；BG_CHROME 显式指定时只认它。"""
    env = os.environ.get("BG_CHROME")
    if env:
        return env if os.path.exists(env) else None
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


CHROME_BIN = find_chrome() or CHROME_CANDIDATES[0]

# 视口尺寸。与 --window-size 必须一致，否则 override 会把视口改走样。
VIEW_W, VIEW_H = 1440, 900

# 渲染倍率。2 = 按 Retina 渲染（文字物理像素翻倍，投屏才不糊）。
# 之所以不再钉死 1：本机是 1512x982 @2x 的真机，屏 1512x982 配 dpr=1 反而是
# 「外接非 Retina 屏」的组合；改成 2 之后 screen/dpr/视口三者正好等于真机参数，
# 既更清晰也更自洽。代价是 screencast 帧宽翻倍，由 screen.py 的档位开关承担。
DPR = float(os.environ.get("BG_DPR", "2"))


def chrome_ua():
    """取本机真实 Chrome 的 UA，去掉 headless 标记。

    【为什么这里要自定义 UA】技能纪律写着「不要设置自定义 user_agent」，那是针对
    有头模式（默认即最优）。无头模式下 Chrome 会把 UA 里的 `Chrome/x.y` 改成
    `HeadlessChrome/x.y`，这是无头身份最容易被抓的一条。实测 bot.sannysoft.com：
    不覆盖 → User Agent 一行标红；覆盖后 → 全绿，其余检测项（WebDriver / WebGL /
    Plugins / Languages）本来就已经被 patchright 补好了。所以这是唯一该覆盖的场景。
    """
    ver = "156.0.0.0"
    try:
        out = subprocess.run(
            [CHROME_BIN, "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if out:
            ver = out.split()[-1]
    except Exception:
        pass
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
    )


def display_css_size():
    """主屏的逻辑像素尺寸（CSS px），取不到退回 1512x982。

    【为什么不用 osascript】实测 `osascript -e 'tell application "Finder" to get
    bounds of window of desktop'` 被系统权限拦（错误 -10004），而 CoreGraphics 的
    CGDisplayBounds 是只读查询、不需要任何授权。
    """
    try:
        import ctypes
        import ctypes.util

        path = (
            ctypes.util.find_library("CoreGraphics")
            or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg = ctypes.cdll.LoadLibrary(path)

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayBounds.restype = CGRect
        r = cg.CGDisplayBounds(cg.CGMainDisplayID())
        w, h = int(r.size.width), int(r.size.height)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1512, 982


async def apply_screen(ctx, page, sessions, sw, sh, dpr=DPR):
    """把 page 上报的 screen 尺寸改成真实显示器尺寸，并设渲染倍率。

    【为什么必须做】无头 Chrome 的 screen 恒为 800x600、dpr 恒为 1，而视口是
    1440x813 —— 视口比屏幕还大，物理上不可能。这是比 UA 里那个 HeadlessChrome
    更硬的自相矛盾：UA 能改，这句话自相矛盾改不了。实测 sannysoft 不查这条，
    但商业风控（BOSS / 小红书这类）会查 screen + dpr + 视口三者的自洽。

    【dpr 为什么是 2 而不是 1】本机真机是 1512x982 @2x。dpr=1 时浏览器按「非
    Retina 屏」栅格化文字，投屏画面在 Retina 显示器上看必然发虚；dpr=2 才是把
    这台 Mac 的真实渲染条件还原出来，既解决清晰度，也消掉「screen 像 Retina、
    dpr 却是 1」的违和感。帧宽随之翻倍，画质由 screen.py 的清晰度档位兜住。

    【dpr 改了坐标会偏吗】不会 —— CDP 的 Input 坐标单位是 CSS px，视口 CSS 宽
    仍是 1440；但如果代码拿「帧的物理像素宽」做换算就会偏一倍，所以 screen.py
    的点击基准一律取页面 innerWidth，不取 screencast 元数据。

    【为什么每个 page 都要调】override 是 per-target 的：实测跨导航保持（goto 到
    别的站也不丢），但新建标签不继承（新标签又变回 800x600）。所以既要给启动时
    已存在的 page 补，也要监听 ctx.on("page") 给后续新标签补。
    """
    try:
        cs = await ctx.new_cdp_session(page)
        await cs.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": VIEW_W,
                "height": VIEW_H,
                "deviceScaleFactor": dpr,
                "mobile": False,
                "screenWidth": sw,
                "screenHeight": sh,
                "positionX": 0,
                "positionY": 0,
            },
        )
        # 必须保住引用：session 被回收，override 随之失效，页面又变回 800x600。
        sessions.append(cs)
    except Exception as e:
        print(f"[warn] screen 对齐失败: {type(e).__name__}: {e}", flush=True)


def port_open(port):
    """裸 socket 探端口 —— 绝不用 urllib，本机沙箱有全局 http_proxy 会劫持探测请求。"""
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_state():
    """读取宿主自述（pid / port / profile / mode）。起不来就是空 dict。

    兼容早期只写纯 pid 的格式，避免升级后 `stop` 找不到进程。
    """
    try:
        with open(PIDFILE) as f:
            txt = f.read().strip()
    except OSError:
        return {}
    try:
        d = json.loads(txt)
        return d if isinstance(d, dict) else {"pid": int(d)}
    except ValueError:
        try:
            return {"pid": int(txt)}
        except ValueError:
            return {}


def read_pid():
    return read_state().get("pid")


def write_state(port, profile, mode, screen=None, dpr=None):
    d = {"pid": os.getpid(), "port": port, "profile": profile, "mode": mode}
    if screen:
        d["screen"] = list(screen)
    if dpr:
        d["dpr"] = dpr
    with open(PIDFILE, "w") as f:
        json.dump(d, f)


def cdp_version(port, timeout=3):
    """走裸 socket 发 HTTP，避开全局代理。返回解析后的 dict 或 None。"""
    req = "GET /json/version HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.sendall(req.encode())
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"}" in buf:
                break
        s.close()
        body = buf.split(b"\r\n\r\n", 1)[1]
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None


async def serve(port, profile, headed, url, fix_screen, dpr=DPR):
    from patchright.async_api import async_playwright

    ua = chrome_ua()
    args = [
        f"--remote-debugging-port={port}",
        f"--window-size={VIEW_W},{VIEW_H}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-agent={ua}",
    ]
    # 真 Retina 栅格化。必须放在启动参数里：Emulation 的 deviceScaleFactor 只是
    # 把已有的 1x 画面放大（实测 2880 帧缩回 1x 与原生 1x 差异 mean=0.31/255，
    # 基本是恒等变换），字还是虚的；这个开关才让合成器按 2 倍物理像素重绘。
    if dpr and dpr != 1:
        args.append(f"--force-device-scale-factor={dpr:g}")
    if headed:
        # 有头模式把窗口甩到屏幕外：完全不可见、不抢焦点，但反检测与真浏览器无差异。
        # --test-type 压掉「不受支持的命令行标记」提示栏（它会让视口高度异常）。
        args += ["--window-position=-10000,-10000", "--test-type"]

    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=not headed,
        no_viewport=True,
        channel="chrome",
        args=args,
        # 后台化之后必须夺回这两个节流开关，否则无头/离屏时渲染会被降频
        ignore_default_args=[
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    # 有头模式的 screen/dpr 本来就来自真实窗口，不需要也不要覆盖。
    apply_fix = fix_screen and not headed
    sw, sh = display_css_size()
    if apply_fix:
        sessions = []
        for pg in ctx.pages:
            await apply_screen(ctx, pg, sessions, sw, sh, dpr)
        # 新标签不继承 override，必须逐个补；回调是同步的，用 ensure_future 转异步。
        ctx.on(
            "page",
            lambda pg: asyncio.ensure_future(
                apply_screen(ctx, pg, sessions, sw, sh, dpr)
            ),
        )

    if url:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

    mode = "headed-offscreen" if headed else "headless"
    write_state(port, profile, mode, (sw, sh) if apply_fix else None,
                dpr if apply_fix else None)
    print(
        f"CDP: http://127.0.0.1:{port}  mode={mode}  profile={profile}\n"
        f"UA: {ua}\n"
        f"screen: {sw}x{sh}"
        + (
            f" -> 视口 {VIEW_W}x{VIEW_H} @dpr{dpr:g}（帧 {int(VIEW_W * dpr)}x{int(VIEW_H * dpr)}）"
            if apply_fix
            else f" (未对齐，真实窗口 dpr 由系统决定)"
        ),
        flush=True,
    )
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await ctx.close()
            await pw.stop()
        except Exception:
            pass


def preflight():
    """启动前体检：缺依赖就说清缺什么、怎么装，别丢一堆 traceback 给使用者。"""
    ok = True
    try:
        from patchright.async_api import async_playwright  # noqa: F401
    except ImportError:
        ok = False
        print("✗ 缺少 patchright —— 本技能靠它启动带反检测补丁的 Chrome。", file=sys.stderr)
        print(f"  当前 Python：{sys.executable}", file=sys.stderr)
        print(f"  安装：{sys.executable} -m pip install patchright", file=sys.stderr)

    if find_chrome() is None:
        ok = False
        print("✗ 没找到 Chrome 系浏览器，已探测：", file=sys.stderr)
        for p in CHROME_CANDIDATES:
            print(f"    {p}", file=sys.stderr)
        print(
            "  装了其它 Chromium 内核浏览器可显式指定：BG_CHROME=/路径/到/可执行文件",
            file=sys.stderr,
        )
    return ok


def cmd_start(a):
    if port_open(a.port):
        v = cdp_version(a.port) or {}
        print(f"已在运行：http://127.0.0.1:{a.port}  {v.get('Browser', '')}")
        return 0
    if not preflight():
        return 1
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(a.profile, exist_ok=True)
    try:
        asyncio.run(
            serve(a.port, a.profile, a.headed, a.url, not a.no_fix_screen, a.dpr)
        )
    except KeyboardInterrupt:
        pass
    return 0


def cmd_stop(a):
    pid = read_pid()
    if not pid or not pid_alive(pid):
        print("没有记录在案的宿主进程")
        if port_open(a.port):
            print(f"但 {a.port} 仍被占用，可能残留 Chrome，请手动处理：lsof -nP -iTCP:{a.port}")
        return 1
    os.kill(pid, 15)
    for _ in range(40):
        if not pid_alive(pid) and not port_open(a.port):
            break
        time.sleep(0.25)
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    print(f"已停止 pid={pid}")
    return 0


def cmd_status(a):
    v = cdp_version(a.port)
    if not v:
        print(f"未运行（{a.port} 无 CDP 响应）")
        return 1
    print(f"运行中 http://127.0.0.1:{a.port}")
    print(f"  {v.get('Browser', '?')}")
    print(f"  UA: {v.get('User-Agent', '?')}")
    st = read_state()
    print(f"  pid: {st.get('pid', '?')}")
    print(f"  mode: {st.get('mode', '?')}")
    print(f"  profile: {st.get('profile', '?')}")
    scr = st.get("screen")
    if scr:
        d = st.get("dpr") or 1
        print(
            f"  screen: {scr[0]}x{scr[1]} @dpr{d:g} "
            f"(视口 {VIEW_W}x{VIEW_H}，帧 {int(VIEW_W * d)}x{int(VIEW_H * d)})"
        )
    else:
        print("  screen: 未对齐（headed 模式或 --no-fix-screen）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="bg-browser 后台浏览器宿主")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="启动后台浏览器")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--profile", default=PROFILE)
    s.add_argument("--headed", action="store_true", help="有头但离屏（过强风控站用）")
    s.add_argument("--url", default="about:blank")
    s.add_argument(
        "--dpr",
        type=float,
        default=DPR,
        help="渲染倍率，2=Retina 渲染（默认 2）",
    )
    s.add_argument(
        "--no-fix-screen",
        action="store_true",
        help="不把 screen 报数对齐到真实显示器（排查用）",
    )
    s.set_defaults(fn=cmd_start)

    for name, fn in (("stop", cmd_stop), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.set_defaults(fn=fn)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
