#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 xuepengju — https://agentest.vip
# SPDX-License-Identifier: Apache-2.0
"""library-port 共用工具：定位内置 library 技能目录、调用其脚本、记录类型转换。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BUNDLE_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"

# 与原样扁平值互相映射的类型 key
_VALUE_WRAPPERS = {
    "text": lambda v: v,
    "number": lambda v: v,
    "currency": lambda v: v,
    "select": lambda v: v,
    "multi_select": lambda v: v,
    "date": lambda v: v,
    "checkbox": lambda v: v,
    "url": lambda v: v,
    "email": lambda v: v,
    "phone_number": lambda v: v,
    "image": lambda v: v,
    "attachment": lambda v: v,
    "person": lambda v: v,
}


class LibraryNotFound(RuntimeError):
    pass


def _candidate_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []

    local_app = os.environ.get("LOCALAPPDATA")
    app_data = os.environ.get("APPDATA")
    for raw in (local_app, app_data):
        if raw:
            roots.append(Path(raw))

    roots += [
        Path("/Applications"),
        home / "Applications",
        home / "Library" / "Application Support",
        home / ".workbuddy",
        Path("/usr/share"),
        Path("/opt"),
    ]
    return [r for r in roots if r.exists()]


def locate_library() -> Path:
    """定位 WorkBuddy 内置的 library 技能目录，兼容 macOS / Windows / 自定义安装位置。"""
    env_dir = (os.environ.get("CODEBUDDY_SKILL_DIR") or "").strip()
    if env_dir:
        candidate = Path(env_dir).expanduser().resolve()
        if candidate.name == "library" and (candidate / "space_api.py").exists():
            return candidate
        nested = candidate / "skills" / "library"
        if nested.exists() and (nested / "space_api.py").exists():
            return nested

    fixed = [
        "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/"
        "plugins/workbuddy-builtin/skills/library",
        "/Applications/WorkBuddy.app/Contents/Resources/plugins/"
        "workbuddy-builtin/skills/library",
    ]
    for raw in fixed:
        candidate = Path(raw)
        if (candidate / "space_api.py").exists():
            return candidate

    # 兜底：在常见根目录下广度搜索 skills/library，限制深度避免全盘扫描
    seen: set[Path] = set()
    queue: list[tuple[Path, int]] = [(root, 0) for root in _candidate_roots()]
    while queue:
        current, depth = queue.pop(0)
        if depth > 6 or current in seen:
            continue
        seen.add(current)
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if (child / "space_api.py").exists() and child.name == "library":
                return child
            if depth < 6:
                queue.append((child, depth + 1))

    raise LibraryNotFound(
        "未找到 WorkBuddy 内置 library 技能目录；请确认已安装并登录 WorkBuddy。"
    )


def run_script(lib: Path, rel: str, args: list[str], timeout: int = 180) -> tuple[int, str]:
    """调用 library 内的脚本，返回 (返回码, stdout)。"""
    target = lib / rel
    if not target.exists():
        return 1, f"脚本不存在: {target}"
    env = dict(os.environ)
    env.setdefault("CODEBUDDY_SKILL_DIR", str(lib))
    try:
        proc = subprocess.run(
            [sys.executable, str(target)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 1, f"脚本超时: {rel}"
    return proc.returncode, (proc.stdout or "").strip()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def wrap_record(record: dict, properties: list[dict]) -> dict:
    """把 query-database 返回的扁平记录转成 batch-add-records 需要的 {字段: {type: value}}。"""
    type_by_name = {p.get("name"): p.get("type") for p in properties}
    wrapped: dict = {}
    for name, value in record.items():
        if name == "record_id" or value is None or value == "":
            continue
        field_type = type_by_name.get(name)
        if not field_type or field_type not in _VALUE_WRAPPERS:
            continue
        wrapped[name] = {field_type: value}
    return wrapped


def die(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(0)
