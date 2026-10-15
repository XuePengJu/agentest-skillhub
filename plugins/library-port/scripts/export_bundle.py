#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_bundle.py —— 把 WorkBuddy 资料库里的页面 + 数据表导出成一个可迁移的 zip。

用法：
    python3 scripts/export_bundle.py --node-id <id> [--node-id <id2>] --out <bundle.zip>
    python3 scripts/export_bundle.py --space-id <id> --out <bundle.zip>
    python3 scripts/export_bundle.py --node-id <id> --out <bundle.zip> --no-children

产出 zip 结构：
    manifest.json
    pages/<page_id>/<构件文件>
    tables/<db_id>/schema.json
    tables/<db_id>/records.json

成功输出一行 LP_EXPORT_OK <JSON>。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lp_util import (  # noqa: E402
    BUNDLE_VERSION,
    MANIFEST_NAME,
    LibraryNotFound,
    die,
    locate_library,
    read_json,
    run_script,
    write_json,
)

MAX_NODES = 200


def node_info(lib: Path, node_id: str) -> dict:
    code, out = run_script(lib, "space_api.py", [
        "space.workspace.node-info", "--node-id", node_id,
    ])
    if code != 0 or not out:
        die(f"读取节点失败: {node_id}")
    payload = json.loads(out)
    if "error" in payload:
        die(f"读取节点失败 {node_id}: {payload['error']}")
    return payload["data"]["node"]


def query_all_records(lib: Path, db_id: str) -> list[dict]:
    """分页拉全量记录，保持扁平值形式。"""
    records: list[dict] = []
    cursor = ""
    for _ in range(500):
        args = [
            "database/query_database_record.py",
            "--database-id", db_id,
            "--page-size", "100",
        ]
        if cursor:
            args += ["--start-cursor", cursor]
        code, out = run_script(lib, args[0], args[1:])
        if code != 0 or not out:
            break
        payload = json.loads(out)
        if "error" in payload:
            die(f"读取表记录失败 {db_id}: {payload['error']}")
        results = payload.get("results") or []
        records.extend(results)
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor") or ""
        if not cursor:
            break
    return records


def export_table(lib: Path, db_id: str, work: Path) -> dict:
    code, out = run_script(lib, "database/get_database_schema.py", [
        "--database-id", db_id,
    ])
    if code != 0 or not out:
        die(f"读取表结构失败: {db_id}")
    schema = json.loads(out)
    if "error" in schema:
        die(f"读取表结构失败 {db_id}: {schema['error']}")

    records = query_all_records(lib, db_id)
    table_dir = work / "tables" / db_id
    write_json(table_dir / "schema.json", schema)
    write_json(table_dir / "records.json", records)
    return {
        "title": schema.get("title") or "",
        "field_count": len(schema.get("properties") or []),
        "record_count": len(records),
    }


def export_page(lib: Path, page_id: str, work: Path) -> dict:
    dest = work / "pages" / page_id
    dest.mkdir(parents=True, exist_ok=True)
    code, out = run_script(lib, "page/download_page_artifacts.py", [
        "--node-id", page_id, "--out-dir", str(dest),
    ])
    if code != 0 or "KS_ARTIFACTS_OK" not in out:
        die(f"下载页面产物失败 {page_id}: {out[:300]}")
    line = [l for l in out.splitlines() if "KS_ARTIFACTS_OK" in l][0]
    payload = json.loads(line.split("KS_ARTIFACTS_OK", 1)[1].strip())
    return {
        "file_count": len(payload.get("files") or []),
        "entry": payload.get("entry_html") or "index.html",
    }


def collect(lib: Path, args: argparse.Namespace) -> tuple[list[dict], list[str]]:
    nodes: list[dict] = []
    space_ids: list[str] = []
    seen: set[str] = set()

    def walk(node_id: str) -> None:
        if len(nodes) >= MAX_NODES or node_id in seen:
            return
        seen.add(node_id)
        node = node_info(lib, node_id)
        space_ids.append(node.get("spaceId") or "")
        nodes.append({
            "id": node.get("id"),
            "title": node.get("title") or "",
            "kind": node.get("kind"),
            "parent_id": node.get("parentId") or "",
        })
        if args.no_children:
            return
        for child_id in node.get("nodes") or []:
            walk(child_id)

    if args.space_id:
        code, out = run_script(lib, "space_api.py", [
            "space.workspace.list-nodes",
            "--space-id", args.space_id,
            "--limit", "100",
        ])
        if code != 0 or not out:
            die(f"列出空间失败: {args.space_id}")
        payload = json.loads(out)
        if "error" in payload:
            die(f"列出空间失败: {payload['error']}")
        for item in payload["data"].get("nodes") or []:
            walk(item.get("id"))
        if not nodes:
            die("该空间下没有可导出的节点")
    else:
        for raw in args.node_id:
            walk(raw.strip())

    unique_space = [s for s in dict.fromkeys([s for s in space_ids if s])]
    return nodes, unique_space


def build_zip(work: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(work).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--node-id", action="append", default=[])
    parser.add_argument("--space-id", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-children", action="store_true")
    try:
        args, _ = parser.parse_known_args()
    except SystemExit:
        die("参数解析失败")

    if not args.node_id and not args.space_id:
        die("必须提供 --node-id 或 --space-id")

    try:
        lib = locate_library()
    except LibraryNotFound as exc:
        die(str(exc))

    out_path = Path(args.out).expanduser().resolve()
    work = out_path.parent / f"_lp_export_{out_path.stem}"
    if work.exists():
        shutil.rmtree(work)
    (work / "pages").mkdir(parents=True, exist_ok=True)
    (work / "tables").mkdir(parents=True, exist_ok=True)

    nodes, space_ids = collect(lib, args)

    pages = [n for n in nodes if n["kind"] == "web"]
    tables = [n for n in nodes if n["kind"] == "database"]
    skipped = [n for n in nodes if n["kind"] not in ("web", "database")]

    table_stats: dict[str, dict] = {}
    for table in tables:
        stats = export_table(lib, table["id"], work)
        table_stats[table["id"]] = stats

    page_stats: dict[str, dict] = {}
    for page in pages:
        page_stats[page["id"]] = export_page(lib, page["id"], work)

    relations: list[dict] = []
    for page in pages:
        code, out = run_script(lib, "page/page_database_relation.py", [
            "--action", "list", "--page-id", page["id"],
        ])
        if code != 0 or not out or '"data"' not in out:
            continue
        payload = json.loads(out)
        for rel in (payload.get("data") or {}).get("relations") or []:
            relations.append({
                "page_id": rel.get("pageId"),
                "database_id": rel.get("databaseId"),
            })

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_space_ids": space_ids,
        "nodes": nodes,
        "relations": relations,
        "stats": {"pages": page_stats, "tables": table_stats},
        "generator": "library-port/export_bundle",
    }
    write_json(work / MANIFEST_NAME, manifest)

    build_zip(work, out_path)
    shutil.rmtree(work, ignore_errors=True)

    size_kb = out_path.stat().st_size // 1024
    print("LP_EXPORT_OK " + json.dumps({
        "bundle": str(out_path),
        "size_kb": size_kb,
        "pages": [{"title": p["title"], "id": p["id"]} for p in pages],
        "tables": [{"title": t["title"], "id": t["id"],
                    "records": table_stats[t["id"]]["record_count"]} for t in tables],
        "relations": len(relations),
        "skipped": [{"title": n["title"], "kind": n["kind"]} for n in skipped],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
