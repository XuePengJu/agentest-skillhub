---
name: library-port
description: 把 WorkBuddy 资料库里的网页页面和它绑定的数据表整套导出成一个 zip 迁移包，或在另一个 WorkBuddy 账号 / 另一台电脑上把这个 zip 原样恢复出来，并重新建立页面与数据表的绑定。当用户提到资料库搬家、导出资料库、备份资料库页面、迁移看板/工作台/管理台给别人、打包资料库给朋友、换个账号用同一个页面、复制这个页面到别的资料库、复制资料库里的节点、导入迁移包、把 zip 还原到资料库时应使用本技能。
version: 1.0.0
display_name: "资料库迁移"
display_name_en: "Library Migration"
agent_created: true
---

# library-port · 资料库页面迁移

把资料库里的「页面 + 绑定的数据表」整套搬家的能力，分导出成 zip 与从 zip 恢复两步。

## 能力边界

支持：kind 为 `web`（网页页面）与 `database`（数据表）的节点，以及两者之间的绑定关系。

不支持：在线文档（`doc`）、本地上传附件（`drive`）、网页剪藏（`link`）。导出时会跳过并在结果里列出。

前提：目标账号必须是**已登录的 WorkBuddy 账号**。导入依赖资料库服务端 API，无法向没有 WorkBuddy 会话的环境写入。

## Before starting

两个脚本会自行定位 WorkBuddy 内置的 library 技能目录（环境变量 → 固定安装路径 → 常见根目录搜索），无需手工配置路径。若定位失败会返回 `{"error":"未找到..."}`，此时需确认本机已安装并登录 WorkBuddy。

## Step 1 · 导出

从链接里提取 nodeId：`/space/d/{id}` 或 `*.workbuddy.link/p/{id}` 中的 `{id}` 部分。

```bash
# 导出单个页面（默认连同它挂载的所有子表一起导出）
python3 scripts/export_bundle.py --node-id "<nodeId>" --out "<目标路径>/<名称>.zip"

# 只导出节点本身，不递归子节点
python3 scripts/export_bundle.py --node-id "<nodeId>" --out "<路径>.zip" --no-children

# 导出整个空间
python3 scripts/export_bundle.py --space-id "<spaceId>" --out "<路径>.zip"
```

成功输出一行 `LP_EXPORT_OK <JSON>`，含导出体积、页面清单、每张表的记录数、绑定关系数、被跳过的节点。单个包最多收录 200 个节点。

## Step 2 · 分发

把 zip 文件本身交给接收方即可。内容与账号无关，不含任何 token 或凭据。

## Step 3 · 导入

接收方在本机 WorkBuddy 已登录的状态下执行：

```bash
# 先试运行，只列出包内内容，不写入
python3 scripts/import_bundle.py <bundle.zip> --dry-run

# 正式导入到默认空间
python3 scripts/import_bundle.py <bundle.zip>

# 导入到指定空间 / 目录
python3 scripts/import_bundle.py <bundle.zip> --space-id "<spaceId>" --parent-id "<nodeId>"

# 给导入结果加统一前缀，便于区分和事后清理
python3 scripts/import_bundle.py <bundle.zip> --name-prefix "[迁移]"
```

成功输出一行 `LP_IMPORT_OK <JSON>`，含每个页面的新链接、每张表的新 ID，以及 `warnings`。

## Step 4 · 收尾

向用户报告导入结果时，必须包含：新页面的访问链接、还原了哪几张表、以及 warnings 的内容。若 warnings 非空，逐条说明含义，不要只说"导入完成"。

## Bundle 结构

```
manifest.json                    清单：节点、标题、kind、层级、绑定关系
pages/<page_id>/<构件文件>        页面产物（HTML / CSS / JS / 图片）
tables/<db_id>/schema.json       表字段结构，用于保真重建
tables/<db_id>/records.json      表记录，保留原始值类型
```

## 为什么能保真

重建表不走 CSV 推断，而是把导出时的字段结构原样喂给建表接口，因此日期、数字、多选等字段类型与源表完全一致。记录值也保留原始类型而非字符串化。

## 重复导入的行为

- 同名数据表：复用已存在的表，不新建副本；若该表为空则补灌数据。
- 同名页面：走重新导入，覆盖同一节点并生成新版本，不产生第二个副本。
- 因此同一个 zip 反复导入不会持续堆积。但去重依赖标题匹配，若手动改过标题则会新建。

## 已知限制

- 页面内的第三方图片会被自动转存为平台链接以满足托管要求；若某个图片转存失败，会记入 warnings。
- 单一数据表上限 10,000 行 / 50 列，超出需先拆分。
- 导入需要目标空间的写入权限；只对空间有只读权限时会失败。

## 排错

| 现象                              | 处理                                            |
| ------------------------------- | --------------------------------------------- |
| `未找到 WorkBuddy 内置 library 技能目录` | 确认本机已安装并登录 WorkBuddy                          |
| `包内缺少 manifest.json`            | 不是本技能产出的包，或 zip 已损坏                           |
| `建表失败 ... 必须且只能包含一种合法类型`        | 源表含本版本不支持的字段类型，跳过该表即可                         |
| 页面导入返回 ok=false                 | 查看返回里的 reason 字段，通常是缺少入口 HTML 或权限不足           |
| 导入后页面看不到数据                      | 下载页面产物，搜索 `data-sp-database-id`，确认是否已指向新表的 ID |
