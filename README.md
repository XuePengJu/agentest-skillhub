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
codebuddy plugin install hot-news-summary@agentest-skillhub
```

### Claude Code

```bash
claude plugin marketplace add XuePengJu/agentest-skillhub
claude plugin install hot-news-summary@agentest-skillhub
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
| [`hot-news-summary`](./plugins/hot-news-summary) | 2.0.0 | 聚合 8 大平台热榜，结构化评分筛 Top10，生成三层洞察点评 |

## 添加一个新技能

`scripts/import_skill.py` 会读取 `~/.workbuddy/skills/<名字>`，复制技能目录、生成
`plugin.json`、并把它登记进 `marketplace.json`：

```bash
# 看有哪些可以导入
python3 scripts/import_skill.py --list

# 导入指定技能
python3 scripts/import_skill.py weather-open-meteo BT-Node-Project-Deploy

# 全量导入
python3 scripts/import_skill.py --all

# 指定版本号
python3 scripts/import_skill.py nuwa-skill --version 1.2.0
```

导入后提交发布：

```bash
git add -A && git commit -m "feat: import weather-open-meteo" && git push
```

## License

MIT
