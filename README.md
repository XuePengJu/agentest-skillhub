# agentest-skillhub

**agentest 技能市场** —— AI 应用开发与 Agent 工具的实战技能集合。

这是一个 WorkBuddy / Claude Code 通用的**插件市场（marketplace）仓库**：仓库根目录的
`.claude-plugin/marketplace.json` 声明市场与其中的插件，每个插件就是一个可安装的技能包。

- 站点：[agentest.vip](https://agentest.vip)
- 维护者：[@XuePengJu](https://github.com/XuePengJu)

## 安装

### WorkBuddy

在对话里说「添加市场」并给出仓库地址，或在插件管理界面点 **添加市场 → GitHub repo**
填 `XuePengJu/agentest-skillhub`。命令行等价写法：

```bash
codebuddy plugin marketplace add XuePengJu/agentest-skillhub
codebuddy plugin install patchright-browser@agentest-skillhub
```

### Claude Code

```bash
claude plugin marketplace add XuePengJu/agentest-skillhub
claude plugin install patchright-browser@agentest-skillhub
```

### 本地开发时

```bash
codebuddy plugin marketplace add ./agentest-skillhub
codebuddy plugin validate ./agentest-skillhub     # 校验清单
```

## 更新

推送到 `main` 后，使用者执行：

```bash
codebuddy plugin marketplace update agentest-skillhub
```

## 跨工具兼容

这个仓库不是 WorkBuddy 专属的，它按**两层**对外暴露能力，越往下兼容面越宽。

### 第一层：市场协议（直接 `marketplace add`）

`.claude-plugin/marketplace.json` 是 Anthropic 定义的插件市场格式，以下工具**原生识别**，
添加市场 + 安装插件各一条命令：

| 工具 | 添加市场 | 安装 |
| --- | --- | --- |
| WorkBuddy / CodeBuddy | `codebuddy plugin marketplace add XuePengJu/agentest-skillhub` | `codebuddy plugin install <插件>@agentest-skillhub` |
| Claude Code | `claude plugin marketplace add XuePengJu/agentest-skillhub` | `claude plugin install <插件>@agentest-skillhub` |
| Claude Desktop (Cowork) | 设置 → Plugins & skills → Add marketplace → GitHub repo | 面板内点击安装 |

> 选 `.claude-plugin` 而不是 `.codebuddy-plugin` 正是为了这一层兼容 —— WorkBuddy 源码里
> 这三种目录名都认，而 Claude Code 只认 `.claude-plugin`。

### 第二层：技能体（`SKILL.md`）

`SKILL.md` 是开放标准（Agent Skills），**40+ 工具**都读：Cursor、GitHub Copilot、
Gemini CLI、Windsurf、Codex、OpenCode、Goose、Amp、Cline、Roo Code、OpenHands、
Kiro、Junie 等。即使目标工具没有市场机制，把技能目录整个拷进去也能用。

Codex CLI 自带 `skill-installer`，可以从本仓库**直接装单个技能**（路径 basename 即为技能名，
本仓库 `plugins/<插件>/` 目录下就是 `SKILL.md`，符合它的要求）：

```bash
$CODEX_HOME/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo XuePengJu/agentest-skillhub \
  --path plugins/patchright-browser
```

其他工具的通用放法（优先 `.agents/skills/`，这是跨工具约定路径）：

| 工具 | 项目级 | 用户级 |
| --- | --- | --- |
| 通用约定 | `.agents/skills/` | `~/.agents/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex CLI | `.codex/skills/` | `~/.codex/skills/` |
| Cursor | `.cursor/skills/` | —（仅项目级） |
| GitHub Copilot | `.github/skills/` | `~/.copilot/skills/` |
| Gemini CLI | `.gemini/skills/` | `~/.gemini/skills/` |
| Windsurf | `.windsurf/skills/` | `~/.codeium/windsurf/skills/` |
| OpenCode | `.opencode/skills/` | `~/.config/opencode/skills/` |
| Goose | `.goose/skills/` | `~/.config/goose/skills/` |

### 跨工具时要注意的

- 插件特有的 `.mcp.json`、hooks、agents、`allowed-tools` 等是**各家私有**的。跨工具后这些会
  被静默忽略，插件退化成"纯技能"，仍然可用，但权限约束等不会被强制。
- 想让技能尽量通用，`SKILL.md` 的 frontmatter 只保留 `name` + `description`，正文用纯 Markdown。
- Cursor 装完技能需要重载窗口才会出现；Codex 需要重启。

## 仓库结构

```
agentest-skillhub/
├── .claude-plugin/
│   └── marketplace.json          # 市场清单：name / owner / plugins[]
├── plugins/
│   └── <插件名>/
│       ├── .claude-plugin/
│       │   └── plugin.json       # 插件清单：name / version / description
│       ├── SKILL.md              # 技能主体（frontmatter 含 name、description）
│       ├── references/           # 参考资料（可选）
│       └── scripts/              # 随技能分发的脚本（可选）
├── scripts/
│   └── import_skill.py           # 把本机技能导入本仓库
├── LICENSE
└── README.md
```

> 一个插件要装多个技能时，改用 `plugins/<插件名>/skills/<技能名>/SKILL.md` 逐个展开。

## 收录的技能

| 技能 | 版本 | 说明 |
| --- | --- | --- |
| [`patchright-browser`](./plugins/patchright-browser) | 1.0.0 | 反检测浏览器（Patchright 驱动）：常驻 HTTP 控制服务 + curl 一行命令，过 Cloudflare/DataDome 风控墙，token 消耗远低于 MCP |

## 添加一个新技能

`scripts/import_skill.py` 会读取 `~/.workbuddy/skills/<名字>`，复制技能目录、生成
`plugin.json`、并把它登记进 `marketplace.json`：

```bash
# 看有哪些可以导入（第三方技能会被标注出来）
python3 scripts/import_skill.py --list

# 导入指定技能
python3 scripts/import_skill.py BT-Node-Project-Deploy kb-archive-audit

# 全量导入（自动跳过第三方技能）
python3 scripts/import_skill.py --all

# 指定版本号
python3 scripts/import_skill.py library-port --version 1.0.0
```

导入后提交发布：

```bash
git add -A && git commit -m "feat: import weather-open-meteo" && git push
```

### ⚠️ 只发布你自己的作品

本机 `~/.workbuddy/skills/` 里的技能**不都是你的作品**：从技能商店装进来的第三方技能，
目录里会带一个 `_skillhub_meta.json`。把它收录进本市场并署上自己的名字、代原作者声明
许可，等于**转载他人作品 + 改署名**，不能这么发。

导入脚本会自动识别并**默认拒绝**：

```
✗ hot-news-summary: 这是从技能商店安装的第三方技能（每日话题助手 v1.0.2），不是你自己的作品。
    发布它会构成转载 + 改署名；确认要收录请加 --allow-third-party
```

`--list` 也会把第三方技能单独标出来，`--all` 会跳过它们。确需收录时加 `--allow-third-party`，
脚本会自动打上 `.skillhub-manual` 标记（防止下次导入冲掉你手改的署名），并提示你
把 `author` / `license` / 版本号改回**如实**的值。

### 导入时会自动剔除什么

避免把本机运行态或个人数据发到公开仓库，导入时**不会**复制以下内容：

| 排除项 | 原因 |
| --- | --- |
| `profile/` | 浏览器用户数据目录，**含 cookie 与登录态** |
| `*.app/` | macOS 应用包：体积大、内含机器相关绝对路径与代码签名 |
| `nohup.out`、`*.log` | 运行日志 |
| `*.bak`、`*.pyc`、`__pycache__/` | 备份与编译产物 |
| `.DS_Store`、`_skillhub_meta.json`、`_icon.svg`、`workbuddy.json`、`node_modules/` | 本机元数据与依赖 |

导入后建议再自查一遍硬编码的个人路径与凭据：

```bash
# 注意：用 -E（macOS 自带的是 BSD grep，其 BRE 不支持 \| 或模式）
grep -rnE '/Users/|/home/|api[_-]?key|secret|passwd' plugins/<插件名>/
```

### ⚠️ 人工改编过的插件不会被覆盖

自动剔除只能处理"垃圾文件和明显的数据目录"，**处理不了埋在正文里的个人路径**
（比如某个技能的 `SKILL.md` 里写着自己的绝对路径、或启动脚本里写死了本机 home）。
这类技能必须手工改编后再发布。

为避免下次 `import_skill.py` 把手工成果**静默覆盖**回去，改编完成后放一个标记文件：

```bash
touch plugins/<插件名>/.skillhub-manual
```

之后导入该插件会被跳过：

```
! <插件名>: 已存在人工改编版本（含 .skillhub-manual），跳过以免覆盖。确需用本机原版覆盖请加 --force
```

确需覆盖时加 `--force`，但**覆盖后必须重做全部去个人化处理**。
（实例见 `plugins/patchright-browser/.skillhub-manual`。）

## License

MIT
