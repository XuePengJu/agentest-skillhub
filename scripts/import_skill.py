#!/usr/bin/env python3
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""把本机 WorkBuddy 技能导入 agentest-skillhub，成为一个可发布的插件。

用法：
    python3 scripts/import_skill.py <技能名> [<技能名> ...]
    python3 scripts/import_skill.py --all
    python3 scripts/import_skill.py <技能名> --version 2.1.0
    python3 scripts/import_skill.py --list

默认从 ~/.workbuddy/skills/ 读取，可用 --source 指定其他目录。
脚本会：复制技能目录（剔除垃圾文件）→ 生成 .claude-plugin/plugin.json
→ 更新 .claude-plugin/marketplace.json 中的条目。重复导入即覆盖更新。

技能商店安装的第三方技能（目录里有 _skillhub_meta.json）默认拒绝导入 ——
它们不是你自己的作品，收录并署自己名字等于转载 + 改署名。确需收录加
--allow-third-party（会用 .skillhub-manual 保护后续手工改的署名不被冲掉）。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"
DEFAULT_SOURCE = Path.home() / ".workbuddy" / "skills"
REPO_URL = "https://github.com/XuePengJu/agentest-skillhub"
HOMEPAGE = "https://agentest.vip"
AUTHOR = {"name": "xuepengju", "url": HOMEPAGE}

# 复制时排除的文件/目录：系统垃圾、安装元数据、运行时产物
EXCLUDE_NAMES = {
    ".DS_Store",
    "_skillhub_meta.json",
    "nohup.out",
    "workbuddy.json",
    "profile",
    "node_modules",
    "__pycache__",
}
EXCLUDE_SUFFIXES = {".log", ".pyc", ".bak"}
# 商店图标缓存：_icon.svg / _icon.png / _icon.jpg …
# 【不是技能自己的资产，是客户端从商店 CDN 下载的缓存】—— 见 README「关于图标」。
# 所以按前缀排除，而不是只排 _icon.svg（那会漏掉 .png/.jpg）。
EXCLUDE_PREFIXES = ("_icon.",)

SLUG_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9._]*$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 人工改编标记：仓库里某个插件被手工去个人化/改写过后放这个文件，
# 再次导入时默认拒绝覆盖（否则本机技能里的绝对路径、个人数据会被原样搬回来）
MANUAL_MARKER = ".skillhub-manual"

# 第三方技能标记：技能商店/市场安装的技能目录里会带这个文件。
# 带它的技能不是你自己的作品，收录进本市场并署自己名字 = 转载 + 改署名。
THIRD_PARTY_MARKER = "_skillhub_meta.json"


