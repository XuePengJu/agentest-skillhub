#!/usr/bin/env python3
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""patchright-browser 常驻控制服务（Patchright 驱动，反检测）

架构：单线程 HTTPServer + Patchright sync API（同线程执行，规避 greenlet 线程限制）。
所有操作走 POST JSON，返回精简 JSON，超长自动截断（PR_MAX_OUT，默认 4000 字符）。

启动：python3 pr.py serve        （建议 Bash run_in_background）
停止：curl -X POST 127.0.0.1:9333/close

环境变量 PR_KEEP_AWAKE=1：保留 Playwright 默认的禁用渲染后台节流参数（旧行为，
空转可能占满一个 CPU 核心）；不设置时默认恢复 Chrome 遮挡/后台节流以省电省 CPU。
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PR_PORT", "9333"))
# profile 落在固定的用户级路径（不受安装位置影响，升级/重装不丢登录态）；PR_PROFILE 可覆盖
PROFILE = os.path.expanduser(
    os.environ.get("PR_PROFILE") or "~/.workbuddy/skills/patchright-browser/profile"
)
SHOT_DIR = os.environ.get("PR_SHOT_DIR", "/tmp")
# cookie / storage_state 落盘目录。**故意不默认落在技能目录内**：技能目录可能被整体
# 打包发布到公开仓库，而 cookie 是明文登录凭证。见 _cookie_path() 的硬拦。
COOKIE_DIR = os.environ.get("PR_COOKIE_DIR", "/tmp")
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_OUT = int(os.environ.get("PR_MAX_OUT", "4000"))

STATE = {"pw": None, "ctx": None, "chrome_pid": None, "lock_seen": False}


def _pid_alive(pid):
    """进程是否存在：True / False / None（无法判定）。"""
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:      # 存在但无权限发信号
        return True
    except OSError:
        return None


