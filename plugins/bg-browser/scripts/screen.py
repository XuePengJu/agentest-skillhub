#!/usr/bin/env python3
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""bg-browser 实时投屏控制台

把一台「后台 Chrome」的画面实时推到浏览器里看，并能直接点击/输入操作它。
画面走 MJPEG（<img src="/stream">，浏览器原生支持，无需 WebSocket）。
清晰度靠浏览器启动参数 --force-device-scale-factor=2（页面恒按真 2x 栅格），
取帧方式按档位自动选：高清档用 Page.captureScreenshot（按物理像素出图），
流畅档用 Page.startScreencast（事件驱动、静止零开销）。

设计要点：
- 全链路 asyncio + patchright async API，单事件循环，不阻塞、多客户端并发
- 只读驱动：attach 到标签页用 session；headed 下不调 Target.activateTarget 所以不抢焦点，
  headless 下必须调 —— 否则非活跃标签截出纯白（见 attach() 里的 bring_to_front）
- 所有输入走 `Input.dispatch*` 直接注入渲染进程，不经过 WindowServer

启动：
    python screen.py                      # 默认连 127.0.0.1:9444，监听 9334
    BG_CDP=http://127.0.0.1:9444 BG_PORT=9334 python screen.py

环境变量：BG_CDP / BG_PORT / BG_GEAR(clear|balanced|smooth) / BG_MODE(auto|poll|screencast)
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from urllib.parse import urlparse

CDP_ENDPOINT = os.environ.get("BG_CDP", "http://127.0.0.1:9444")
PORT = int(os.environ.get("BG_PORT", "9334"))
MODE = os.environ.get("BG_MODE", "auto")

# 清晰度档位：名字 -> (jpeg quality, 取帧方式)
#
# 【关键事实】页面的栅格倍率由浏览器启动参数 --force-device-scale-factor 决定，
# 一旦画好就固定；`Emulation.setDeviceMetricsOverride` 里的 deviceScaleFactor
# 只是把已有图层**缩放**，不会重绘。实测：把 dpr 调 2 得到的 2880 帧，缩回 1x
# 与原生 1x 的差异只有 mean=0.24/255 —— 几乎是恒等变换，字还是虚的。
# 所以这里不用 dpr 控清晰度（页面恒为原生 2x），档位只决定"怎么取帧 + 压缩多狠"：
#   clear    截图轮询，q82 —— 保真最高，滚动实测 19fps
#   balanced 屏幕流，  q78 —— 事件驱动，静止零开销，适合长时间挂着
#   smooth   屏幕流，  q60 —— 体积最小，弱网/远程看的时候用
# 三档都是 2880 宽的帧（等于 1x 时代的 2 倍像素），只差取帧方式和压缩。
GEARS = {
    "clear": (82, "poll"),
    "balanced": (78, "screencast"),
    "smooth": (60, "screencast"),
}
GEAR = os.environ.get("BG_GEAR", "clear")
if GEAR not in GEARS:
    GEAR = "clear"


def cast_kind(name=None):
    """取帧方式：截图轮询（高清）还是 screencast（省 CPU）。

    【为什么高清必须用截图】Page.startScreencast 只按 CSS 尺寸出帧：设了
    deviceScaleFactor=2、maxWidth=2880，出的还是 1440×813，它不吃倍率。
    Page.captureScreenshot 才按物理像素出图。
    【为什么 1x 反而用 screencast】事件驱动，画面不动就完全不出帧，滚动时实测
    14.7fps，适合长时间挂着看。
    """
    if MODE in ("poll", "screencast"):
        return MODE
    return GEARS[name or S["gear"]][1]

# 记住"上次停在哪个标签"，服务重启后自动回到那里。
# 按端口分文件：同时跑两个实例（比如 9334 正式 + 9335 调试）互不干扰。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录：与 bhost.py 同一套规则 —— 默认就是技能根目录，BG_DATA 可指到别处。
# 两边必须用同一规则，否则读不到 bhost.pid（宿主状态是屏幕尺寸/启动模式的权威来源）。
DATA_DIR = os.path.expanduser(os.environ.get("BG_DATA") or ROOT)
STATE_FILE = os.environ.get("BG_STATE") or os.path.join(
    DATA_DIR, f".last-tab-{PORT}.json"
)
PIDFILE = os.path.join(DATA_DIR, "bhost.pid")


def host_screen():
    """从宿主的 bhost.pid 里取真实屏幕尺寸（CSS px）。

    【为什么不读页面的 screen】Emulation 一旦被摘掉（换 session、attach 失败），
    页面报回来的 screen 会掉回无头默认的 800×600 —— 那正是要修的矛盾值。拿它
    回填 override 会正反馈放大：实测把视口从 1440 一路推到 2880、5760，页面按
    2880 CSS 宽排版，等于把网站缩放了一半。宿主状态文件才是权威来源。
    """
    try:
        with open(PIDFILE) as f:
            d = json.load(f)
        s = d.get("screen") or []
        if len(s) == 2 and int(s[0]) >= 1024 and int(s[1]) >= 700:
            return int(s[0]), int(s[1])
    except Exception:
        pass
    return 1512, 982

S = {
    "pw": None,
    "browser": None,
    "ctx": None,
    "page": None,
    "cdp": None,
    "tid": None,   # 当前页的 CDP targetId（标签增删会让下标位移，id 不会）
    "gen": 0,      # 代际：每次 attach 自增，用来丢弃旧 session 迟到的帧
    "frame": None,
    "seq": 0,
    "w": 0,        # CSS 视口宽（点击/滚轮坐标的基准，单位永远是 CSS px）
    "h": 0,
    "dpr": 1.0,    # 页面当前 devicePixelRatio
    "screen": host_screen(),   # screen.width/height（CSS px），只从宿主状态文件取
    "fw": 0,       # 最近一帧的物理像素宽（只用于展示，绝不参与坐标换算）
    "fh": 0,
    "gear": GEAR,  # 当前清晰度档位
    "cast": "screencast",  # 实际取帧方式（由 cast_kind 决定）
    "poll_token": None,    # 轮询循环的令牌，换标签/换档时置空即让旧循环自杀
    "wake": 0.0,           # 用户刚操作过的截止时间，期间轮询保持高帧率
    "dragging": False,     # 指针是否处于按下状态（决定 mouseMoved 的 buttons）
    "idx": 0,
    "ticks": [],
    "err": None,
    "tasks": set(),
}
LOCK = asyncio.Lock()


