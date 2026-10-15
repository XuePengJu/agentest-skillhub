---
name: patchright-browser
description: 反检测浏览器操作技能（Patchright 驱动）。当普通抓取遇到 Cloudflare/DataDome 等风控墙、强反爬站点（小红书/闲鱼等敏感操作）、需要隐身身份隔离于日常浏览器，或需要渲染/点击/登录态时使用。本技能通过常驻控制服务暴露精简 CLI，token 消耗远低于 MCP 方案。日常搜索、读静态页优先 WebFetch/curl，不要用本技能。
version: 1.0.0
license: MIT
display_name: "Patchright 隐身浏览器"
display_name_en: "Patchright Stealth Browser"
---

# Patchright 隐身浏览器技能

Patchright = 打过反检测补丁的 Playwright（去 Runtime.enable 泄漏、去自动化标记）。本技能用常驻 HTTP 控制服务（默认 `127.0.0.1:9333`）+ curl 一行命令操作，输出精简省 token。

**约定**：下文 `$SKILL_DIR` 是**文档占位符、不是环境变量** —— 它指本 SKILL.md 所在目录（即技能根目录），执行命令时请替换为实际绝对路径或先 `export SKILL_DIR=<实际技能目录>`。随附脚本一律用自身路径定位，不依赖安装位置与当前工作目录。

**平台**：核心链路（Patchright + 控制服务）跨平台；脚本示例用 `bash`/`curl`，macOS 与 Linux 均可。文中「macOS 启动优化」一节仅涉 macOS。

## 路由边界（先读）

- 日常搜索、读内容、静态页 → **WebSearch / WebFetch / curl，禁止启动本技能**
- 需要渲染、点击、登录态、多步交互的普通操作 → **本技能（`/eval` + `/extract` 定向取值）**
- 遇到风控墙（Cloudflare 挑战页、403 反爬）、强风控站点操作、需要身份隔离 → **本技能（首选，唯一带隐身补丁）**

## 前置检查

```bash
PY="${PR_PY:-python3}"                                    # 用装了 patchright 的解释器，可用 PR_PY 指定
"$PY" -c "import patchright" 2>/dev/null || "$PY" -m pip install --no-cache-dir patchright
```

若未装系统 Chrome 内核且无 `channel="chrome"`，脚本自动回退 patchright 自带 chromium（隐身稍弱）；需要时执行 `"$PY" -m patchright install chromium`。

## 启动 / 停止

```bash
PY="${PR_PY:-python3}"

# 启动（常驻服务，须以后台任务方式跑，勿用 nohup 再包一层）
"$PY" "$SKILL_DIR/scripts/pr.py" serve

# 存活检查（GET）
curl -s 127.0.0.1:9333/status

# 停止
curl -s -X POST 127.0.0.1:9333/close
```

- 单例：重复启动自动让位（裸 socket 探端口，不走代理）
- profile 默认落在 `~/.workbuddy/skills/patchright-browser/profile/`，登录态跨会话保留；用 `PR_PROFILE` 可指定到别处，删除即重置身份
- 默认**有头**模式（过检率最高）；无头加环境变量 `PR_HEADLESS=1`
- 换端口：`PR_PORT=9334`
- 启动策略：在受限进程树内 Chrome 原生沙箱可能初始化失败，故默认 `--no-sandbox` + `--test-type` 压掉"不受支持的命令行标记"提示栏（该提示栏会占 ~40px 视口，属可检测特征；页面 JS 读不到命令行本身）。在普通终端直接跑可用 `PR_CLEAN=1` 获得剔除 no-sandbox 的完全干净启动

### macOS 启动优化（进阶，可选）

若把浏览器封装成「双击即用」的 `.app`，注意一个 macOS 调度陷阱：`.app` 若为 `LSUIElement`（后台型）应用，**由 LaunchServices 直接启动时系统会把整棵进程树按"后台应用"降级调度 → `nice=5`**，在多核机器上只能挤到能效核，**Chrome 加载多资源页面慢 20~60 倍**（实测同一首页 26.2s/15.0s vs 0.52s/0.45s）。

