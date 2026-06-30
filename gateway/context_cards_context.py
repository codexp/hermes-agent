#!/usr/bin/env python3
"""Resolve and manage Hermes Gateway Context Cards.

Update obligation for agents: when changing this script's storage layout,
command surface, render order, or frontmatter behavior, also update the design
canvas at:
  /home/ewe/Dokumente/eos-vault/Hermes/Hermes Gateway Context Cards.md

This script is intentionally deterministic and low-level. Gateway command
handling may preprocess or intelligently normalize content, then call this
script with only ID arguments so file targeting stays centralized here.

Storage layout:
  ~/.hermes/gateway-context/global.md
  ~/.hermes/gateway-context/<platform>/gateway.md
  ~/.hermes/gateway-context/<platform>/<workspace_id>/channel/<channel_id>/MAIN.md
  ~/.hermes/gateway-context/<platform>/<workspace_id>/channel/<channel_id>/<thread_id>.md

Usage:
  context get [--scope] <platform> <workspace_id> <channel_id> [thread_id]
  context resolve <platform> <workspace_id> <channel_id> [thread_id]
  context set <platform> <workspace_id> <channel_id> [thread_id] -- <content>
  context path <platform> <workspace_id> <channel_id> [thread_id]

Notes:
  Cards store machine metadata in YAML-like frontmatter. The resolver keeps an
  agent-optimized compiled frontmatter block in the rendered context: deeper
  scope_type values override broader ones, definition metadata is folded in, and
  list metadata such as skills/tools/toolsets/resources is deduplicated.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BASE = Path(
    os.environ.get(
        "HERMES_GATEWAY_CONTEXT_HOME",
        Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "gateway-context",
    )
).expanduser()


class ContextError(Exception):
    pass


def _clean_id(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ContextError(f"missing {label}")
    if "/" in value or "\x00" in value or value in {".", ".."}:
        raise ContextError(f"invalid {label}: {value!r}")
    return value


def workspace_root(platform: str, workspace_id: str) -> Path:
    platform = _clean_id(platform, "platform")
    workspace_id = _clean_id(workspace_id, "workspace_id")
    return BASE / platform / workspace_id


def scope_path(platform: str, workspace_id: str, channel_id: str, thread_id: str | None = None) -> Path:
    root = workspace_root(platform, workspace_id)
    channel_id = _clean_id(channel_id, "channel_id")
    channel_dir = root / "channel" / channel_id
    if thread_id:
        thread_id = _clean_id(thread_id, "thread_id")
        if thread_id in {"MAIN", "MAIN.md"}:
            return channel_dir / "MAIN.md"
        if thread_id.endswith(".md"):
            return channel_dir / thread_id
        return channel_dir / f"{thread_id}.md"
    return channel_dir / "MAIN.md"


def bundle_paths(platform: str, workspace_id: str, channel_id: str, thread_id: str | None = None) -> list[Path]:
    """Return paths in rendered bundle order: thread > channel > global > gateway."""
    paths: list[Path] = []
    if thread_id:
        paths.append(scope_path(platform, workspace_id, channel_id, thread_id))
    paths.extend([
        scope_path(platform, workspace_id, channel_id, None),
        BASE / "global.md",
        BASE / _clean_id(platform, "platform") / "gateway.md",
    ])
    return paths


def split_frontmatter(text: str) -> tuple[dict[str, list[str]], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip("\n")
    body_start = end + len("\n---")
    while body_start < len(text) and text[body_start:body_start + 1] == "\n":
        body_start += 1
    data: dict[str, list[str]] = {}
    key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            if value:
                data.setdefault(key, []).append(value.strip('"\''))
            else:
                data.setdefault(key, [])
            continue
        m = re.match(r"^\s*-\s*(.+)$", line)
        if m and key:
            data.setdefault(key, []).append(m.group(1).strip().strip('"\''))
    return data, text[body_start:]


def _workspace_root_for_path(path: Path) -> Path | None:
    try:
        rel = path.resolve().relative_to(BASE.resolve())
    except Exception:
        return None
    # <platform>/<workspace_id>/...
    if len(rel.parts) >= 3:
        return BASE / rel.parts[0] / rel.parts[1]
    return None


def _resolve_include(path: Path, include: str) -> tuple[Path | None, str | None]:
    inc_path = Path(include).expanduser()
    if not inc_path.is_absolute():
        workspace = _workspace_root_for_path(path)
        inc_path = (workspace / include) if workspace is not None else (path.parent / include)
    try:
        inc_resolved = inc_path.resolve()
        inc_resolved.relative_to(BASE.resolve())
    except Exception:
        return None, f"include outside gateway-context skipped: {include}"
    return inc_resolved, None


COMPILED_FRONTMATTER_ORDER = ["scope_type", "skills", "tools", "toolsets", "resources"]


def _dedupe_extend(values: list[str], additions: list[str]) -> None:
    for value in additions:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)


def merge_metadata(target: dict[str, list[str]], meta: dict[str, list[str]], *, allow_scope_override: bool = False) -> None:
    """Merge card metadata for the compiled agent context frontmatter."""
    for key, values in meta.items():
        clean_values = [str(value).strip() for value in values if str(value).strip()]
        if not clean_values:
            continue
        if key == "scope_type":
            if allow_scope_override or "scope_type" not in target:
                target["scope_type"] = [clean_values[-1]]
            continue
        _dedupe_extend(target.setdefault(key, []), clean_values)


def render_frontmatter(meta: dict[str, list[str]]) -> str:
    keys = [key for key in COMPILED_FRONTMATTER_ORDER if key in meta]
    keys.extend(sorted(key for key in meta if key not in keys and key != "include"))
    lines: list[str] = []
    for key in keys:
        values = [value for value in meta.get(key, []) if value]
        if not values:
            continue
        if key == "scope_type":
            lines.append(f"scope_type: {values[-1]}")
        else:
            lines.append(f"{key}:")
            lines.extend(f"  - {value}" for value in values)
    return "---\n" + "\n".join(lines) + "\n---" if lines else ""


def parse_card(path: Path) -> tuple[dict[str, list[str]], str, list[str]]:
    """Return (frontmatter, body bytes after frontmatter, warnings) for an existing card."""
    if not path.exists():
        return {}, "", []
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    warnings: list[str] = []
    if "include" in meta and "require" in meta:
        warnings.append(f"card uses both include and require: {path}")
    return meta, body, warnings


def render_include(
    path: Path,
    *,
    active: set[Path] | None = None,
    rendered: set[Path] | None = None,
) -> tuple[dict[str, list[str]], str, list[str]]:
    warnings: list[str] = []
    if not path.exists():
        return {}, "", [f"missing include target: {path}"]
    active = active or set()
    rendered = rendered if rendered is not None else set()
    resolved = path.resolve()
    if resolved in active:
        return {}, "", [f"cyclic include skipped: {path}"]
    if resolved in rendered:
        return {}, "", []
    active.add(resolved)
    rendered.add(resolved)
    meta, body, parse_warnings = parse_card(resolved)
    warnings.extend(parse_warnings)
    merged_meta: dict[str, list[str]] = {}
    merge_metadata(merged_meta, meta)
    chunks: list[str] = []
    if body:
        chunks.append(body)
    for inc in meta.get("include", []):
        inc_path, warning = _resolve_include(resolved, inc)
        if warning:
            warnings.append(warning)
            continue
        assert inc_path is not None
        inc_meta, inc_body, inc_warnings = render_include(inc_path, active=active, rendered=rendered)
        merge_metadata(merged_meta, inc_meta)
        warnings.extend(inc_warnings)
        if inc_body:
            chunks.append(inc_body)
    active.discard(resolved)
    return merged_meta, "\n\n".join(chunks), warnings


def scope_type_definition_path(platform: str, workspace_id: str, scope_type: str) -> Path:
    return workspace_root(platform, workspace_id) / "definitions" / "scope-types" / f"{scope_type}.md"


def render_bundle(platform: str, workspace_id: str, channel_id: str, thread_id: str | None = None) -> tuple[str, list[str]]:
    card_chunks: list[str] = []
    include_chunks: list[str] = []
    definition_chunks: list[str] = []
    warnings: list[str] = []
    seen_includes: set[Path] = set()
    rendered_includes: set[Path] = set()
    compiled_meta: dict[str, list[str]] = {}

    for path in bundle_paths(platform, workspace_id, channel_id, thread_id):
        meta, body, parse_warnings = parse_card(path)
        warnings.extend(parse_warnings)
        merge_metadata(compiled_meta, meta)
        if body:
            card_chunks.append(body)
        for inc in meta.get("include", []):
            inc_path, warning = _resolve_include(path, inc)
            if warning:
                warnings.append(warning)
                continue
            assert inc_path is not None
            inc_resolved = inc_path.resolve()
            if inc_resolved in seen_includes:
                continue
            seen_includes.add(inc_resolved)
            inc_meta, inc_body, inc_warnings = render_include(inc_resolved, rendered=rendered_includes)
            merge_metadata(compiled_meta, inc_meta)
            warnings.extend(inc_warnings)
            if inc_body:
                include_chunks.append(inc_body)

    for scope_type in list(compiled_meta.get("scope_type", [])):
        definition = scope_type_definition_path(platform, workspace_id, scope_type)
        if not definition.exists():
            continue
        def_meta, def_body, def_warnings = render_include(definition, rendered=rendered_includes)
        merge_metadata(compiled_meta, def_meta, allow_scope_override=False)
        warnings.extend(def_warnings)
        if def_body:
            definition_chunks.append(def_body)

    frontmatter = render_frontmatter(compiled_meta)
    body = "\n\n".join([*card_chunks, *definition_chunks, *include_chunks])
    return "\n\n".join(part for part in [frontmatter, body] if part), warnings


def set_scope(platform: str, workspace_id: str, channel_id: str, thread_id: str | None, content: str) -> Path:
    path = scope_path(platform, workspace_id, channel_id, thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def parse_scope_args(values: list[str]) -> tuple[str, str, str, str | None]:
    if len(values) not in {3, 4}:
        raise ContextError("expected <platform> <workspace_id> <channel_id> [thread_id]")
    platform, workspace_id, channel_id = values[:3]
    thread_id = values[3] if len(values) == 4 else None
    return platform, workspace_id, channel_id, thread_id


def command_get(argv: list[str]) -> int:
    scope_only = False
    if argv and argv[0] == "--scope":
        scope_only = True
        argv = argv[1:]
    platform, workspace_id, channel_id, thread_id = parse_scope_args(argv)
    if scope_only:
        path = scope_path(platform, workspace_id, channel_id, thread_id)
        if path.exists():
            body = path.read_text(encoding="utf-8")
            warnings = []
        else:
            body, warnings = "", []
    else:
        body, warnings = render_bundle(platform, workspace_id, channel_id, thread_id)
    if body:
        sys.stdout.write(body)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


def command_set(argv: list[str]) -> int:
    if "--" in argv:
        idx = argv.index("--")
        ids = argv[:idx]
        content = " ".join(argv[idx + 1:])
    else:
        ids = argv[:4] if len(argv) >= 4 else argv[:3]
        content = " ".join(argv[len(ids):])
    platform, workspace_id, channel_id, thread_id = parse_scope_args(ids)
    if not content.strip():
        content = sys.stdin.read()
    if not content.strip():
        raise ContextError("set requires content after -- or on stdin")
    path = set_scope(platform, workspace_id, channel_id, thread_id, content)
    print(path)
    return 0


def command_path(argv: list[str]) -> int:
    platform, workspace_id, channel_id, thread_id = parse_scope_args(argv)
    print(scope_path(platform, workspace_id, channel_id, thread_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        return 0
    cmd = argv.pop(0)
    try:
        if cmd in {"get", "resolve"}:
            return command_get(argv)
        if cmd == "set":
            return command_set(argv)
        if cmd == "path":
            return command_path(argv)
        raise ContextError(f"unknown command: {cmd}")
    except ContextError as exc:
        print(f"context: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