def _spawn(coro):
    t = asyncio.create_task(coro)
    S["tasks"].add(t)
    t.add_done_callback(S["tasks"].discard)
    return t


def fps():
    now = time.time()
    S["ticks"] = [t for t in S["ticks"] if now - t < 2.0]
    return round(len(S["ticks"]) / 2.0, 1)


# ---------------------------------------------------------------- 记忆上次标签


def load_state():
    """读回上次记住的标签。文件坏了当没有，绝不因此起不来。"""
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_SAVED = load_state()


def save_state(**kw):
    """记住当前标签：tid 优先，url / index 兜底。

    只在值真的变了才落盘（attach 会被频繁调用），写 tmp 再 rename，
    避免进程被杀时留下半个 JSON 读不回来。
    """
    d = dict(_SAVED)
    d.update({k: v for k, v in kw.items() if v is not None})
    if d == _SAVED:
        return
    _SAVED.clear()
    _SAVED.update(d)
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


async def pick_start():
    """重启后回到上次那个标签：targetId → 同 URL → 上次下标 → 第一个。

    tid 是 Chrome 级标识，服务重启、Playwright 重连后都不变，所以这是最准的一条；
    浏览器整个重启过（tid 会变）就退到按 URL 找，用户开了「恢复上次会话」也能命中。
    """
    tid = _SAVED.get("tid")
    if tid:
        pg, _ = await find_page(tid)
        if pg is not None:
            return tid
    url = _SAVED.get("url")
    if url:
        for pg in S["ctx"].pages:
            try:
                if pg.url == url:
                    return await tid_of(pg) or 0
            except Exception:
                pass
    idx = _SAVED.get("index")
    pages = S["ctx"].pages
    if isinstance(idx, int) and 0 <= idx < len(pages):
        return idx
    return 0


# ---------------------------------------------------------------- 连接与附着


async def connect():
    """连上后台浏览器的 CDP 端点。失败由 keeper 重试，不影响服务存活。"""
    b = await S["pw"].chromium.connect_over_cdp(CDP_ENDPOINT)
    ctx = b.contexts[0] if b.contexts else await b.new_context()
    S["browser"], S["ctx"], S["err"] = b, ctx, None
    await attach(await pick_start())


async def tid_of(page):
    """取标签的 CDP targetId，缓存在 Page 对象上。

    实测 ctx.pages 每次返回的是同一批 Page 实例（is 相同），所以这个属性
    缓存有效，同一标签整个生命周期只问一次 Chrome。
    """
    t = getattr(page, "_bg_tid", None)
    if t:
        return t
    try:
        s = await S["ctx"].new_cdp_session(page)
        try:
            r = await s.send("Target.getTargetInfo")
            t = r["targetInfo"]["targetId"]
        finally:
            await s.detach()
    except Exception:
        return None
    try:
        page._bg_tid = t
    except Exception:
        pass
    return t


async def find_page(sel):
    """按 targetId 或下标找标签，返回 (page, index)。id 优先。"""
    pages = S["ctx"].pages
    if isinstance(sel, str) and sel:
        for i, pg in enumerate(pages):
            if await tid_of(pg) == sel:
                return pg, i
        return None, -1
    if sel is None:
        return None, -1
    i = max(0, min(int(sel), len(pages) - 1))
    return pages[i], i


async def measure(cdp):
    """量当前页的 CSS 视口 / screen / dpr。

    【为什么坐标基准必须来自这里】screencast 元数据里的 deviceWidth 是**物理**
    像素（dpr=2 时是 2880），而 CDP 的 Input.dispatch* 收的是 CSS px。拿元数据
    做换算，点一个位置会偏一倍。所以 S["w"]/S["h"] 只认 innerWidth/innerHeight，
    元数据只拿来显示。
    """
    r = await cdp.send(
        "Runtime.evaluate",
        {
            "expression": "({w:innerWidth,h:innerHeight,dpr:devicePixelRatio,"
                          "sw:screen.width,sh:screen.height})",
            "returnByValue": True,
        },
    )
    v = (r or {}).get("result", {}).get("value") or {}
    w, h = int(v.get("w") or 0), int(v.get("h") or 0)
    # 【必须夹住】视口只接受「笔记本屏幕量级」的值。Emulation 被摘掉时页面会
    # 报出 2880 这种物理宽度，一旦回填给 override 就会翻倍自激（实测 1440→5760），
    # 页面按 2880 CSS 宽排版、字号全变成一半。超出合理范围就沿用上一次的值。
    if 640 <= w <= 1920 and 400 <= h <= 1400:
        S["w"], S["h"] = w, h
    if v.get("dpr"):
        S["dpr"] = float(v["dpr"])
    return v


async def start_cast(cdp, quality):
    """开（或重开）screencast。

    maxWidth/maxHeight 按物理像素给足：只给 CSS 宽会让 Chrome 把画面缩回 1x
    再编码。不过实测 screencast 本来就只出 CSS 尺寸，这两个值目前是保险。
    """
    w = S["w"] or 1440
    h = S["h"] or 900
    dsf = S["dpr"] or 1
    await cdp.send(
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": int(quality),
            "maxWidth": max(1, round(w * dsf)),
            "maxHeight": max(1, round(h * dsf)),
            "everyNthFrame": 1,
        },
    )


