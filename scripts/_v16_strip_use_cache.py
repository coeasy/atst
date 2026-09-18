"""One-shot mechanical strip of use_cache plumbing (v16 decision 5)."""

import re
import sys
from pathlib import Path

ROOT = Path("tstdx")
FILES = [
    ROOT / "client_api.py",
    ROOT / "orchestration.py",
    ROOT / "runtime" / "legacy_bridge.py",
    ROOT / "runtime" / "gateway.py",
    ROOT / "runtime" / "runtime.py",
    ROOT / "execution" / "semantic.py",
    ROOT / "integration" / "runtime_http.py",
    ROOT / "integration" / "runtime_ws.py",
    ROOT / "integration" / "mcp" / "_tools_impl.py",
    ROOT / "integration" / "mcp" / "_tools_spec.py",
    ROOT / "cli" / "runtime_commands.py",
]

LINE_PATTERNS = [
    re.compile(r"^\s*use_cache: bool = True,\n$"),
    re.compile(r"^\s*use_cache=use_cache,\n$"),
    re.compile(r"^\s*use_cache = bool\(kwargs\.pop\(\"use_cache\", True\)\)\n$"),
    re.compile(r"^\s*use_cache = bool\(params\.get\(\"use_cache\", True\)\)\n$"),
    re.compile(r"^\s*use_cache=bool\(payload\.get\(\"use_cache\", True\)\),\n$"),
    re.compile(r"^\s*\"use_cache\":\s*\{[^}]*\},?\n$"),
    re.compile(r"^\s*use_cache = (use_cache|bool\(.*use_cache.*\))\n$"),
]
INLINE_PATTERNS = [
    (re.compile(r", use_cache: bool = True"), ""),
    (re.compile(r", use_cache=use_cache"), ""),
    (re.compile(r"\(\s*use_cache=use_cache\s*\)"), "()"),
    (re.compile(r"use_cache: bool = True, "), ""),
    (re.compile(r"use_cache=use_cache, "), ""),
]
BLOCK_PATTERNS = [
    re.compile(r"        if cap == \"sync_daily\":\n            use_cache = False\n"),
    re.compile(r"from \.\.cache_semantic import SemanticResultCache\n"),
]

changed = []
for path in FILES:
    if not path.exists():
        print(f"MISSING {path}")
        continue
    src = orig = path.read_text(encoding="utf-8")
    for pat in BLOCK_PATTERNS:
        src = pat.sub("", src)
    lines = src.splitlines(keepends=True)
    kept = []
    for line in lines:
        if any(p.match(line) for p in LINE_PATTERNS):
            continue
        kept.append(line)
    src = "".join(kept)
    for pat, rep in INLINE_PATTERNS:
        src = pat.sub(rep, src)
    if src != orig:
        path.write_text(src, encoding="utf-8")
        changed.append(str(path))

print("changed:", *changed, sep="\n  ")
