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

推送到 `master` 后，使用者执行：

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
│       ├── ai.workbuddy/
│       │   └── assets/
│       │       └── icon.svg      # 插件图标（可选，见「关于图标」）
│       ├── assets/
│       │   └── icon.svg          # 同上，兼容另一种图标解析路径
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
| [`library-port`](./plugins/library-port) | 1.0.0 | 资料库搬家：把「网页页面 + 绑定的数据表」整套导出成 zip，在另一个账号/电脑上原样还原并重建绑定 |
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

> 这是本市场的**收录条款**（见上「收录原则」），导入脚本只是它的技术兜底。

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
| `.DS_Store`、`_skillhub_meta.json`、`workbuddy.json`、`node_modules/` | 本机元数据与依赖 |
| `_icon.*`（任意扩展名，即 `_icon.svg` / `.png` / `.jpg` …） | **商店图标缓存**，不是技能自己的资产，见「关于图标」 |

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

> 说明：`.skillhub-manual` 是维护者**本机**的工作标记，不随本仓库分发；仓库根
> 已用 `.gitignore` 忽略它，因此你在 clone 下来的仓库里看不到该文件。

## 收录原则

**本市场只收录维护者本人原创的技能。** 这不是建议，是本市场的收录条款：

- 收录进 `plugins/` 的每个插件，其代码与文档都由维护者创作，版权归维护者所有。
- **不接受**把他人作品（含从技能商店安装的第三方技能）重新署名后收录。这样做构成
  **转载 + 改署名**，与开源协议无关，是独立的侵权行为。
- 技术上有 `scripts/import_skill.py` 的 `_skillhub_meta.json` 检测作拦截，但这只是
  工具兜底；**真正的约束是这条原则本身**。
- 若确需收录第三方作品，必须保留其原始署名与许可，并在该插件的 `SKILL.md` 中如实标注来源。

## 使用须知与免责声明

**本仓库按「现状」提供，请在使用前阅读本节。**

1. **软件质量免责**：本仓库及其全部插件按 **AS-IS** 提供，不附带任何明示或暗示的担保，
   包括但不限于对适销性、特定用途适用性、无侵权的担保。因使用本软件产生的任何损失，
   作者与贡献者不承担责任（详见 `LICENSE` 第 7、8 节）。

2. **用途合规责任归使用者**：本软件仅提供技术能力，**不对使用者的具体用途作任何担保或背书**。
   使用者须自行确保其使用行为符合所在地法律法规及目标网站/服务的使用条款。
   例如，`patchright-browser` 是浏览器自动化工具，使用者不得将其用于违反法律、
   侵犯他人权益或规避合法访问控制的场景。**由此产生的一切法律责任由使用者自行承担。**

3. **数据版权归原权利人**：使用者通过本软件获取、抓取或处理的任何数据，
   其版权与相关权利归原权利人所有，与本仓库的软件许可无关。

4. **不含第三方技能**：本仓库不收录第三方技能包。运行期依赖（如 `patchright`、
   `Playwright`）由使用者自行安装，各自遵循其原始许可，详见 `NOTICE`。

5. **无担保与无支持承诺**：维护者不承诺提供技术支持、更新或问题修复。

## 关于图标

技能目录里的 `_icon.svg` / `_icon.png` **不是技能作者放进去的文件** —— 别被它骗了。

它是 WorkBuddy 客户端从技能商店 **CDN 下载下来的缓存**：作者上架到商店时在后台传封面图，
客户端安装技能时把图存进技能目录，并在 `_skillhub_meta.json` 里记下三个字段：

```json
{
  "iconSource": "https://openplatform-cdn.codebuddy.cn/public/.../xxx.svg",
  "iconLocalPath": "<技能目录>/_icon.svg",
  "iconDownloadedFrom": "<同一个 CDN 地址>"
}
```

`iconSource` 是 http 地址时客户端才会去下载；**手动往技能目录丢一个 `_icon.png` 不会被读取**。
所以本仓库把它列为排除项：以 `_icon.` 开头的文件一律不导入。

本仓库的插件图标走**另一条路** —— 插件包里的资源文件，WorkBuddy 按约定路径解析，
图标扩展名只认 `.svg` 和 `.png`（官方连接器插件 feishu / notion / qq-mail 都是这么放的）：

```
<插件>/ai.workbuddy/assets/icon.svg
```

为兼容另一种解析路径（以插件根为基准目录），同一份图在 `<插件>/assets/icon.svg` 也放一份。
两处内容需保持一致，改图时记得一起改。

> 说明：通用插件市场（`codebuddy-plugins-official` 等）里的插件目前**都没带图标**，
> 上面这个约定是从 WorkBuddy 源码与官方连接器插件里读出来的，没有先例可对照 ——
> 实际是否显示、显示成什么样，以本机插件面板为准。

## License

本项目采用 **Apache License 2.0**，全文见 [`LICENSE`](./LICENSE)，第三方组件声明见 [`NOTICE`](./NOTICE)。

```
Copyright (c) 2026 xuepengju — https://agentest.vip

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

**为什么是 Apache-2.0 而不是 MIT？** 两者在自由度上完全等价（都可商用、可闭源、可修改、
可再分发），Apache-2.0 额外提供**明确的专利授权**与**专利报复条款**，对企业使用者更友好。
代价是分发时需携带 `NOTICE` 文件，且修改过的文件需声明改动（`LICENSE` 第 4 节）。