async def apply_override(cdp, dsf):
    """把视口尺寸 / screen 报数对齐，并把 deviceScaleFactor 钉在页面原生倍率。

    【为什么必须传原生倍率、绝不能调低】screencast 和截图都会拿「当前 session
    的倍率」缩放已有图层，而不是重新栅格化。把 2 调成 1 再调回 2，第二次拿到的
    仍然是 1x 图放大 —— 实测 2880 帧缩回 1x 与原生 1x 差异仅 mean=0.24/255。
    所以这里只做「对齐」，清晰度由浏览器启动参数 --force-device-scale-factor
    决定，档位只管取帧方式和压缩质量。

    【为什么每条 session 都要设】这个状态是 per CDP session 的；宿主 bhost 在他
    自己 session 上设过，本 session 视角仍是默认值，出的帧会退回 1x（实测）。
    """
    ov = {
        "width": S["w"] or 1440,
        "height": S["h"] or 900,
        "deviceScaleFactor": dsf,
        "mobile": False,
    }
    sw, sh = S["screen"]
    if sw and sh:
        ov["screenWidth"], ov["screenHeight"] = sw, sh
    await cdp.send("Emulation.setDeviceMetricsOverride", ov)
    S["dpr"] = dsf


async def set_gear(cdp, name):
    """切换清晰度。

    只换取帧方式和压缩质量，**不动 deviceScaleFactor** —— 页面始终保持原生
    2x 栅格，所以来回切档不会把画面"降级成放大图"（这是之前踩的坑）。
    """
    q, _ = GEARS[name]
    S["gear"], S["frame"] = name, None
    S["poll_token"] = None   # 旧轮询循环自杀
    try:
        await cdp.send("Page.stopScreencast")
    except Exception:
        pass
    kind = cast_kind(name)
    S["cast"] = kind
    if kind == "poll":
        _spawn(poll_loop())
    else:
        await start_cast(cdp, q)


def host_mode():
    """读宿主浏览器（bhost.py）的启动模式，值来自同目录的 bhost.pid。

    headed 模式下激活 target 会真把窗口弹到前台抢焦点，必须跳过；
    headless 下没有可见窗口，激活不存在副作用 —— 反而是截图正常的前提。
    """
    try:
        with open(PIDFILE, "r", encoding="utf-8") as f:
            return json.load(f).get("mode") or ""
    except Exception:
        return ""


async def attach(sel):
    """切到某个标签页并（重）启动取帧。

    sel 可以是 CDP targetId（str）或 ctx.pages 下标（int）。
    id 优先：关掉中间某个标签后，后面所有下标都会前移，用下标会切错页。

    竞态说明：切换时先让代际号自增，旧 session 迟到的帧因 gen 不匹配被丢弃。
    否则会出现「点了标签、画面还停在上一页」——因为旧回调已 spawn 出去、
    正在 await，等它回来时全局 frame 已经被新页接管，它还是把旧帧写了进去。

    关键：只用 new_cdp_session 附着，**不调用 Target.activateTarget**，
    所以浏览器窗口不会被激活、不会抢焦点。

    例外（2026-10-27 实测修正）：headless 宿主**必须**激活 target，否则非活跃
    标签截出纯白。见本函数内 bring_to_front 那段注释。
    """
    ctx = S["ctx"]
    if ctx is None or not ctx.pages:
        return
    page, idx = await find_page(sel)
    if page is None:
        return
    old = S.get("cdp")
    S["poll_token"] = None   # 先叫停可能还在跑的轮询循环
    S["dragging"] = False    # 切标签/关标签时按下态一定已经失效，别带到新页面
    if old is not None:
        try:
            await old.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await old.detach()
        except Exception:
            pass

    S["gen"] += 1
    gen = S["gen"]

    # 【headless 必须激活 target】Chrome 会丢弃非活跃标签的合成层，
    # Page.captureScreenshot 只能拍回纯白（实测 31268 字节、明暗恒为 255/255），
    # 白图哈希永远相同 → seq 不递增 → /stream 再不推帧 → 前端永久空白 + 0fps。
    # 首屏只有一个标签时看不出问题，一旦开出第二个标签（含 profile 恢复的旧标签）
    # 就必然踩到 —— 这正是「控制台一片空白」的真凶。
    # headed 模式跳过：那才是真的会抢焦点，保持原有承诺。
    if host_mode() == "headless":
        try:
            await page.bring_to_front()
        except Exception:
            pass

    cdp = await ctx.new_cdp_session(page)

    def _on_frame_sync(p):
        if gen != S["gen"]:
            return  # 迟到的旧帧，丢弃
        _spawn(on_frame(p, gen))

    cdp.on("Page.screencastFrame", _on_frame_sync)
    tid = await tid_of(page)
    S.update(page=page, cdp=cdp, idx=idx, frame=None, tid=tid)
    try:
        cur_url = page.url
    except Exception:
        cur_url = None
    save_state(tid=tid, index=idx, url=cur_url)
    # 先量 CSS 视口再开流：点击换算的基准和 maxWidth 都要用它
    q, _ = GEARS[S["gear"]]
    try:
        await measure(cdp)
        # 用页面自己的原生倍率回填，绝不下调 —— 下调只会拿到缩放后的糊图
        await apply_override(cdp, S["dpr"] or 1)
    except Exception:
        pass
    kind = cast_kind()
    S["cast"] = kind
    if kind == "poll":
        _spawn(poll_loop())
        return
    await start_cast(cdp, q)


def jpeg_size(b):
    """从 JPEG 头里读真实像素宽高。

    只用来展示「这一帧到底多少像素」，顺手能验证 dpr 有没有生效。
    刻意不用 screencast 元数据的 deviceWidth：那个字段的含义在 CSS px / 物理 px
    之间没有可靠文档，而 Input 坐标要的是 CSS px，混用就会点偏。
    """
    try:
        i = b.find(b"\xff\xd8\xff")
        if i < 0:
            return None
        j = i + 2
        end = min(len(b), i + 4096)
        while j < end - 9:
            if b[j] != 0xFF:
                j += 1
                continue
            mk = b[j + 1]
            if mk in (0xC0, 0xC1, 0xC2):  # SOF0/1/2
                h = (b[j + 5] << 8) | b[j + 6]
                w = (b[j + 7] << 8) | b[j + 8]
                return w, h
            if mk == 0xD8:
                j += 2
                continue
            j += 2 + ((b[j + 2] << 8) | b[j + 3])
    except Exception:
        pass
    return None


