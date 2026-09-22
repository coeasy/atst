#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail-closed relative-link integrity check for repository documentation.

扫描面是 ``docs/`` 全量加仓库根的 ``*.md``：根级说明（README 尤其）是用户读到的第一份
文档，一度却完全在射程外——``docs/`` 一片绿时 README 里照样能躺着指向已归档计划的死链。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)")

#: 变更日志按天追加，每条写的是**当时**的路径；为了绿灯改写历史条目，
#: 等于让 CHANGELOG 不再能当证据用。归档移动造成的旧链接因此明确豁免。
EXEMPT_BASENAMES = frozenset({"CHANGELOG.md"})


def _markdown_files(*, docs: Path = DOCS, root: Path = ROOT) -> list[Path]:
    """``docs/`` 递归 + 仓库根的顶层说明文件；不手抄名单，新加一份根级文档就自动进射程。"""
    extra = [path for path in sorted(root.glob("*.md")) if path.name not in EXEMPT_BASENAMES]
    return [*sorted(docs.rglob("*.md")), *extra]


def check_docs_links(*, docs: Path = DOCS, root: Path = ROOT) -> list[str]:
    """Return broken or repository-escaping relative Markdown links."""

    root = root.resolve()
    bad: list[str] = []
    for markdown in _markdown_files(docs=docs, root=root):
        text = markdown.read_text(encoding="utf-8")
        for match in _LINK_RE.finditer(text):
            raw_target = match.group(1)
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            target = unquote(raw_target).split("?", 1)[0]
            resolved = (markdown.parent / target).resolve()
            if not resolved.is_relative_to(root):
                bad.append(f"{markdown.relative_to(root)}: {raw_target} (escapes repository)")
                continue
            if not resolved.exists():
                bad.append(f"{markdown.relative_to(root)}: {raw_target}")
    return bad


def main() -> int:
    files = _markdown_files()
    bad = check_docs_links()
    if bad:
        print("broken documentation links:")
        print("\n".join(bad))
        return 1

    print(f"docs link check OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
