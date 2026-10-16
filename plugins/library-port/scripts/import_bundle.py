#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""import_bundle.py —— 把 export_bundle 产出的 zip 导入到当前账号的资料库。

用法：
    python3 scripts/import_bundle.py <bundle.zip> [--space-id <id>] [--parent-id <id>]
    python3 scripts/import_bundle.py <bundle.zip> --name-prefix "[迁移]"
    python3 scripts/import_bundle.py <bundle.zip> --dry-run

流程：按 schema 保真建表 → 灌数据 → 重映射 HTML 内的表 ID → 上传页面并建立绑定。
成功输出一行 LP_IMPORT_OK <JSON>。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lp_util import (  # noqa: E402
    MANIFEST_NAME,
    LibraryNotFound,
    die,
    locate_library,
    read_json,
    run_script,
    wrap_record,
)

PLATFORM_HOST_HINTS = ("workbuddy", "codebuddy", "myqcloud", "tencentcos", "cos.", "qcloud")
IMAGE_SRC_RE = re.compile(
    r'(<img[^>]{0,400}?src=["\'])(https?://[^"\']+)(["\'])', re.IGNORECASE
)
CSS_URL_RE = re.compile(r'(url\()(["\']?)(https?://[^"\')]+)(\2\))', re.IGNORECASE)


def _is_platform_url(url: str) -> bool:
    lower = url.lower()
    return any(hint in lower for hint in PLATFORM_HOST_HINTS)


def _to_platform_url(lib: Path, url: str, warnings: list[str]) -> str:
    code, out = run_script(lib, "manage/upload_image.py", ["--url", url])
    if code != 0 or "KS_IMAGE_UPLOAD_OK" not in out:
        warnings.append(f"第三方图片转存失败，导入可能因图片外链被拒: {url}")
        return url
    line = [l for l in out.splitlines() if "KS_IMAGE_UPLOAD_OK" in l][0]
    try:
        payload = json.loads(line.split("KS_IMAGE_UPLOAD_OK", 1)[1].strip())
        return payload.get("url") or url
    except json.JSONDecodeError:
        return url


def fix_external_images(lib: Path, root: Path, warnings: list[str]) -> int:
    """把页面里指向第三方的图片地址换成平台链接，满足 imported page 的图片托管硬门槛。"""
    changed = 0
    for target in list(root.rglob("*.html")) + list(root.rglob("*.htm")) + \
            list(root.rglob("*.css")) + list(root.rglob("*.js")):
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text

        def repl_img(match: re.Match) -> str:
            url = match.group(2)
            if _is_platform_url(url):
                return match.group(0)
            return match.group(1) + _to_platform_url(lib, url, warnings) + match.group(3)

        def repl_css(match: re.Match) -> str:
            url = match.group(3)
            if _is_platform_url(url):
                return match.group(0)
            return match.group(1) + match.group(2) + \
                _to_platform_url(lib, url, warnings) + match.group(4)

        text = IMAGE_SRC_RE.sub(repl_img, text)
        text = CSS_URL_RE.sub(repl_css, text)
        if text != original:
            target.write_text(text, encoding="utf-8")
            changed += 1
    return changed


_CONFIG_DEFAULTS = {
    "text": "",
    "number": {},
    "currency": {},
    "select": {},
    "multi_select": {},
    "date": {},
    "checkbox": False,
    "url": {},
    "email": "",
    "phone_number": "",
    "image": {},
    "attachment": {},
    "person": {},
}


def build_property_payload(raw_properties: list[dict]) -> list[dict]:
    """把导出格式的字段数组转成 create_database 要求的 {name, config:{type: config}}。"""
    result: list[dict] = []
    for item in raw_properties or []:
        name = (item.get("name") or "").strip()
        field_type = item.get("type")
        if not name or not field_type:
            continue
        config = item.get("config")
        if not isinstance(config, dict) or not config:
            config = _CONFIG_DEFAULTS.get(field_type, {})
        result.append({"name": name, "config": {field_type: config}})
    return result


def existing_titles(lib: Path, space_id: str) -> dict[str, dict]:
    """列出目标空间顶层已有的 {标题: {id, kind}}，用于避免重复导入堆积。"""
    args = ["space.workspace.list-nodes", "--limit", "100"]
    if space_id:
        args += ["--space-id", space_id]
    code, out = run_script(lib, "space_api.py", args)
    if code != 0 or not out or '"data"' not in out:
        return {}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return {
        (n.get("title") or ""): {"id": n.get("id"), "kind": n.get("kind")}
        for n in (payload.get("data") or {}).get("nodes") or []
    }


def node_brief(lib: Path, node_id: str) -> dict:
    code, out = run_script(lib, "space_api.py", [
        "space.workspace.node-info", "--node-id", node_id,
    ])
    if code != 0 or not out or '"data"' not in out:
        return {}
    try:
        return json.loads(out)["data"]["node"]
    except (KeyError, json.JSONDecodeError):
        return {}