async def on_frame(p, gen):
    if gen != S["gen"]:
        return
    try:
        data = base64.b64decode(p["data"])
        S["frame"] = data
        S["seq"] += 1
        wh = jpeg_size(data)
        if wh:
            S["fw"], S["fh"] = wh
        S["ticks"].append(time.time())
        del S["ticks"][:-120]
    except Exception:
        pass
    try:
        await S["cdp"].send(
            "Page.screencastFrameAck", {"sessionId": p["sessionId"]}
        )
    except Exception:
        pass


async def poll_loop():
    """高清取帧：定时 captureScreenshot，帧哈希不变就自动降频。

    【为什么高清档必须走它】screencast 只按 CSS 尺寸出帧，设 dpr 无效；截图吃
    dpr，2x 下出 2880×1626。实测 2x/q82：单帧 182KB、52ms、约 19fps —— 比 1x
    的 screencast（14.7fps）还快，因为少了一道合成器投递。

    【自适应节流】静止页面没必要 19fps：帧哈希没变就把间隔拉到 0.30s，变了立刻
    回到 0.04s。滚动跟手，挂机不烧 CPU（静止约 3 次截图/秒）。
    哈希不变时连 seq 都不加，前端连重绘都省了。

    【令牌】切标签 / 切档位时 S["poll_token"] 被换掉，旧循环下一次检查就退出，
    否则两个循环会互相覆盖帧（attach 的 gen 守卫只挡迟到帧，挡不住活着的循环）。
    """
    token = object()
    S["poll_token"] = token
    gen = S["gen"]
    prev = None
    FAST, SLOW = 0.04, 0.30
    while S.get("poll_token") is token and gen == S["gen"]:
        cdp = S.get("cdp")
        if cdp is None:
            await asyncio.sleep(0.5)
            continue
        me = cdp
        same = False
        try:
            r = await cdp.send(
                "Page.captureScreenshot",
                {"format": "jpeg", "quality": GEARS[S["gear"]][0]},
            )
            data = base64.b64decode(r["data"])
            if S.get("poll_token") is not token:
                return  # 截图期间被换挡了，这一帧丢掉
            h = hashlib.blake2b(data, digest_size=8).digest()
            same = h == prev
            if not same:
                prev = h
                S["frame"] = data
                S["seq"] += 1
                wh = jpeg_size(data)
                if wh:
                    S["fw"], S["fh"] = wh
                S["ticks"].append(time.time())
                del S["ticks"][:-120]
        except Exception as e:
            S["err"] = f"{type(e).__name__}: {e}"
        if S.get("cdp") is not me or S.get("poll_token") is not token:
            return
        # 每次操作后 2 秒内保持高帧率：否则静止降到 0.3s 一次后，用户一点一输入
        # 要等最多 0.3 秒才看到反应，手感像卡了。
        # 睡眠切成 30ms 小段，是为了让「刚点了」这件事能立刻打断长睡 —— 直接
        # sleep(0.3) 的话点击最多要等 0.3 秒才出下一帧。
        awake = time.time() < S.get("wake", 0.0)
        end = time.time() + (FAST if (awake or not same) else SLOW)
        while time.time() < end:
            if S.get("poll_token") is not token or S.get("cdp") is not me:
                return
            if time.time() < S.get("wake", 0.0):
                break
            await asyncio.sleep(0.03)


async def probe_alive(timeout=3.0):
    """真判活：发一条轻量 CDP 命令并带超时。

    不能信 Playwright 的 is_connected() / ctx.pages —— 浏览器外部退出后它们
    都会说谎（pr.py 里踩过一模一样的坑：is_connected() 仍回 True、pages 回
    陈旧列表），结果就是服务一直挂在死连接上，永远不重连。
    """
    cdp = S.get("cdp")
    if cdp is None or S.get("browser") is None:
        return False
    try:
        await asyncio.wait_for(
            cdp.send("Runtime.evaluate", {"expression": "1"}), timeout
        )
        return True
    except Exception:
        return False


def drop():
    """丢弃当前连接引用。

    故意不调 browser.close()：对 connect_over_cdp 拿到的 Browser 调 close 有
    连带关掉宿主浏览器的风险（那正是我们要保命的东西），交给 GC 回收 WebSocket。
    """
    S["browser"] = S["ctx"] = S["cdp"] = S["page"] = None
    S["frame"] = None
    S["seq"] = 0
    S["w"] = S["h"] = 0
    # 断开时一定要清按下态：否则重连后第一批 mouseMoved 会带着 buttons=1，
    # 页面会以为用户一直按着左键（实测表现为「莫名其妙选中一片文字」）。
    S["dragging"] = False


async def keeper():
    """保活：宿主浏览器重启后自动重新挂上，无需手动重启本服务。"""
    while True:
        if not await probe_alive():
            drop()
            try:
                await connect()
            except Exception as e:
                S["err"] = f"{type(e).__name__}: {e}"
        await asyncio.sleep(2)


# ---------------------------------------------------------------- HTTP 与 API