解法是把启动器改为经 `launchctl bootstrap` 由 launchd 拉起（plist 放 `~/Library/LaunchAgents/`），即可拿到正常优先级 `nice=0`。

- 自检：查 `pr.py` 与 Chrome 主进程的 `nice`，**必须是 0**；若为 5 就是又被降级了
- 该 `.app` 启动器为作者本机自建，未随本技能发布；按上述原理自行封装即可

## 操作命令（POST JSON，一律 `curl -s`，建议带 `-m` 超时）

| 命令 | 示例 |
|---|---|
| 导航 | `curl -s -m 60 -X POST 127.0.0.1:9333/goto -d '{"url":"https://example.com","wait":"#main"}'` |
| 点击 | `curl -s -X POST 127.0.0.1:9333/click -d '{"selector":"button.submit"}'` |
| 填表 | `curl -s -X POST 127.0.0.1:9333/fill -d '{"selector":"#kw","text":"关键词"}'` |
| 执行 JS | `curl -s -X POST 127.0.0.1:9333/eval -d '{"expr":"document.title"}'` |
| 批量提取 | `curl -s -X POST 127.0.0.1:9333/extract -d '{"css":"h2 a","attr":"href","limit":50}'` |
| 截图 | `curl -s -X POST 127.0.0.1:9333/shot -d '{"full":false}'` → 返回图片路径 |
| 标签页 | `.../tabs -d '{"op":"list"}'` / `new` / `switch` / `close` |

完整参数说明见 `references/api_reference.md`。

## 省 token 约定

- 优先 `/extract`（带 `limit`、`attr`）拿结构化结果，`/eval` 只用于 extract 覆盖不了的逻辑
- 截图只回路径，需要看图再用 Read 工具打开
- 所有响应超 4000 字符自动截断（`PR_MAX_OUT` 可调），先靠 limit/attr 收窄而不是调大阈值

## 已知坑

- **打开网页极慢（nice=5 后台降级）**：见上文「macOS 启动优化」。诊断顺序：① 查 `pr.py`/Chrome 的 nice 是不是 5；② 若是 5 → 改用 launchd 拉起；③ 排除法（同 profile 副本 + 同参数另起一个实例，若秒开则证明是"实例被降级/状态异常"，而非 profile/参数/代理问题）
- **窗口一闪即关 / 反复开关**：旧版单例检测用 `urllib` 探测 `127.0.0.1:9333`，在有全局 `http_proxy` 的环境会被代理劫持 → 永远误判"没在跑" → 真的另起一个浏览器，随后端口绑定失败又退出。已改为**裸 socket 探端口**（不走代理）+ 绑定失败时关闭刚起的浏览器。若再现，先查日志里是否有连续多次 `ui mode:` 且缺失 `already running`
- **浏览器被强杀或彻底退出后，旧版服务不会自愈、会永久卡死**（2026-10-16 实测并已修）

  **症状**：端口仍在监听，`/status` 甚至仍回 `{"running": true, "pages": [...]}`（读到的是陈旧页面列表），但任何真实操作（`goto` 等）会**永久挂起** —— 单线程服务被这一个请求彻底堵死，表现为「9333 在监听但完全不通」。

  **根因**（做过双向验证：确认 kill 真的生效、Chrome 进程确已消失）：浏览器一消失，patchright 的**三个官方信号全都不可信** ——
  - `ctx.pages` 返回**陈旧**的页面列表（浏览器已死仍照报 1 页）
  - `ctx.browser.is_connected()` 仍返回 `True`
  - `context.on("close")` / `browser.on("disconnected")` **都不触发**

  于是旧版 `--ui` 的生命周期判定 `bool(ctx.pages)` 恒为真 → 服务永不退出。

  **修法（已内置于 `pr.py` 的 `browser_alive()` / `_serving()`，`/status` 与 `--ui` 都改用它）**：判活改看 Chrome 的**进程单例文件**，不再信 Playwright：

  | 状态 | `profile/SingletonLock` | `os.kill(pid, 0)` |
  |---|---|---|
  | 正常运行 | 符号链接 → `<hostname>-<pid>` | 成功 |
  | 干净退出 | Chrome 自行删除该文件 | — |
  | 被强杀 | **残留悬空符号链接** | `OSError` |

  判据缺失时（拿不到该文件）自动退回旧的 `ctx.pages` 判定，不会误判为"已死"。

  **排查已在跑旧版服务的实例**：

  ```bash
  lsof -nP -iTCP:9333 -sTCP:LISTEN    # 有监听
  curl -s -m 5 127.0.0.1:9333/status  # 旧版恒回 running:true，不可信
  ls -la <profile>/SingletonLock      # 悬空链接 => 浏览器早就死了
  tail <技能目录>/serve.log           # 会看到驱动栈 + Node.js vX
  kill <服务PID>                      # 处置：只能 kill（旧版连 /close 都可能卡住）
  ```

  **连带症状**：此时双击 `LSUIElement` 的 `.app` 会**毫无反应** —— 启动器单例检测误判「已在运行」直接退出，或（探测超时）交给 `pr.py`，而 `pr.py` 的端口探测发现端口被占同样直接退出。所以「双击没反应」先查这个，别急着怀疑启动器坏了。
