# patchright-browser 控制服务 API 参考

基础地址：`http://127.0.0.1:9333`（`PR_PORT` 可换）
约定：GET `/status` 存活检查；其余一律 `POST` JSON body；响应为精简 JSON，超 `PR_MAX_OUT`（默认 4000 字符）自动截断。

## 路由一览

| 路由 | 参数 | 返回 |
|---|---|---|
| `/goto` | `url`(必填), `wait`(selector), `wait_until`(默认 domcontentloaded), `timeout`(ms) | `{title, url}` |
| `/click` | `selector`(必填), `timeout` | `{ok:true}` |
| `/fill` | `selector`, `text`, `timeout` | `{ok:true}` |
| `/eval` | `expr`(JS 表达式字符串) | `{result}` |
| `/extract` | `css`(必填), `attr`(取属性而非文本), `limit`(默认200) | `{count, total, items[]}` |
| `/shot` | `full`(整页), `name`(文件名) | `{path}` 图片落 `/tmp` |
| `/tabs` | `op`: `list`/`new`/`switch`/`close`, `index`, `url` | 见下 |
| `/status` | 无（GET 或 POST 均可） | `{running, pages[]}`；**浏览器真的不在了会返回 `{running:false}`**（判活看 Chrome 进程单例，不信 `ctx.pages` / `is_connected()`，见下） |
| `/close` | 无 | 关浏览器并退出进程 |

## tabs 细节

- `{"op":"new","url":"..."}` → `{"index":n,"url":...}`（新页成为当前页）
- `{"op":"switch","index":1}` → 切换当前页（当前页 = 页面列表最后一个被 new/switch 的）
- `{"op":"close","index":1}` → 关闭指定页
- `{"op":"list"}` → 全部页的 index/url/title

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PR_PORT` | 9333 | 控制端口 |
| `PR_HEADLESS` | 0 | 1=无头（过检率下降） |
| `PR_MAX_OUT` | 4000 | 响应截断阈值（字符） |
| `PR_SHOT_DIR` | /tmp | 截图落盘目录 |
| `PR_PROFILE` | `~/.workbuddy/skills/patchright-browser/profile` | 浏览器用户数据目录（登录态所在，删即重置身份） |
| `PR_CLEAN` | 0 | 1=先尝试剔除 `--no-sandbox` 的干净启动（仅限用户终端直跑；受限进程树内 Chrome 原生沙箱必失败会回退） |

## 实现要点（改代码前必读）

- 单线程 `HTTPServer` + Patchright **sync API 同线程执行**——Patchright sync 用 greenlet（线程本地），严禁把浏览器操作挪到别的线程；要多线程必须改用 async API
- `launch_persistent_context` + `channel="chrome"`（系统 Chrome）+ `no_viewport=True`，失败自动回退自带 chromium
- 不设置自定义 user_agent；不注入 init script（Patchright 补丁已覆盖）
- `--no-sandbox` 在受限进程树内无法剔除（继承 macOS seatbelt 时 Chrome 原生沙箱初始化必失败），用 `--test-type` 压掉提示栏；验证方式：`outerHeight-innerHeight` 应为 ~87px（有提示栏 ~127px）
- profile 固定于 `~/.workbuddy/skills/patchright-browser/profile/`（`PR_PROFILE` 可改），删除即重置身份
- **判活不要用 Playwright 的 API**：浏览器被强杀/彻底退出后，`ctx.pages` 返回陈旧列表、`ctx.browser.is_connected()` 仍回 `True`、`close`/`disconnected` 事件都不触发（2026-10-16 实测）。`browser_alive()` 采用分层降级：① `launch()` 时记录的 Chrome 主进程 PID + `os.kill(pid, 0)`；② `profile/SingletonLock -> "<hostname>-<pid>"` + 验活；③ 启动时有锁、现在无锁 ⇒ 已优雅退出；④ 都不适用返回 `None`，由 `_serving()` 退回 `ctx.pages`。**①不可省**：优雅退出（Cmd+Q）时 Chrome 会自己删掉锁文件，只看锁会退回陈旧的 `ctx.pages` 而误报 `running: true`
- 数 Chrome 进程**别用** `lsof -c Chrome`（macOS 进程名是 `Google Chrome`，`-c` 只匹前 9 字符 ⇒ 恒为 0）；用 `lsof -c "Google Ch"` 或直接查 profile 占用
- `/close` 在浏览器已死时会跳过 `ctx.close()`/`pw.stop()`（对死浏览器调用可能卡住），直接退出进程