def create_table(lib: Path, db_id: str, table_dir: Path, display_title: str,
                 space_id: str, warnings: list[str]) -> str:
    schema = read_json(table_dir / "schema.json")
    raw_properties = schema.get("properties") or []
    properties = build_property_payload(raw_properties)
    payload: dict = {"title": display_title, "properties": properties}
    if space_id:
        payload["space_id"] = space_id

    # 走 stdin 通道喂 JSON，避免超长命令行转义问题
    env = dict(os.environ)
    env.setdefault("CODEBUDDY_SKILL_DIR", str(lib))
    proc = subprocess.run(
        [sys.executable, str(lib / "database" / "create_database.py"), "--stdin"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, timeout=60, env=env,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        die(f"建表失败 {display_title}: {out[:300]}")
    # create_database 输出两行：首行 JSON，次行 KS_USER_REPLY
    try:
        result = json.loads(out.splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        die(f"建表返回无法解析 {display_title}: {out[:300]}")
    if "error" in result:
        die(f"建表失败 {display_title}: {result['error']}")
    new_id = result.get("database_id")
    if not new_id:
        die(f"建表未返回 ID: {display_title}")

    records = read_json(table_dir / "records.json")
    records = records if isinstance(records, list) else []
    # 灌数据时必须传原始字段数组（含 type），转换后的 payload 已丢掉 type
    fill_records(lib, new_id, raw_properties, records, warnings)

    return new_id


def table_is_empty(lib: Path, db_id: str) -> bool:
    code, out = run_script(lib, "database/query_database_record.py", [
        "--database-id", db_id, "--page-size", "1",
    ])
    if code != 0 or not out:
        return False
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return False
    return not (payload.get("results") or [])


def fill_records(lib: Path, db_id: str, properties: list[dict],
                 records: list[dict], warnings: list[str]) -> None:
    env = dict(os.environ)
    env.setdefault("CODEBUDDY_SKILL_DIR", str(lib))
    written = 0
    for start in range(0, len(records), 100):
        batch = [wrap_record(r, properties) for r in records[start:start + 100]]
        batch = [b for b in batch if b]
        if not batch:
            continue
        proc = subprocess.run(
            [sys.executable, str(lib / "database" / "batch_add_database_records.py"),
             "--stdin"],
            input=json.dumps({"database_id": db_id, "records": batch},
                             ensure_ascii=False),
            capture_output=True, text=True, timeout=120, env=env,
        )
        body = (proc.stdout or "").strip()
        if proc.returncode != 0 or not body or '"error"' in body:
            warnings.append(
                f"表 {db_id} 第 {start // 100 + 1} 批数据写入失败: {body[:200]}")
        else:
            written += len(batch)
    if records and written == 0:
        warnings.append(
            f"表 {db_id} 的记录全部未能写入：记录字段与表结构不匹配，请检查源表是否含特殊字段类型")


def remap_tree(lib: Path, page_dir: Path, mapping: dict) -> int:
    """对页面目录内所有引用了旧表 ID 的文件做 ID 重映射。"""
    touched = 0
    for target in page_dir.rglob("*"):
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not any(old in text for old in mapping):
            continue
        code, out = run_script(lib, "page/remap_database_ids.py", [
            "--html", str(target), "--mapping", json.dumps(mapping, ensure_ascii=False),
        ])
        if code != 0 or "KS_REMAP_OK" not in out:
            die(f"表 ID 重映射失败: {target.name} → {out[:200]}")
        touched += 1
    return touched


def pack_upload_bundle(page_dir: Path, work: Path) -> tuple[Path, str]:
    files = [p for p in page_dir.rglob("*") if p.is_file()]
    if len(files) == 1 and files[0].suffix.lower() in (".html", ".htm"):
        return files[0], "html"
    zip_path = work / f"{page_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            zf.write(path, path.relative_to(page_dir).as_posix())
    return zip_path, "zip"


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("bundle", nargs="?")
    parser.add_argument("--space-id", default="")
    parser.add_argument("--parent-id", default="")
    parser.add_argument("--name-prefix", default="")
    parser.add_argument("--dry-run", action="store_true")
    try:
        args, _ = parser.parse_known_args()
    except SystemExit:
        die("参数解析失败")

    if not args.bundle:
        die("必须提供 bundle.zip 路径")
    bundle_path = Path(args.bundle).expanduser().resolve()
    if not bundle_path.exists():
        die(f"找不到文件: {bundle_path}")

    if args.dry_run:
        with zipfile.ZipFile(bundle_path) as zf:
            if MANIFEST_NAME not in zf.namelist():
                die("包内缺少 manifest.json，不是有效的迁移包")
            manifest = json.loads(zf.read(MANIFEST_NAME))
        print("LP_DRYRUN_OK " + json.dumps({
            "nodes": len(manifest.get("nodes") or []),
            "relations": len(manifest.get("relations") or []),
            "titles": [n.get("title") for n in manifest.get("nodes") or []],
        }, ensure_ascii=False))
        return

    try:
        lib = locate_library()
    except LibraryNotFound as exc:
        die(str(exc))

    work = bundle_path.parent / f"_lp_import_{bundle_path.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with zipfile.ZipFile(bundle_path) as zf:
        zf.extractall(work)

    manifest_path = work / MANIFEST_NAME
    if not manifest_path.exists():
        die("包内缺少 manifest.json，不是有效的迁移包")
    manifest = read_json(manifest_path)

    nodes = manifest.get("nodes") or []
    relations = manifest.get("relations") or []
    tables = [n for n in nodes if n.get("kind") == "database"]
    pages = [n for n in nodes if n.get("kind") == "web"]

    warnings: list[str] = []
    id_map: dict[str, str] = {}
    present = existing_titles(lib, args.space_id)

    # 已存在的同名页面下挂载的表也算「已存在」，避免重复导同一个包时堆积副本
    for page in pages:
        page_title = f"{args.name_prefix}{page.get('title') or '页面'}"
        same = present.get(page_title)
        if not (same and same.get("kind") == "web"):
            continue
        for child_id in node_brief(lib, same["id"]).get("nodes") or []:
            child = node_brief(lib, child_id)
            if child.get("kind") == "database":
                present[child.get("title") or ""] = {
                    "id": child_id, "kind": "database"}

    for table in tables:
        table_dir = work / "tables" / table["id"]
        if not (table_dir / "schema.json").exists():
            warnings.append(f"包内缺少表数据，已跳过: {table.get('title')}")
            continue
        title = f"{args.name_prefix}{table.get('title') or '数据表'}"
        hit = present.get(title)
        if hit and hit.get("kind") == "database":
            warnings.append(f"目标位置已存在同名表，已复用以免重复堆积: {title}")
            reused = hit["id"]
            id_map[table["id"]] = reused
            if table_is_empty(lib, reused):
                raw_props = read_json(table_dir / "schema.json").get("properties") or []
                fill_records(lib, reused, raw_props,
                             read_json(table_dir / "records.json"), warnings)
            continue
        new_id = create_table(lib, table["id"], table_dir, title,
                              args.space_id, warnings)
        id_map[table["id"]] = new_id
        present[title] = {"id": new_id, "kind": "database"}

    page_results: list[dict] = []
    for page in pages:
        page_dir = work / "pages" / page["id"]
        if not page_dir.exists():
            warnings.append(f"包内缺少页面产物，已跳过: {page.get('title')}")
            continue

        page_relations = [r for r in relations if r.get("page_id") == page["id"]]
        my_map = {r["database_id"]: id_map[r["database_id"]]
                  for r in page_relations if r.get("database_id") in id_map}
        if my_map:
            remap_tree(lib, page_dir, my_map)

        fix_external_images(lib, page_dir, warnings)

        entry, kind = pack_upload_bundle(page_dir, work)
        raw_title = page.get("title") or "页面"
        display_name = f"{args.name_prefix}{raw_title}.{kind}"
        upload_args = [str(entry), "--file-name", display_name]
        same = present.get(f"{args.name_prefix}{raw_title}")
        if same and same.get("kind") == "web":
            # 同名页面已存在：走重新导入覆盖，避免堆积副本
            upload_args += ["--node-block-id", same["id"]]
        elif same:
            warnings.append(f"存在同名非页面节点，本次新建页面: {raw_title}")
        if my_map:
            upload_args += ["--databases",
                            json.dumps([{"id": v} for v in my_map.values()],
                                       ensure_ascii=False)]
        if args.space_id:
            upload_args += ["--space-id", args.space_id]
        if args.parent_id:
            upload_args += ["--parent-id", args.parent_id]

        code, out = run_script(lib, "page/import_html.py", upload_args, timeout=300)
        if code != 0 or "KS_IMPORT_OK" not in out:
            page_results.append({
                "title": raw_title, "ok": False, "reason": out[:300],
            })
            continue
        line = [l for l in out.splitlines() if "KS_IMPORT_OK" in l][0]
        payload = json.loads(line.split("KS_IMPORT_OK", 1)[1].strip())
        page_results.append({
            "title": raw_title,
            "ok": True,
            "url": payload.get("url") or "",
            "bound_tables": len(my_map),
        })

    shutil.rmtree(work, ignore_errors=True)

    print("LP_IMPORT_OK " + json.dumps({
        "pages": page_results,
        "tables": [{"title": t.get("title"), "new_id": id_map.get(t["id"])}
                   for t in tables if t["id"] in id_map],
        "warnings": warnings,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
