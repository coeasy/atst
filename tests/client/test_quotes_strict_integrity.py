# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``strict=True`` 行情：解析器自校验漂移当场拒收（E3 不降级）。

实测背景（V18-B1）：不存在代码 ``sh999999`` 的主站响应长得与真行情一模一样——
回声匹配、载荷齐全，但解析器自校验（哨兵失配 / 成交额越界）会把疑点记在
``extra['_warnings']``。默认路径把这些行当"成功"返回；``strict=True`` 必须把
它们判为 :class:`~atst.errors.IntegrityViolation`，而不是返回一份
"帧合法、值可疑"的行情让调用方自己猜。

替身打在 ``atst.client.dispatch`` 上：``_t_quotes`` 的生成器协议逐 op 手动驱动，
不碰任何网络与连接池。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.client.sync import TdxClient
from atst.errors import IntegrityViolation
from atst.protocol.registry import ParseResult

CMD_REALTIME_QUOTE = 0x0530

DRIFT = "成交额越界: amount=1e15 超出 value 域"


def _clean_row() -> dict[str, Any]:
    return {
        "code": "600519",
        "price": 1324.97,
        "last_close": 1320.0,
        "open": 1321.0,
        "high": 1330.0,
        "low": 1319.0,
        "volume": 1000,
        "amount": 1324970.0,
        "extra": {},
    }


def _drifted_row() -> dict[str, Any]:
    row = _clean_row()
    row["extra"] = {"_warnings": [DRIFT]}
    return row


def _patch_dispatch(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> None:
    def fake_dispatch(frame: Any, **kwargs: Any) -> ParseResult:  # noqa: ARG001
        return ParseResult(
            command=CMD_REALTIME_QUOTE,
            name="realtime_quote",
            tier="L1",
            confidence=1.0,
            rows=[row],
        )

    monkeypatch.setattr("atst.client.dispatch", fake_dispatch)


def _drive(
    monkeypatch: pytest.MonkeyPatch, *, row: dict[str, Any], strict: bool
) -> tuple[list[Any], list[tuple[str, BaseException]]]:
    """手动驱动 ``_t_quotes`` 生成器：喂一帧、收一结果，不碰网络。"""

    _patch_dispatch(monkeypatch, row)
    client = TdxClient(hosts=[["1.2.3.4", 7709]])
    errors: list[tuple[str, BaseException]] = []
    gen = client._t_quotes(["sh600519"], as_format="dict", strict=strict, _collect=errors)
    op = next(gen)
    assert op[0] == "req" and op[1] == CMD_REALTIME_QUOTE
    try:
        gen.send(object())  # dispatch 被替身接管，帧本体不透明即可
    except StopIteration as stop:  # 正常收尾：返回 _emit 的结果
        return list(stop.value or []), errors
    raise AssertionError("单只标的不应有第二次 op")


class TestStrictQuotes:
    def test_clean_row_serves_normally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out, errors = _drive(monkeypatch, row=_clean_row(), strict=False)
        assert len(out) == 1 and out[0]["price"] == 1324.97
        assert errors == []

    def test_drifted_row_defaults_to_a_quiet_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认路径：带疑点的行照样"成功"返回——这正是 strict 存在的理由。

        同时锁死内部标记不出现在公开 dict 出口：``_warnings`` 是下划线前缀的
        内部字段，只留给对象消费者，不污染 DataFrame/CSV 列名。
        """

        out, errors = _drive(monkeypatch, row=_drifted_row(), strict=False)
        assert len(out) == 1
        assert "_warnings" not in out[0]
        assert errors == []

    def test_strict_rejects_a_drifted_row_as_integrity_violation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, errors = _drive(monkeypatch, row=_drifted_row(), strict=True)
        assert out == []
        assert len(errors) == 1
        sym, exc = errors[0]
        assert sym == "sh600519"
        assert isinstance(exc, IntegrityViolation)
        assert exc.context["strict"] is True
        assert exc.context["drift"] == [DRIFT]
        # 漂移描述进了异常文本：调用方在日志里能看到疑点本身
        assert "成交额越界" in str(exc)

    def test_strict_keeps_a_clean_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out, errors = _drive(monkeypatch, row=_clean_row(), strict=True)
        assert len(out) == 1 and out[0]["price"] == 1324.97
        assert errors == []