async def api(route, body):
    d = json.loads(body or b"{}") or {}
    # 用户一动手就唤醒高帧率，见 poll_loop 里的 awake
    if route in ("click", "mouse", "wheel", "text", "key", "goto", "reload", "back",
                 "tab", "new", "close", "gear"):
        S["wake"] = time.time() + 2.0
    cdp = S.get("cdp")
    if cdp is None:
        return {"error": "浏览器未连接"}
    if route == "state":
        pages = []
        try:
            for i, pg in enumerate(S["ctx"].pages):
                try:
                    title = await pg.title()
                except Exception:
                    title = ""
                pages.append(
                    {
                        "index": i,
                        "id": await tid_of(pg),
                        "url": pg.url,
                        "title": title,
                    }
                )
        except Exception:
            pass
        cur = S.get("page")
        try:
            cur_url = cur.url if cur is not None else ""
        except Exception:
            cur_url = ""
        # 地址变了就同步进状态文件：浏览器整个重启后 tid 会失效，
        # 那时靠 URL 还能找回同一个页面。值没变不会真写盘。
        if cur_url:
            save_state(url=cur_url)
        return {
            "connected": True,
            "url": cur_url,
            "w": S["w"],
            "h": S["h"],
            "dpr": round(float(S["dpr"]), 2),
            "fw": S["fw"],
            "fh": S["fh"],
            "gear": S["gear"],
            "fps": fps(),
            "mode": MODE,
            "cast": S["cast"],
            "idx": S["idx"],
            "tid": S.get("tid"),
            "seq": S["seq"],
            "pages": pages,
        }
    if route == "goto":
        url = (d.get("url") or "").strip()
        if "://" not in url:
            url = "http://" + url
        await cdp.send("Page.navigate", {"url": url})
        return {"ok": True}
    if route == "reload":
        await cdp.send("Page.reload", {"ignoreCache": False})
        return {"ok": True}
    if route == "back":
        await cdp.send("Runtime.evaluate", {"expression": "history.back()"})
        return {"ok": True}
    if route == "gear":
        name = d.get("gear")
        if name not in GEARS:
            return {"error": f"未知档位：{name}"}
        try:
            await set_gear(cdp, name)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "gear": name}
    if route == "tab":
        await attach(d.get("id") or d.get("index", 0))
        return {"ok": True, "idx": S["idx"], "tid": S.get("tid")}
    if route == "new":
        page = await S["ctx"].new_page()
        await attach(await tid_of(page) or len(S["ctx"].pages) - 1)
        return {"ok": True, "idx": S["idx"], "tid": S.get("tid")}
    if route == "close":
        pages = list(S["ctx"].pages)
        if len(pages) <= 1:
            return {"error": "只剩一个标签页，不关"}
        pg, i = await find_page(d.get("id") or d.get("index", 0))
        if pg is None:
            return {"error": "没找到要关的标签页"}
        # 关当前页前先切到相邻页，否则 screencast 会挂在正在销毁的 target 上
        if pg is S.get("page"):
            nxt = pages[1] if i == 0 else pages[i - 1]
            await attach(await tid_of(nxt) or 0)
        try:
            await pg.close()
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return {"ok": True}
    if route == "click":
        x, y = float(d.get("x", 0)), float(d.get("y", 0))
        bt = "left"
        for t in ("mousePressed", "mouseReleased"):
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": t,
                    "x": x,
                    "y": y,
                    "button": bt,
                    "clickCount": 1,
                    "buttons": 1 if t == "mousePressed" else 0,
                },
            )
        return {"ok": True}
    if route == "mouse":
        # 指针事件流：down → move（可批量点位）→ up。
        #
        # 【为什么必须有这条】验证码滑块（百度「拖动滑块匹配曲线」这类）要的是
        # 「按下后连续移动」。老的 click 只发 mousePressed + mouseReleased、两点
        # 坐标相同，等于在滑块上点了一下 —— 画面里滑块一动不动，看起来就像「拖不动」。
        #
        # 【为什么不用合成轨迹】风控会看轨迹曲率、加速度、时间间隔、末端微调。
        # 这里转发的是用户本人在面板上的真实鼠标轨迹，天然带人手特征。
        t = d.get("type") or "move"

        def xy(p):
            return float(p.get("x", 0)), float(p.get("y", 0))

        if t == "down":
            x, y = xy(d)
            # 先补一条移动到按下点：真实鼠标按下前一定是从别处移过来的，不少
            # 拖拽组件要靠 hover/mouseenter 才初始化滑块状态，缺这条会「按不动」。
            await cdp.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0},
            )
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 1,
                    "clickCount": 1,
                },
            )
            S["dragging"] = True
        elif t == "up":
            x, y = xy(d)
            await cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 0,
                    "clickCount": 1,
                },
            )
            S["dragging"] = False
        else:
            # 按住与否决定 buttons：0/1。传错的话页面会把「拖」当成「悬停」。
            held = 1 if d.get("held", S.get("dragging")) else 0
            pts = d.get("pts") or [d]
            for p in pts:
                x, y = xy(p)
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseMoved",
                        "x": x,
                        "y": y,
                        "button": "none",
                        "buttons": held,
                    },
                )
            return {"ok": True, "n": len(pts)}
        return {"ok": True}
    if route == "wheel":
        await cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": float(d.get("x", S["w"] / 2)),
                "y": float(d.get("y", S["h"] / 2)),
                "deltaX": float(d.get("dx", 0)),
                "deltaY": float(d.get("dy", 0)),
            },
        )
        return {"ok": True}
    if route == "text":
        await cdp.send("Input.insertText", {"text": str(d.get("text", ""))})
        return {"ok": True}
    if route == "key":
        vk = int(d.get("vk", 0))
        key = d.get("key", "")
        code = d.get("code", key)
        base = {
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
            "key": key,
            "code": code,
        }
        await cdp.send("Input.dispatchKeyEvent", dict(base, type="rawKeyDown"))
        await cdp.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))
        return {"ok": True}
    return {"error": f"unknown api: {route}"}


async def send(writer, code, ctype, body):
    head = (
        f"HTTP/1.1 {code} OK\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Cache-Control: no-store\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    writer.write(head + body)
    await writer.drain()


async def serve_stream(writer):
    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
        b"Cache-Control: no-store\r\n"
        b"Pragma: no-cache\r\n"
        b"Connection: keep-alive\r\n\r\n"
    )
    await writer.drain()
    last = -1
    while True:
        seq = S["seq"]
        if seq != last and S["frame"]:
            last = seq
            f = S["frame"]
            # 帧前必须有 CRLF：multipart 的标准分隔是 \r\n--boundary，
            # 少了这个前导 CRLF，Chrome 的 <img> 会一直解析不出第一帧（实测）。
            writer.write(
                b"\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(f)).encode()
                + b"\r\n\r\n"
                + f
                + b"\r\n"
            )
            await writer.drain()
        await asyncio.sleep(0.02)


