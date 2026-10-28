#!/bin/bash
# bg-browser 常驻安装脚本（launchd LaunchAgent x2）
#
# 【为什么必须你自己在系统终端里跑】
# WorkBuddy 会话内的进程与 launchd 的 mach 通道是断开的：实测连一个只有
# /bin/echo 的最小 plist 都报 "Bootstrap failed: 5: Input/output error"，
# 且 `launchctl list` 输出为空（正常 macOS 有几十条）。这是宿主 App 的上下文
# 限制，不是 plist 写错，在会话里怎么调都起不来。
# 打开「终端 / Terminal.app」，粘贴运行：
#
#     bash ~/.workbuddy/skills/bg-browser/install.sh
#
# 卸载：
#     bash ~/.workbuddy/skills/bg-browser/install.sh uninstall
#
set -u

# 一律按脚本自身位置定位，不假设装在哪 —— 从技能市场安装时，
# 这个目录是 ~/.workbuddy/plugins/cache/<市场>/<插件>/<版本>/ 之类，
# 和开发期的手动放置路径完全不同。
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SCRIPTS="$SKILL_DIR/scripts"
DATA_DIR="${BG_DATA:-$SKILL_DIR}"
LA="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"

# Python 解释器：BG_PY 优先 → WorkBuddy 自带 → 系统 python3。
# 挑错解释器的后果是启动即 ModuleNotFoundError: patchright，所以逐个试。
pick_python() {
    for c in "${BG_PY:-}" "$HOME/.workbuddy/binaries/python/envs/default/bin/python" "$(command -v python3 2>/dev/null)"; do
        [ -n "$c" ] && [ -x "$c" ] && printf '%s' "$c" && return 0
    done
    return 1
}
PY="$(pick_python || true)"
HOST_LABEL="com.workbuddy.bg-browser.host"
SCREEN_LABEL="com.workbuddy.bg-browser.screen"
CDP_PORT=9444
UI_PORT=9334

say() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

uninstall() {
    for L in "$HOST_LABEL" "$SCREEN_LABEL"; do
        if launchctl bootout "gui/$UID_N/$L" 2>/dev/null; then
            say "✓ 已停止 $L"
        else
            say "· $L 本来就没在跑"
        fi
    done
    rm -f "$LA/$HOST_LABEL.plist" "$LA/$SCREEN_LABEL.plist"
    say "✓ 已删除 plist"
    say "（浏览器进程已随服务停止；profile 和登录态保留在 $DATA_DIR/profile）"
    exit 0
}

[ "${1:-install}" = "uninstall" ] && uninstall

# ---- 前置校验 ----
[ -n "$PY" ] || die "找不到 python3：请先安装，或用 BG_PY 指定解释器"
"$PY" -c 'import patchright' 2>/dev/null || die "这个解释器没装 patchright：$PY
    安装：$PY -m pip install patchright
    或换一个已装好的：BG_PY=/path/to/python bash $0"

CHROME_OK=""
for c in "${BG_CHROME:-}" \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"; do
    if [ -n "$c" ] && [ -x "$c" ]; then CHROME_OK="$c"; break; fi
done
[ -n "$CHROME_OK" ] || die "没找到 Chrome 系浏览器。装了别的 Chromium 内核浏览器请用 BG_CHROME=/路径 指定"

[ -f "$SCRIPTS/bhost.py" ] || die "找不到 $SCRIPTS/bhost.py"
[ -f "$SCRIPTS/screen.py" ] || die "找不到 $SCRIPTS/screen.py"
mkdir -p "$LA" "$DATA_DIR" || die "无法创建 $LA 或 $DATA_DIR"

# ---- 生成 plist ----
write_host_plist() {
    cat > "$LA/$HOST_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$HOST_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PY</string>
		<string>$SCRIPTS/bhost.py</string>
		<string>start</string>
		<string>--url</string>
		<string>about:blank</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$SCRIPTS</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>BG_DATA</key>
		<string>$DATA_DIR</string>
	</dict>
	<key>StandardOutPath</key>
	<string>$DATA_DIR/bhost.log</string>
	<key>StandardErrorPath</key>
	<string>$DATA_DIR/bhost.log</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ProcessType</key>
	<string>Interactive</string>
</dict>
</plist>
EOF
}

write_screen_plist() {
    cat > "$LA/$SCREEN_LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$SCREEN_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PY</string>
		<string>$SCRIPTS/screen.py</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$SCRIPTS</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>BG_CDP</key>
		<string>http://127.0.0.1:$CDP_PORT</string>
		<key>BG_PORT</key>
		<string>$UI_PORT</string>
		<key>BG_DATA</key>
		<string>$DATA_DIR</string>
	</dict>
	<key>StandardOutPath</key>
	<string>$DATA_DIR/screen.log</string>
	<key>StandardErrorPath</key>
	<string>$DATA_DIR/screen.log</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ProcessType</key>
	<string>Interactive</string>
</dict>
</plist>
EOF
}

write_host_plist
write_screen_plist
say "✓ 已生成 plist：$LA/$HOST_LABEL.plist"
say "✓ 已生成 plist：$LA/$SCREEN_LABEL.plist"

# ---- 先卸载旧实例，保证幂等 ----
for L in "$HOST_LABEL" "$SCREEN_LABEL"; do
    launchctl bootout "gui/$UID_N/$L" 2>/dev/null && say "· 已清理旧实例 $L"
done
sleep 1

# ---- 注册 ----
ok=0
fail=0
for L in "$HOST_LABEL" "$SCREEN_LABEL"; do
    if launchctl bootstrap "gui/$UID_N" "$LA/$L.plist" 2>/dev/null; then
        say "✓ 已注册 $L"
        ok=$((ok + 1))
    else
        say "✗ 注册失败 $L"
        fail=$((fail + 1))
    fi
done

if [ "$fail" -gt 0 ]; then
    say ""
    say "注册失败，试试这两个排查："
    say "  1) 确认是在「终端」里跑，不是 WorkBuddy 会话"
    say "  2) 查看 launchd 拒绝原因： log stream --predicate 'process == \"launchd\"' --level debug"
    exit 1
fi

# ---- 等待就绪 ----
say ""
say "等待浏览器起来（最多 30 秒）…"
ready=0
for _ in $(seq 1 30); do
    if curl -s --noproxy '*' -m 2 -o /dev/null "http://127.0.0.1:$CDP_PORT/json/version"; then
        ready=1
        break
    fi
    sleep 1
done

if [ "$ready" = 1 ]; then
    say "✓ CDP 已就绪 http://127.0.0.1:$CDP_PORT"
else
    say "✗ CDP 30 秒内没起来，看日志：$DATA_DIR/bhost.log"
fi

for _ in $(seq 1 15); do
    if curl -s --noproxy '*' -m 2 -o /dev/null "http://127.0.0.1:$UI_PORT/"; then
        say "✓ 控制台已就绪 http://127.0.0.1:$UI_PORT"
        break
    fi
    sleep 1
done

say ""
say "完成。控制台：http://127.0.0.1:$UI_PORT"
say "  开机自启：是（RunAtLoad）"
say "  崩溃自动拉起：是（KeepAlive）"
say "  日志：$DATA_DIR/bhost.log  $DATA_DIR/screen.log"
say "  卸载：bash $SKILL_DIR/install.sh uninstall"
