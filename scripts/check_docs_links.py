#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail-closed relative-link integrity check for repository documentation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)")


def check_docs_links(*, docs: Path = DOCS, root: Path = ROOT) -> list[str]:
    """Return broken or repository-escaping relative Markdown links."""

    root = root.resolve()
    bad: list[str] = []
    for markdown in sorted(docs.rglob("*.md")):
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
    bad = check_docs_links()
    if bad:
        print("broken documentation links:")
        print("\n".join(bad))
        return 1

    count = sum(1 for _ in DOCS.rglob("*.md"))
    print(f"docs link check OK ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