async def handle(reader, writer):
    try:
        line = await reader.readline()
        if not line:
            return
        parts = line.decode("latin1").split()
        if len(parts) < 2:
            return
        method, path = parts[0], parts[1]
        n = 0
        while True:
            h = await reader.readline()
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode("latin1").partition(":")
            if k.lower().strip() == "content-length":
                n = int(v.strip())
        body = await reader.readexactly(n) if n else b""
        u = urlparse(path)
        if u.path == "/stream":
            await serve_stream(writer)
            return
        if u.path in ("/", "/index.html"):
            await send(writer, 200, "text/html; charset=utf-8", UI.encode())
            return
        if u.path.startswith("/api/"):
            async with LOCK:
                r = await api(u.path[5:], body)
            await send(writer, 200, "application/json; charset=utf-8",
                       json.dumps(r, ensure_ascii=False).encode())
            return
        await send(writer, 404, "text/plain; charset=utf-8", b"not found")
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    except Exception as e:
        try:
            await send(writer, 500, "application/json",
                       json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 前端


UI = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>后台浏览器 · 实时投屏</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{height:100vh;display:flex;flex-direction:column;font:13px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#1e293b;background:#f1f5f9;overflow:hidden}
.bar{display:flex;gap:8px;align-items:center;padding:8px 12px;background:#fff;border-bottom:1px solid #e2e8f0;flex:none}
.bar input{flex:1;min-width:0;padding:7px 10px;border:1px solid #cbd5e1;border-radius:8px;font-size:13px;outline:none;background:#f8fafc}
.bar input:focus{border-color:#6366f1;background:#fff;box-shadow:0 0 0 3px rgba(99,102,241,.12)}
button{padding:7px 14px;border:1px solid #cbd5e1;background:#fff;border-radius:8px;cursor:pointer;font-size:13px;color:#334155}
button:hover{background:#f8fafc;border-color:#94a3b8}
button.primary{background:#6366f1;border-color:#6366f1;color:#fff}
button.primary:hover{background:#4f46e5}
select{padding:7px 8px;border:1px solid #cbd5e1;border-radius:8px;font-size:12px;color:#334155;background:#fff;cursor:pointer;outline:none}
select:hover{border-color:#94a3b8}
.main{flex:1;display:flex;min-height:0}
.tabbar{display:flex;align-items:flex-end;gap:1px;padding:6px 6px 0;background:#dee1e6;flex:none;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin}
.tabbar::-webkit-scrollbar{height:4px}
.tabbar::-webkit-scrollbar-thumb{background:#bcc0c6;border-radius:2px}
.tab{display:flex;align-items:center;gap:6px;height:34px;padding:0 8px 0 10px;border-radius:8px 8px 0 0;cursor:pointer;max-width:220px;min-width:44px;flex:0 1 190px;color:#5f6368;font-size:12px;background:transparent;transition:background .12s}
.tab:hover{background:#cdd1d7}
.tab.on{background:#fff;color:#202124}
.tab .fav{width:15px;height:15px;flex:none;border-radius:3px;object-fit:contain;display:block}
.tab .fav.ph{display:flex;align-items:center;justify-content:center;background:#8b93a1;color:#fff;font-size:9px;font-weight:600;text-transform:uppercase}
.tab .t{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tab .x{flex:none;width:16px;height:16px;line-height:15px;text-align:center;border-radius:50%;font-size:13px;color:#5f6368;opacity:0;transition:opacity .12s}
.tab:hover .x,.tab.on .x{opacity:1}
.tab .x:hover{background:#dfe1e5}
.newtab{flex:none;width:28px;height:28px;margin:0 2px 3px;border:none;border-radius:50%;background:transparent;color:#4a4f57;font-size:18px;line-height:1;cursor:pointer;padding:0}
.newtab:hover{background:#cdd1d7}
.stage{position:relative;flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:10px;background:#e2e8f0}
#view{max-width:100%;max-height:100%;object-fit:contain;background:#fff;border-radius:6px;box-shadow:0 1px 6px rgba(15,23,42,.12);cursor:crosshair;display:block;transition:opacity .15s;-webkit-user-drag:none;user-select:none;-webkit-user-select:none;touch-action:none}
#view.switching{opacity:.35}
.mask{position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:#475569;font-size:13px;background:rgba(226,232,240,.5);pointer-events:none}
.mask.on{display:flex}
.foot{flex:none;display:flex;gap:14px;padding:5px 12px;background:#fff;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#22c55e;margin-right:5px;vertical-align:middle}
.dot.off{background:#ef4444}
</style></head><body>
<div class="tabbar" id="tabs"></div>
<div class="bar">
  <button onclick="back()" title="后退">←</button>
  <button onclick="reload()" title="刷新">↻</button>
  <input id="url" placeholder="输入网址或本机端口，如 127.0.0.1:3000" autocomplete="off">
  <button class="primary" onclick="go()">打开</button>
  <select id="gear" title="清晰=高清截帧（保真最高）；均衡=屏幕流（静止零开销）；省流=低压缩体积">
    <option value="clear">清晰</option>
    <option value="balanced">均衡</option>
    <option value="smooth">省流</option>
  </select>
</div>
<div class="main">
  <div class="stage"><img id="view" alt="实时画面" draggable="false"><div class="mask" id="mask">切换中…</div></div>
</div>
<div class="foot">
  <span><span class="dot" id="dot"></span><span id="stat">连接中…</span></span>
  <span id="size"></span><span id="fps"></span><span id="mode"></span>
  <span style="margin-left:auto">点击画面即可操作 · 支持滚轮 / 键盘</span>
</div>
<script>
const $=id=>document.getElementById(id);
const view=$('view'), urlIn=$('url');
let S={w:0,h:0,fw:0,fh:0}, lastUrl='';

async function api(p,b){try{const r=await fetch('/api/'+p,{method:'POST',body:JSON.stringify(b||{})});return await r.json()}catch(e){return{error:String(e)}}}
function go(){const u=urlIn.value.trim(); if(u) api('goto',{url:u})}
function reload(){api('reload')}
function back(){api('back')}

// 切换/新建/关闭时压低画面亮度，避免「旧画面停留」造成误判
let maskT=null;
function showMask(ms){$('mask').classList.add('on');view.classList.add('switching');
  clearTimeout(maskT);maskT=setTimeout(hideMask,ms||1500)}
function hideMask(){clearTimeout(maskT);$('mask').classList.remove('on');view.classList.remove('switching')}
async function pick(id,idx){showMask();await api('tab',id?{id}:{index:idx})}
async function newTab(){showMask();await api('new')}
// 切清晰度：服务端改 deviceScaleFactor + 重开 screencast，页面布局和滚动位置都不动
async function setGear(g){showMask(4000);const r=await api('gear',{gear:g});if(r&&r.error)alert(r.error)}
async function closeTab(e,id,idx){e.stopPropagation();showMask();
  const r=await api('close',id?{id}:{index:idx}); if(r&&r.error) alert(r.error)}
urlIn.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();go()}});

async function tick(){
  const s=await api('state');
  if(s.error||!s.connected){
    $('dot').className='dot off'; $('stat').textContent=s.error||'未连接';
    return;
  }
  $('dot').className='dot';
  $('stat').textContent='已连接';
  S.w=s.w; S.h=s.h;
  if(s.fw){S.fw=s.fw; S.fh=s.fh}   // 帧的真实像素尺寸，坐标换算要用它算白边
  // 左：CSS 视口（点击基准）；右：帧的真实像素（清晰度的证据）
  $('size').textContent = s.w+'×'+s.h + (s.dpr&&s.dpr!==1 ? ' @'+s.dpr+'x' : '')
    + (s.fw ? ' · 帧 '+s.fw+'×'+s.fh : '');
  const g=$('gear');
  if(s.gear && g.value!==s.gear && document.activeElement!==g) g.value=s.gear;
  $('fps').textContent=s.fps+' fps';
  // 取帧方式：截图轮询出物理像素（真 2x），屏幕流只出 CSS 尺寸但静止零开销
  $('mode').textContent = s.cast==='poll' ? '高清截图' : '屏幕流';
  if(s.url && s.url!==lastUrl){lastUrl=s.url; if(document.activeElement!==urlIn) urlIn.value=s.url}
  const tabs=$('tabs');
  // 用签名比较：标题/地址变化也要重绘，否则标签改名了列表还是旧的
  const sig=s.pages.map(p=>(p.id||'')+'|'+p.title+'|'+p.url).join('\n')+'#'+s.tid+'#'+s.idx;
  if(tabs.dataset.sig!==sig){
    tabs.dataset.sig=sig;
    // 高亮认 targetId 不认下标：关掉中间的标签后下标会整体前移
    tabs.innerHTML=s.pages.map(p=>{
      const on = s.tid ? p.id===s.tid : p.index===s.idx;
      return `<div class="tab${on?' on':''}" onclick="pick('${p.id||''}',${p.index})" title="${esc(p.url)}">`
        + favOf(p.url)
        + `<div class="t">${esc(p.title||'(无标题)')}</div>`
        + `<div class="x" onclick="closeTab(event,'${p.id||''}',${p.index})">×</div></div>`;
    }).join('') + `<button class="newtab" onclick="newTab()" title="新建标签页">＋</button>`;
    const cur=tabs.querySelector('.tab.on');
    if(cur) cur.scrollIntoView({block:'nearest',inline:'nearest'});
  }
}
function esc(t){return String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function originOf(u){try{const x=new URL(u);return (x.protocol==='http:'||x.protocol==='https:')?x.origin:''}catch(e){return ''}}
function favFail(el,ch){const s=document.createElement('span');s.className='fav ph';s.textContent=ch;el.replaceWith(s)}
// 直接向目标站要 favicon：<img> 不受同源限制，失败就退化成域名首字母块
function favOf(u){
  const o=originOf(u);
  if(!o) return '<span class="fav ph">?</span>';
  let ch='?';
  try{ch=new URL(o).hostname.replace(/^www\./,'').charAt(0)||'?'}catch(e){}
  return `<img class="fav" src="${o}/favicon.ico" alt="" onerror="favFail(this,'${ch}')">`;
}

// ---- 输入：点击 + 拖拽 ----
// 【为什么不能只有一个 click】验证码滑块（百度「拖动滑块匹配曲线」那类）要的是
// 「按下 → 连续移动 → 松开」。只发 click 等于在滑块上点了一下，画面里纹丝不动。
// 这里把用户真实的指针轨迹按 ~10ms 一批转发出去：真人手速就是最好的反检测。
//
// 【为什么用 Promise 链】每条 POST 是独立连接，并发发出会乱序到达，页面收到的
// 轨迹就会前后跳。串成一条链既保证顺序，不满时自动排队合并。
let chain = Promise.resolve(), drag = null, lastDragEnd = 0;
function ptOf(e){
  // 【必须按 contain 后的真实内容框换算】画面是 object-fit:contain 铺在 rect 里，
  // 容器比例和帧比例不一致时会有留白（上下或左右）。按整个 rect 直接映射的话，
  // 所有点击/拖拽都会整体偏移 —— 实测偏出 4px 就抓不到滑块手柄。
  const r = view.getBoundingClientRect();
  const nw = view.naturalWidth || S.fw || S.w, nh = view.naturalHeight || S.fh || S.h;
  const k = Math.min(r.width/nw, r.height/nh);
  const dw = nw*k, dh = nh*k;
  const ox = r.left + (r.width-dw)/2, oy = r.top + (r.height-dh)/2;
  return {x:(e.clientX-ox)/dw*S.w, y:(e.clientY-oy)/dh*S.h};
}
function enq(body){ chain = chain.then(()=>api('mouse', body)).catch(()=>{}); }

// 攒点批量发：逐点单独 POST 会有上百次往返，批量后仍然近似实时
setInterval(()=>{
  if(!drag || !drag.pts.length) return;
  enq({type:'move', pts:drag.pts.splice(0), held:true});
},10);

view.addEventListener('pointerdown', e=>{
  if(e.button!==0 || !S.w || !S.h) return;
  // 必须掐掉默认行为：不掐的话浏览器会先起「原生图片拖拽」，pointer 序列
  // 直接被 dragstart 掐断 —— 手感上就是「拖着拖着丢了」。
  e.preventDefault();
  const p = ptOf(e);
  drag = {id:e.pointerId, x0:p.x, y0:p.y, moved:false, pts:[]};
  try{ view.setPointerCapture(e.pointerId); }catch(_){}
  enq({type:'down', x:p.x, y:p.y});
});
view.addEventListener('pointermove', e=>{
  if(!drag || e.pointerId!==drag.id) return;
  const p = ptOf(e);
  if(!drag.moved && Math.hypot(p.x-drag.x0, p.y-drag.y0) > 3) drag.moved = true;
  drag.pts.push(p);
});
view.addEventListener('pointerup', e=>{
  if(!drag || e.pointerId!==drag.id) return;
  const p = ptOf(e), d = drag; drag = null;
  // 千万别在这里丢点：最后几个点就是「末端微调」，滑块风控很看这一段
  if(d.pts.length) enq({type:'move', pts:d.pts.splice(0), held:true});
  enq({type:'up', x:p.x, y:p.y});
  if(d.moved) lastDragEnd = Date.now();
});
view.addEventListener('pointercancel', e=>{
  if(!drag) return;
  const p = ptOf(e), d = drag; drag = null;
  enq({type:'up', x:p.x, y:p.y});
  if(d.moved) lastDragEnd = Date.now();
});
// 【不要再单独转发 click】按下+松开这一对事件本身就会让远端页面生成一次 click
// （老 /api/click 走的就是 press+release）。这里再加一条 click 转发，每次点击都会
// 变成双击 —— 实测按钮计数会从 1 跳到 2。
view.addEventListener('wheel',e=>{
  e.preventDefault();
  const r=view.getBoundingClientRect();
  api('wheel',{x:S.w/2,y:S.h/2,dx:e.deltaX,dy:e.deltaY});
},{passive:false});

const KEYS={Enter:[13,'Enter','Enter'],Backspace:[8,'Backspace','Backspace'],Tab:[9,'Tab','Tab'],
Escape:[27,'Escape','Escape'],ArrowLeft:[37,'ArrowLeft','ArrowLeft'],ArrowUp:[38,'ArrowUp','ArrowUp'],
ArrowRight:[39,'ArrowRight','ArrowRight'],ArrowDown:[40,'ArrowDown','ArrowDown'],
Delete:[46,'Delete','Delete'],Home:[36,'Home','Home'],End:[35,'End','End'],' ':[32,' ','Space']};
document.addEventListener('keydown',e=>{
  if(document.activeElement===urlIn) return;
  e.preventDefault();
  if(e.metaKey||e.ctrlKey||e.altKey) return;
  if(KEYS[e.key]){const[k,key,code]=KEYS[e.key];api('key',{vk:k,key,code})}
  else if(e.key.length===1){api('text',{text:e.key})}
});

// ---- 画面流 ----
// 不用 <img src="/stream">：Chrome 156 的 img 解析不了 multipart/x-mixed-replace
// （实测 fetch 能拿到完整 51KB 数据、img 却始终 naturalWidth=0），改成自己读流拆 JPEG。
let curObj=null;
function drawFrame(bytes){
  const url=URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
  const old=curObj;
  view.src=url; curObj=url;
  if(old) setTimeout(()=>URL.revokeObjectURL(old),2000);
  if(view.classList.contains('switching')) hideMask();
}
function find3(b,from){for(let i=from;i<b.length-2;i++)if(b[i]===0xFF&&b[i+1]===0xD8&&b[i+2]===0xFF)return i;return -1}
function find2(b,a,c,from){for(let i=from;i<b.length-1;i++)if(b[i]===a&&b[i+1]===c)return i;return -1}

async function startStream(){
  if(window.__ac) window.__ac.abort();
  const ac=new AbortController(); window.__ac=ac;
  try{
    const res=await fetch('/stream',{signal:ac.signal,cache:'no-store'});
    const rd=res.body.getReader();
    let buf=new Uint8Array(0);
    while(true){
      const {value,done}=await rd.read();
      if(done) break;
      const nb=new Uint8Array(buf.length+value.length);
      nb.set(buf); nb.set(value,buf.length); buf=nb;
      for(;;){
        const s=find3(buf,0);
        if(s<0){ if(buf.length>4) buf=buf.slice(buf.length-4); break }
        const e=find2(buf,0xFF,0xD9,s+3);
        if(e<0){ if(s>0) buf=buf.slice(s); break }
        drawFrame(buf.slice(s,e+2));
        buf=buf.slice(e+2);
      }
    }
  }catch(e){}
  // 流断了自动重连：服务重启、浏览器重启都不用刷新页面
  if(!ac.signal.aborted) setTimeout(startStream,800);
}

tick(); setInterval(tick,1500);
startStream();
</script></body></html>"""


async def main():
    from patchright.async_api import async_playwright

    S["pw"] = await async_playwright().start()
    _spawn(keeper())
    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    print(f"投屏控制台: http://127.0.0.1:{PORT}  (CDP={CDP_ENDPOINT}, mode={MODE})",
          flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