- **单线程阻塞**：服务是单线程 `HTTPServer`，一个慢请求（如 goto 等慢站）会堵住后续所有请求。curl 侧务必带 `-m`（超时），长导航用 `{"timeout":毫秒}` 收窄 Playwright 侧等待
- **选择器过时**：不要凭记忆写 `#kw`/`#su` 这类旧版选择器（某搜索引擎新版首页输入框是 `#chat-textarea`，`#kw` 隐藏不可见导致 fill/click 超时）。先 `/eval` 用 `offsetParent!==null` 过滤出**可见**输入框/按钮，拿到真实选择器再 fill/click
- **截图偶发空响应**：结果页大图首次 `/shot` 可能无响应（服务被上一请求余波卡住），带 `-m 20` 重试一次即可成功
- **所有网站图标变成灰色地球（favicon 被吞）**：patchright 默认开启全局请求拦截，驱动 `coreBundle.js` 的 `requestStarted()` 里有 `if (request._isFavicon) { route?.abort("aborted"); return; }` —— 上游 Playwright 因默认不开拦截（`route2` 为 null），这行是死代码；patchright 开了拦截，于是**每个 `/favicon.ico` 都在发出网络请求前被真的掐断**（表现为 netlog 里 `CORS_REQUEST` 只到 phase=1 就没了下文）。**只有 `.ico` 受影响**，PNG/SVG/`<link rel=icon>` 图标正常 → 典型症状是"少数站点有图标、大多数没有"。修复：把 `abort("aborted")` 改成 `continue({})`（等价于不拦截该请求）。升级 patchright 会覆盖驱动文件，需重跑补丁：
  ```bash
  PY="${PR_PY:-python3}"
  "$PY" "$SKILL_DIR/scripts/reapply_favicon_fix.py"          # 打补丁（自动备份 .bak-faviconfix）
  "$PY" "$SKILL_DIR/scripts/reapply_favicon_fix.py" --check  # 只体检，退出码 0=在位
  curl -s --noproxy '*' -X POST 127.0.0.1:9333/close          # 重启生效
  ```
  验证：打开目标站点静置 ~20s，`<profile>/Default/Favicons` 里应新增该站 `favicon.ico`，且 `icon_mapping` 出现该站点。诊断口径：查 `icon_mapping`（页面→图标映射）而不是只看 `favicons`（去重后的图标 URL）。

## 隐身纪律

- 保持 `launch_persistent_context` + 独立 profile，不要改成 `launch + new_context`
- **不要设置自定义 user_agent**（默认即最优）
- 遇到挑战页：`goto` 后 sleep 3~5 秒再检查，不要高频刷新
- 操作节奏放慢：风控站点每步之间可 `sleep 1~3`

## 自检

```bash
PY="${PR_PY:-python3}"
"$PY" "$SKILL_DIR/scripts/selftest.py"                # 依赖 + 内核
"$PY" "$SKILL_DIR/scripts/selftest.py" --with-server  # 含端到端（会临时拉起服务）
```
