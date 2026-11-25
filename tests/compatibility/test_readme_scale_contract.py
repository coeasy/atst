"""README 的四个规模数字必须由运行期注册表现算回查。

``test_ledger_public_surface.py`` 只核对命令账本自己的文档数字；README 里的
85 / 61 / 217 与绑定数一直是手抄的。绑定数从 304 降到 299 后 README 四处仍写
304，没有任何判据会红。这里从同一批活注册表重算四个数，并且只认**带上下文的
句子**（「85 命令账本」「DIRECT_BINDINGS 299 条精确绑定」……），不做「某个数字
在文件里出现过」这种裸匹配——否则一个 299 出现在版本号里也算通过。
"""

from __future__ import annotations

import re
from pathlib import Path

from atst.protocol.commands import COMMANDS
from atst.protocol.registry import PARSERS
from atst.providers import PROVIDERS
from atst.runtime.bindings import DIRECT_BINDINGS

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

#: 每个指标只认这些带主语的句式。捕获组 1 是待核对的数字。
_CLAIM_PATTERNS = {
    "commands": (r"(\d+)\s*命令账本", r"(\d+)\s*命令、", r"(\d+)\s*命令 /"),
    "parsers": (r"(\d+)\s*精确解析器",),
    "capabilities": (r"(\d+)\s*(?:项\s*)?capability\b",),
    "bindings": (
        r"DIRECT_BINDINGS\s+(\d+)\s*条精确绑定",
        r"（(\d+)\s*条）",
        r"executor\((\d+)\s*绑定\)",
        r"/\s*(\d+)\s*条精确绑定",
    ),
}

#: 绑定数的括号/斜杠句式在别处也会出现，必须同一行点名绑定本身才算数。
_LINE_HINTS = {"bindings": ("DIRECT_BINDINGS", "executor(", "条精确绑定")}


def _live_counts() -> dict[str, int]:
    capabilities = {
        capability
        for provider_id in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider_id).channels
        for capability in channel.capabilities
    }
    return {
        "commands": len(COMMANDS),
        "parsers": len(PARSERS),
        "capabilities": len(capabilities),
        "bindings": len(DIRECT_BINDINGS),
    }


def _claimed_numbers(text: str, metric: str) -> set[int]:
    hints = _LINE_HINTS.get(metric)
    found: set[int] = set()
    for line in text.splitlines():
        if hints and not any(hint in line for hint in hints):
            continue
        for pattern in _CLAIM_PATTERNS[metric]:
            found.update(int(match.group(1)) for match in re.finditer(pattern, line))
    return found


def test_readme_scale_numbers_match_live_registries() -> None:
    live = _live_counts()
    assert all(count > 0 for count in live.values()), f"注册表为空，判据自身失效：{live}"

    readme = README.read_text(encoding="utf-8")
    offenders: list[str] = []
    for metric, expected in live.items():
        claimed = _claimed_numbers(readme, metric)
        if not claimed:
            offenders.append(f"{metric}: README 没有带上下文的规模声明")
        elif claimed != {expected}:
            offenders.append(f"{metric}: README={sorted(claimed)} / 运行期={expected}")
    assert offenders == [], f"README 规模数字与注册表不符：{offenders}"