def _lock_pid():
    """读 profile 的进程单例文件，取出当时持有它的 Chrome 主进程 PID（拿不到返回 None）。"""
    lock = os.path.join(PROFILE, "SingletonLock")
    try:
        if not os.path.islink(lock):
            return None
        return int(os.readlink(lock).rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return None


def _lock_state():
    """单例锁给出的判据：True=锁在且进程活 / False=锁在但进程已死 / None=无锁或不可解析。"""
    pid = _lock_pid()
    if pid is None:
        return None
    return False if _pid_alive(pid) is False else True


def browser_alive():
    """浏览器是否真的还活着：True / False / None（本平台拿不到判据）。

    实测（2026-10-16）浏览器消失后，patchright 三个官方信号全都不可信：
    ctx.pages 返回**陈旧**页面列表、ctx.browser.is_connected() 仍回 True、
    context.close / browser.disconnected 事件都不触发。后果是服务永不退出、
    /status 恒报 running:true（假阳性），而任何真实操作会永久挂起把单线程服务堵死。

    判活按可靠性依次降级：
      1) launch() 时记录下的 Chrome 主进程 PID（最直接，且能覆盖"锁被删掉"的情形）
      2) profile/SingletonLock -> "<hostname>-<pid>"：锁在且 pid 活 => 活；
         锁在但 pid 已死 => 死（强杀 / 崩溃留下的悬空链接）
      3) 启动时锁存在过、现在没了 => Chrome 已优雅退出并自行清理 => 死
      4) 都不适用 => None，由调用方退回 ctx.pages 旧判据

    已知局限：判据 1 依赖 PID 未被回收；极端情况下（Chrome 退出后该 PID 恰好被
    无关进程复用）会误判为仍存活 —— 概率极低，且退化后的行为与旧版一致。
    """
    if STATE.get("ctx") is None:
        return False
    rec = STATE.get("chrome_pid")
    if rec:
        a = _pid_alive(rec)
        if a is False:
            return False        # 强杀 / 崩溃 / 优雅退出，全覆盖
        if a is True:
            return True
    st = _lock_state()
    if st is not None:
        return st
    if STATE.get("lock_seen"):
        return False            # 启动时有锁、现在没锁 => Chrome 已清理并退出
    return None


def _pages_nonempty():
    ctx = STATE.get("ctx")
    try:
        return bool(ctx.pages) if ctx is not None else False
    except Exception:  # 浏览器已被 Cmd+Q 杀掉，访问 pages 会抛
        return False


def _serving():
    """服务是否还有可用浏览器；判据缺失（None）时退回旧的 ctx.pages 判定。"""
    if STATE.get("ctx") is None:
        return False
    alive = browser_alive()
    return _pages_nonempty() if alive is None else alive

# 恢复 Chrome 遮挡/后台节流：只剔除渲染相关的两个默认 flag，
# 保留 --disable-background-timer-throttling 以免 Playwright 计时器漂移。
UNTHROTTLE_ARGS = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


def _trunc(obj):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > MAX_OUT:
        s = s[:MAX_OUT] + f'...[截断,共{len(s)}字符,可用PR_MAX_OUT调大]'
    return s


def launch():
    from patchright.sync_api import sync_playwright

    headless = os.environ.get("PR_HEADLESS", "0") == "1"
    STATE["pw"] = sync_playwright().start()
    STATE["chrome_pid"] = None
    STATE["lock_seen"] = False
    # 启动策略：
    # - 默认走 --no-sandbox + --test-type（WorkBuddy 进程树内 Chrome 原生沙箱必失败，
    #   --test-type 压掉"不受支持的命令行标记"提示栏，避免视口高度差异被启发式检测）
    # - PR_CLEAN=1 时先尝试剔除 --no-sandbox 的完全干净启动（适合在用户终端直接跑）
    # - 默认恢复渲染后台节流（剔除下面两个默认 flag 省 CPU）；PR_KEEP_AWAKE=1 时保留旧行为
    unthrottle = [] if os.environ.get("PR_KEEP_AWAKE", "0") == "1" else list(UNTHROTTLE_ARGS)
    attempts = []
    if os.environ.get("PR_CLEAN", "0") == "1":
        attempts.append({"channel": "chrome",
                         "ignore_default_args": ["--no-sandbox"] + unthrottle})
    for base in ({"channel": "chrome", "args": ["--test-type"]},
                 {"args": ["--test-type"]}):
        if unthrottle:
            base["ignore_default_args"] = list(unthrottle)
        attempts.append(base)
    ctx = None
    last_err = None
    for extra in attempts:
        try:
            ctx = STATE["pw"].chromium.launch_persistent_context(
                user_data_dir=PROFILE, headless=headless, no_viewport=True, **extra
            )
            mode = extra.get("channel", "chromium") + (
                "+clean" if "--no-sandbox" in extra.get("ignore_default_args", []) else "+test-type"
            ) + ("+throttled" if unthrottle else "+awake")
            print(f"launched mode: {mode}", flush=True)
            break
        except Exception as e:
            last_err = e
    if ctx is None:
        raise last_err
    STATE["ctx"] = ctx
    # 记录 Chrome 主进程 PID —— 这是后续判活的**主判据**。不能只依赖单例锁：
    # 用户优雅退出（Cmd+Q）时 Chrome 会把锁文件删掉，此时锁判据会失效并退回
    # ctx.pages，而 pages 在浏览器外部退出时是陈旧的（实测仍返回 1）。
    for _ in range(40):                 # 最多等 10s，正常 <1s 即出现
        pid = _lock_pid()
        if pid:
            STATE["chrome_pid"] = pid
            STATE["lock_seen"] = True
            break
        time.sleep(0.25)
    return ctx


def cur_page():
    ctx = STATE["ctx"]
    if not ctx.pages:
        ctx.new_page()
    return ctx.pages[-1]


def op_goto(p):
    import time

    page = cur_page()
    for i in range(2):  # 浏览器刚启动首航可能竞态，失败重试一次
        try:
            page.goto(p["url"], wait_until=p.get("wait_until", "domcontentloaded"),
                      timeout=p.get("timeout", 30000))
            break
        except Exception:
            if i == 1:
                raise
            time.sleep(1)
    if p.get("wait"):
        page.wait_for_selector(p["wait"], timeout=p.get("timeout", 15000))
    return {"title": page.title(), "url": page.url}


def op_click(p):
    cur_page().locator(p["selector"]).first.click(timeout=p.get("timeout", 10000))
    return {"ok": True}


def op_fill(p):
    cur_page().locator(p["selector"]).first.fill(p["text"], timeout=p.get("timeout", 10000))
    return {"ok": True}


def op_eval(p):
    return {"result": cur_page().evaluate(p["expr"])}


def op_extract(p):
    page = cur_page()
    limit = int(p.get("limit", 200))
    els = page.locator(p["css"])
    n = min(els.count(), limit)
    attr = p.get("attr")
    items = [(els.nth(i).get_attribute(attr) if attr else els.nth(i).inner_text()) for i in range(n)]
    return {"count": n, "total": els.count(), "items": items}


def op_shot(p):
    page = cur_page()
    name = p.get("name") or f"pr-shot-{int(__import__('time').time())}.png"
    path = os.path.join(SHOT_DIR, name)
    page.screenshot(path=path, full_page=bool(p.get("full")))
    return {"path": path}


def op_tabs(p):
    ctx = STATE["ctx"]
    act = p.get("op", "list")
    if act == "new":
        pg = ctx.new_page()
        if p.get("url"):
            pg.goto(p["url"], wait_until="domcontentloaded")
        return {"index": len(ctx.pages) - 1, "url": pg.url}
    if act == "switch":
        idx = int(p["index"])
        ctx.pages[idx].bring_to_front()
        return {"index": idx, "url": ctx.pages[idx].url}
    if act == "close":
        idx = int(p.get("index", len(ctx.pages) - 1))
        ctx.pages[idx].close()
        return {"remaining": len(ctx.pages)}
    return {"pages": [{"index": i, "url": pg.url, "title": pg.title()} for i, pg in enumerate(ctx.pages)]}


def _cookie_path(p, kind):
    """cookie 落盘路径。默认 /tmp，PR_COOKIE_DIR 可改，p["save"] 显式指定优先。

    硬拦写入技能目录：技能目录会被 scripts/import_skill.py 整体打包进**公开仓库**
    （排除列表只有 profile / *.app / _icon.*），把明文 cookie 写进去等于公开发布凭证。
    """
    ext = "json" if kind == "json" else "txt"
    path = p.get("save") or os.path.join(
        COOKIE_DIR, f"pr-cookies-{int(time.time())}.{ext}"
    )
    path = os.path.abspath(os.path.expanduser(path))
    if path == SKILL_ROOT or path.startswith(SKILL_ROOT + os.sep):
        raise ValueError(
            f"拒绝写入技能目录内（{path}）：该目录会被打包进公开仓库，"
            f"请改用 /tmp 或 PR_COOKIE_DIR"
        )
    return path


def _fmt_header(cs):
    """Cookie: 请求头形态，可直接贴给 curl -H。"""
    return "; ".join(f'{c["name"]}={c["value"]}' for c in cs)


def _fmt_netscape(cs):
    """Netscape cookie 文件格式，可直接 curl -b / yt-dlp --cookies / requests 使用。"""
    out = ["# Netscape HTTP Cookie File", "# 由 patchright-browser 生成，含明文凭证", ""]
    for c in cs:
        dom = c.get("domain") or ""
        # Netscape 的 #HttpOnly_ 前缀是 httpOnly 的唯一表达方式（curl 认这个前缀）
        name = ("#HttpOnly_" + dom, c["name"]) if c.get("httpOnly") else (dom, c["name"])
        exp = int(c.get("expires") or 0)
        if exp < 0:                      # Playwright 用 -1 表示会话 cookie，Netscape 用 0
            exp = 0
        out.append("\t".join([
            name[0],
            "TRUE" if dom.startswith(".") else "FALSE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(exp),
            name[1],
            c["value"],
        ]))
    return "\n".join(out) + "\n"


def op_cookies(p):
    """读 cookie。走 CDP 的 Storage.getCookies，在浏览器进程层执行，绕开页面 JS 沙箱
    ⇒ HttpOnly 与跨域对它都不存在（这是它与 /eval document.cookie 的本质差别）。

    默认**只回摘要、值落盘**：cookie 动辄数 KB，_trunc 会把 JSON 从中间切断，
    调用方拿到坏数据却不报错。需要内容时读 path，或显式 inline:true。
    """
    ctx = STATE["ctx"]
    urls = p.get("urls") or ([p["url"]] if p.get("url") else None)
    cs = ctx.cookies(urls) if urls else ctx.cookies()
    if p.get("domain"):
        d = p["domain"].lstrip(".").lower()
        cs = [c for c in cs if d in (c.get("domain") or "").lstrip(".").lower()]
    if p.get("name"):
        cs = [c for c in cs if c["name"] == p["name"]]
    if p.get("limit"):
        cs = cs[: int(p["limit"])]
    fmt = p.get("format", "json")
    if fmt == "header":
        text = _fmt_header(cs)
    elif fmt == "netscape":
        text = _fmt_netscape(cs)
    else:
        fmt = "json"
        text = json.dumps(cs, ensure_ascii=False, indent=2)
    path = _cookie_path(p, fmt)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.chmod(path, 0o600)                # 明文凭证，只给自己读
    r = {
        "count": len(cs),
        "format": fmt,
        "path": path,
        "bytes": len(text.encode()),
        "httpOnly": sum(1 for c in cs if c.get("httpOnly")),
        "domains": sorted({c.get("domain") or "" for c in cs})[:20],
    }
    if p.get("inline"):
        r["data"] = text
    else:
        r["names"] = [c["name"] for c in cs][:50]
    return r


def op_cookies_set(p):
    """写 cookie。接受 Playwright 原生数组，或 header 字符串 + url。"""
    if p.get("cookies"):
        cs = p["cookies"]
    elif p.get("header"):
        from urllib.parse import urlsplit

        if not p.get("url"):
            raise ValueError("header 形态必须同时给 url，否则无法确定 cookie 的 domain")
        host = urlsplit(p["url"]).hostname
        if not host:
            raise ValueError(f"无法从 url 解析 hostname：{p['url']}")
        cs = []
        for seg in p["header"].split(";"):
            seg = seg.strip()
            if "=" in seg:
                k, v = seg.split("=", 1)
                if k.strip():
                    cs.append({"name": k.strip(), "value": v.strip(),
                               "domain": host, "path": "/"})
    else:
        raise ValueError("需要 cookies 数组，或 header 字符串（配 url）")
    for c in cs:
        # Playwright 明确禁止 url 与 domain/path 同时出现，提前拦下给出可读错误
        if c.get("url") and (c.get("domain") or c.get("path")):
            raise ValueError(f"cookie {c.get('name')}: url 与 domain/path 不能同时指定")
        if not c.get("name"):
            raise ValueError("cookie 缺 name")
    STATE["ctx"].add_cookies(cs)
    return {"ok": True, "count": len(cs)}


def op_cookies_clear(p):
    """清 cookie。**必须带过滤条件或显式 all:true**，防止手滑清光全部登录态。"""
    flt = {k: p[k] for k in ("name", "domain", "path") if p.get(k)}
    if not flt:
        if not p.get("all"):
            raise ValueError('未指定过滤条件；确认清空全部请显式传 {"all": true}')
        STATE["ctx"].clear_cookies()
        return {"ok": True, "cleared_by": "ALL"}
    STATE["ctx"].clear_cookies(**flt)
    return {"ok": True, "cleared_by": flt}


def op_storage(p):
    """导出 storage_state（cookies + 各 origin 的 localStorage），用于整套登录态搬家。"""
    path = _cookie_path(p, "json")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    STATE["ctx"].storage_state(path=path, indexed_db=bool(p.get("indexed_db")))
    os.chmod(path, 0o600)
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    return {
        "path": path,
        "cookies": len(st.get("cookies") or []),
        "origins": [o.get("origin") for o in (st.get("origins") or [])][:20],
        "bytes": os.path.getsize(path),
    }


def op_status(p):
    ctx = STATE["ctx"]
    if ctx is None or not _serving():
        return {"running": False}
    return {"running": True, "pages": [{"index": i, "url": pg.url} for i, pg in enumerate(ctx.pages)]}


OPS = {"goto": op_goto, "click": op_click, "fill": op_fill, "eval": op_eval,
       "extract": op_extract, "shot": op_shot, "tabs": op_tabs, "status": op_status,
       "cookies": op_cookies, "cookies/set": op_cookies_set,
       "cookies/clear": op_cookies_clear, "storage": op_storage}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志，省磁盘
        pass

    def _send(self, code, obj):
        body = _trunc(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            self._send(200, OPS["status"]({}))
        else:
            self._send(404, {"error": "unknown route"})

    def do_POST(self):
        route = self.path.strip("/")
        if route == "close":
            self._send(200, {"ok": True, "bye": True})
            try:
                # 浏览器已死（False）时不要再去 close/stop：可能是死等的调用，
                # 会把这个逃生出口也一起堵住。直接退出即可。
                if browser_alive() is not False:
                    STATE["ctx"].close()
                    STATE["pw"].stop()
            except Exception:
                pass
            os._exit(0)
        fn = OPS.get(route)
        if not fn:
            self._send(404, {"error": f"unknown route: {route}",
                             "routes": sorted(OPS) + ["close"]})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, fn(params))
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def run_ui(server):
    """--ui 模式：窗口即生命周期。最后一个 Chrome 窗口关闭（或 Cmd+Q）→ 服务整体退出。"""
    import time
    server.timeout = 1
    grace_until = time.time() + 10  # 启动初期 pages 可能瞬间为空，给宽限期
    print("ui mode: close last window to quit", flush=True)
    while True:
        server.handle_request()
        # 判活用 _serving()（浏览器进程是否还在），不要只看 ctx.pages：
        # 浏览器被强杀后 pages 会一直返回陈旧值，只看它会导致服务永不退出。
        alive = _serving() and _pages_nonempty()
        if not alive and time.time() >= grace_until:
            break
    try:
        STATE["ctx"].close()
        STATE["pw"].stop()
    except Exception:
        pass
    os._exit(0)


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        print(__doc__)
        return
    # 单例保护（两层，缺一不可）：
    # 1) 裸 socket 探端口 —— 不经过任何代理。踩过的坑：原先只用 urllib 探测，而本机沙箱环境
    #    有全局 http_proxy，探测请求被发给代理 → 永远误判"没有实例在跑" → 每次都真的另起一个
    #    浏览器，随后端口绑定失败又退出 ⇒ 表现为"窗口一闪即关"。
    # 2) 端口已被占用 → 直接退出，绝不再启动浏览器（宁可不启动，也不要重复启动）。
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    in_use = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    if in_use:
        print(f"already running on 127.0.0.1:{PORT} (port in use), exit", flush=True)
        return
    ctx = launch()
    try:
        server = HTTPServer(("127.0.0.1", PORT), Handler)
    except Exception:
        # 绑定失败（端口竞态）时务必关掉刚起的浏览器，避免留下"没人控制"的幽灵窗口
        try:
            ctx.close()
            STATE["pw"].stop()
        except Exception:
            pass
        raise
    print(f"patchright-browser serving on 127.0.0.1:{PORT}, profile={PROFILE}", flush=True)
    if "--ui" in sys.argv:
        run_ui(server)
    else:
        server.serve_forever()


if __name__ == "__main__":
    main()