def third_party_info(src: Path) -> dict | None:
    """技能是商店装的第三方作品就返回它的安装元数据，否则返回 None。"""
    meta_file = src / THIRD_PARTY_MARKER
    if not meta_file.is_file():
        return None
    try:
        info = json.loads(meta_file.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {"source": "skillhub"}
    except Exception:
        return {"source": "skillhub"}


def parse_frontmatter(text: str) -> dict:
    """极简 YAML frontmatter 解析：只取顶层的 key: value 标量。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t#-":
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            out[key.strip()] = value
    return out


def is_excluded(path: Path) -> bool:
    # macOS 应用包（*.app）：体积大、内含机器相关的绝对路径与代码签名，不适合发布
    if any(part.endswith(".app") for part in path.parts):
        return True
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if path.name.startswith(EXCLUDE_PREFIXES):
        return True
    return any(part in EXCLUDE_NAMES for part in path.parts)


def copy_skill(src: Path, dst: Path) -> int:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    count = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if is_excluded(rel):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def build_plugin_json(slug: str, meta: dict, version: str) -> dict:
    description = meta.get("description", "").strip()
    if not description:
        description = f"{slug} —— 来自 agentest-skillhub 的技能"
    return {
        "name": slug,
        "version": version,
        "description": description,
        "author": AUTHOR,
        "homepage": HOMEPAGE,
        "repository": REPO_URL,
        "license": meta.get("license", "Apache-2.0"),
    }


def load_marketplace() -> dict:
    if MARKETPLACE_FILE.exists():
        return json.loads(MARKETPLACE_FILE.read_text(encoding="utf-8"))
    return {
        "name": "agentest-skillhub",
        "description": "agentest 技能市场 —— AI 应用开发与 Agent 工具的实战技能集合",
        "owner": {"name": "xuepengju", "url": HOMEPAGE},
        "plugins": [],
    }


def upsert_entry(market: dict, entry: dict) -> str:
    plugins = market.setdefault("plugins", [])
    for i, existing in enumerate(plugins):
        if existing.get("name") == entry["name"]:
            # 保留人工补充的字段（category / keywords / description_en 等），避免重复导入时被冲掉
            for key, value in existing.items():
                entry.setdefault(key, value)
            plugins[i] = entry
            return "updated"
    plugins.append(entry)
    plugins.sort(key=lambda p: p.get("name", ""))
    return "added"


def import_one(
    slug: str,
    source_root: Path,
    version: str | None,
    force: bool = False,
    allow_third_party: bool = False,
) -> bool:
    if not SLUG_RE.match(slug):
        print(f"  ✗ {slug}: 名字不合法（须匹配 {SLUG_RE.pattern}）")
        return False
    src = source_root / slug
    if not (src / "SKILL.md").is_file():
        print(f"  ✗ {slug}: 未找到 {src / 'SKILL.md'}")
        return False

    info = third_party_info(src)
    if info and not allow_third_party:
        shown = info.get("name") or slug
        ver = info.get("version") or "?"
        print(f"  ✗ {slug}: 这是从技能商店安装的第三方技能（{shown} v{ver}），不是你自己的作品。")
        print(f"      发布它会构成转载 + 改署名；确认要收录请加 --allow-third-party")
        return False

    dst = PLUGINS_DIR / slug
    if (dst / MANUAL_MARKER).exists() and not force:
        print(f"  ! {slug}: 已存在人工改编版本（含 {MANUAL_MARKER}），跳过以免覆盖。"
              f"确需用本机原版覆盖请加 --force")
        return False

    meta = parse_frontmatter((src / "SKILL.md").read_text(encoding="utf-8"))
    resolved_version = version or meta.get("version") or "0.1.0"
    if version and meta.get("version") and version != meta["version"]:
        print(f"      ⚠ SKILL.md 写的是 v{meta['version']}，本次被 --version 覆盖为 v{version}，"
              f"两处已不一致，建议同步 SKILL.md 的 version")
    elif not meta.get("version"):
        print(f"      · SKILL.md frontmatter 没有 version（商店生态 82% 都写），"
              f"本次按 v{resolved_version} 登记，建议在 SKILL.md 里补一行 version 保持三处一致")

    copied = copy_skill(src, dst)
    plugin_meta = build_plugin_json(slug, meta, resolved_version)
    plugin_json = PLUGINS_DIR / slug / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir(parents=True, exist_ok=True)
    plugin_json.write_text(
        json.dumps(plugin_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    market = load_marketplace()
    entry = {
        "name": slug,
        "description": meta.get("description", plugin_meta["description"]),
        "version": resolved_version,
        "source": f"./plugins/{slug}",
        "author": AUTHOR,
        "homepage": HOMEPAGE,
        "repository": REPO_URL,
        "license": plugin_meta["license"],
    }
    action = upsert_entry(market, entry)
    MARKETPLACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARKETPLACE_FILE.write_text(
        json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  ✓ {slug} v{resolved_version} —— 复制 {copied} 个文件，市场条目已{action}")

    if info:
        # 已放行：打人工改编标记（防止下次导入冲掉手改的署名），并提醒如实标注
        (dst / MANUAL_MARKER).touch()
        print(f"      ⚠ 第三方技能已放行并打上 {MANUAL_MARKER} 标记。收工前请手工改：")
        print(f"        - marketplace.json 与 plugin.json 的 author 改为「来源：{info.get('source', 'skillhub')}」")
        print(f"        - 删掉不是原作者声明的 license，版本号改回商店的 {info.get('version') or '实际版本'}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="导入 WorkBuddy 技能到 agentest-skillhub")
    ap.add_argument("skills", nargs="*", help="技能目录名（可多个）")
    ap.add_argument("--all", action="store_true", help="导入来源目录下的全部技能")
    ap.add_argument("--list", action="store_true", help="列出来源目录下可导入的技能")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help="技能来源目录")
    ap.add_argument("--version", default=None, help="强制指定版本号")
    ap.add_argument("--force", action="store_true",
                    help="即使插件含人工改编标记（.skillhub-manual）也用本机原版覆盖")
    ap.add_argument("--allow-third-party", action="store_true", dest="allow_third_party",
                    help="放行技能商店安装的第三方技能（默认拒绝；请先把作者/许可改回如实）")
    args = ap.parse_args()

    source_root = Path(args.source).expanduser()
    if not source_root.is_dir():
        print(f"来源目录不存在：{source_root}", file=sys.stderr)
        return 1

    available = sorted(
        p.name for p in source_root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )

    if args.list:
        own = [n for n in available if not third_party_info(source_root / n)]
        third = [n for n in available if third_party_info(source_root / n)]
        print(f"来源 {source_root} 下共 {len(available)} 个技能：")
        print(f"\n自建（可发布，{len(own)} 个）：")
        for name in own:
            print(f"  - {name}")
        print(f"\n第三方·技能商店安装（默认不导入，{len(third)} 个）：")
        for name in third:
            info = third_party_info(source_root / name) or {}
            shown = info.get("name") or ""
            ver = info.get("version") or "?"
            suffix = f"（{shown} v{ver}）" if shown else f"（v{ver}）"
            print(f"  - {name} {suffix}")
        return 0

    targets = available if args.all else args.skills
    if not targets:
        ap.print_help()
        return 1

    print(f"来源：{source_root}")
    ok = 0
    skipped = 0
    for slug in targets:
        if import_one(slug, source_root, args.version, force=args.force,
                      allow_third_party=args.allow_third_party):
            ok += 1
        else:
            skipped += 1
    print(f"\n完成：{ok}/{len(targets)} 个技能已导入。")
    if skipped:
        print(f"（{skipped} 个被跳过或失败，原因见上）")
    if ok:
        print("下一步：git add -A && git commit -m 'feat: import skills' && git push")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
