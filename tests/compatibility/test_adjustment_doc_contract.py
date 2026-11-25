# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""复权文档契约：README / 接口文档必须反映真实实现，不得再写回已删除的 API 或不存在的类型。

这些断言不是措辞洁癖：2026-11-25 核对时接口文档仍写「动态方法面已随 v2 删除」，
但 ``Client.adjusted_bars`` 是一等公民方法；``derived.md`` 还列出三个仓里根本
不存在的 Result 类型。文档里的这类"看起来合理但指向空物"的句子比不写更误导。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
INTERFACES = ROOT / "docs" / "api" / "interfaces.md"
DERIVED = ROOT / "docs" / "providers" / "derived.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_documents_adjustment_paths() -> None:
    text = _text(README)
    assert "复权 K 线" in text
    assert "adjusted_bars" in text
    assert 'adjust="qfq"' in text
    # README 只读官方客户端板块配置，不得把它写成行情源
    assert "BoardConfigReader" in text and "不含成分股或行情" in text


def test_interfaces_documents_first_class_method_and_two_paths() -> None:
    text = _text(INTERFACES)
    assert "| `adjusted_bars` |" in text
    assert "Client.adjusted_bars(" in text
    assert "Web 服务端复权" in text
    assert "E1010" in text
    # 能力发现面是 217，不是历史残留的 216
    assert "216 项" not in text
    assert "217 项" in text
    # 一等公民的 adjusted_bars 不再写「动态方法面已随 v2 删除」
    section = text.split("### 复权 K 线（`adjusted_bars`）", 1)[1].split("### ", 1)[0]
    assert "动态方法面已随 v2 删除" not in section


def test_adjusted_bars_is_really_a_first_class_method() -> None:
    from atst import Client

    assert hasattr(Client, "adjusted_bars")
    assert hasattr(Client, "capital_changes")


def test_derived_doc_claims_no_nonexistent_result_types() -> None:
    text = _text(DERIVED)
    # 这三个名字只能出现在「不存在」的否定句里，不能当成可用结果类型推荐
    assert "仓里并不存在 `AdjustedBars`" in text
    assert "不要按不存在的类名写代码" in text
    assert "from atst import Client" in text
