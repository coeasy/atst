# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Bar / Quote 的 extra 低开销容器契约测试。

``extra`` 内部改为「空则 None、非空则扁平 tuple」，但对外必须仍是可变映射：
写入、迭代、``in``、缺失访问、与 dict 比较、赋值（含 ``None``）的行为全部保持；
``to_dict`` 的字段名、层级、缺省值与顺序与改造前逐字节一致（golden 取自改造前实测）。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.domain.models import Bar, Level, Quote, to_dicts, to_tuples


def _assert_json_equal(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """值等价 + 键顺序等价（序列化契约里的顺序语义要锁死）。"""
    assert actual == expected
    assert list(actual.keys()) == list(expected.keys())


class TestSerializationGolden:
    """改造前实测输出的 golden，任何形状变化都必须失败。"""

    def test_empty_bar(self) -> None:
        _assert_json_equal(
            Bar().to_dict(),
            {
                "datetime": "",
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 0,
                "amount": 0.0,
            },
        )

    def test_bar_without_extra(self) -> None:
        bar = Bar(
            datetime="2026-01-02", open=1.0, high=2.0, low=0.5, close=1.5, volume=10, amount=15.0
        )
        _assert_json_equal(
            bar.to_dict(),
            {
                "datetime": "2026-01-02",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
                "amount": 15.0,
            },
        )

    def test_bar_extra_flattened_in_insertion_order(self) -> None:
        bar = Bar(
            datetime="2026-01-02",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10,
            amount=15.0,
            extra={"open_interest": 5, "x": 1},
        )
        _assert_json_equal(
            bar.to_dict(),
            {
                "datetime": "2026-01-02",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
                "amount": 15.0,
                "open_interest": 5,
                "x": 1,
            },
        )

    def test_bar_from_dict_roundtrip(self) -> None:
        bar = Bar.from_dict(
            {"datetime": "2026-01-02", "open": 1.0, "volume": 10, "open_interest": 5}
        )
        _assert_json_equal(
            bar.to_dict(),
            {
                "datetime": "2026-01-02",
                "open": 1.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 10,
                "amount": 0.0,
                "open_interest": 5,
            },
        )

    def test_empty_quote(self) -> None:
        _assert_json_equal(
            Quote().to_dict(),
            {
                "code": "",
                "datetime": None,
                "price": 0.0,
                "last_close": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "amount": 0.0,
                "bid": [],
                "ask": [],
            },
        )

    def test_quote_with_levels_and_extra(self) -> None:
        quote = Quote(
            code="600000",
            datetime="2026-09-02 15:00:00",
            price=10.0,
            last_close=9.9,
            open=9.95,
            high=10.2,
            low=9.7,
            volume=100000,
            amount=1000000.0,
            bid=[Level(9.99, 1000)],
            ask=[Level(10.01, 2000)],
            extra={"float_shares": 10**9, "turnover": 1.23, "_wdf": 0},
        )
        _assert_json_equal(
            quote.to_dict(),
            {
                "code": "600000",
                "datetime": "2026-09-02 15:00:00",
                "price": 10.0,
                "last_close": 9.9,
                "open": 9.95,
                "high": 10.2,
                "low": 9.7,
                "volume": 100000,
                "amount": 1000000.0,
                "bid": [{"price": 9.99, "volume": 1000}],
                "ask": [{"price": 10.01, "volume": 2000}],
                "float_shares": 10**9,
                "turnover": 1.23,
            },
        )
        #: 下划线内部键不进公开 dict 出口，但对象消费者仍能读到。
        assert quote.extra["_wdf"] == 0

    def test_batch_helpers(self) -> None:
        rows = to_dicts([Bar(datetime="d", extra={"a": 1}), Quote(code="c")])
        assert rows == [
            {
                "datetime": "d",
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 0,
                "amount": 0.0,
                "a": 1,
            },
            {
                "code": "c",
                "datetime": None,
                "price": 0.0,
                "last_close": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "amount": 0.0,
                "bid": [],
                "ask": [],
            },
        ]
        assert to_tuples([Bar(datetime="d")]) == [("d", 0.0, 0.0, 0.0, 0.0, 0, 0.0)]


class TestExtraMappingSemantics:
    def test_empty_extra_has_no_backing_dict(self) -> None:
        assert Bar()._extra is None
        assert Quote()._extra is None

    def test_write_read_delete_iterate(self) -> None:
        bar = Bar(datetime="d")
        bar.extra["a"] = 1
        bar.extra["b"] = 2
        assert bar.extra["a"] == 1
        assert list(bar.extra) == ["a", "b"]
        assert list(bar.extra.items()) == [("a", 1), ("b", 2)]
        assert len(bar.extra) == 2
        assert "a" in bar.extra and "zz" not in bar.extra
        assert bar.extra.get("zz") is None
        with pytest.raises(KeyError):
            bar.extra["zz"]
        bar.extra["a"] = 10
        assert list(bar.extra.items()) == [("a", 10), ("b", 2)]
        del bar.extra["a"]
        assert list(bar.extra.items()) == [("b", 2)]
        assert bar.extra.pop("b") == 2
        assert bar.extra == {}
        assert dict(bar.extra) == {}
        assert Bar(datetime="d")._extra is None

    def test_update_setdefault_clear(self) -> None:
        quote = Quote(code="c", extra={"a": 1})
        quote.extra.update({"b": 2})
        assert quote.extra.setdefault("c", 3) == 3
        assert quote.extra.setdefault("a", 9) == 1
        assert dict(quote.extra) == {"a": 1, "b": 2, "c": 3}
        quote.extra.clear()
        assert quote.extra == {}
        assert quote._extra is None

    def test_equality_and_copy(self) -> None:
        bar = Bar(datetime="d", extra={"prev_close": 10.48})
        expected = {"prev_close": 10.48}
        assert bar.extra == expected
        assert expected == bar.extra
        assert bar.extra != {"prev_close": 10.5}
        copied = bar.extra.copy()
        assert type(copied) is dict
        assert copied == {"prev_close": 10.48}

    def test_extra_assignment(self) -> None:
        bar = Bar(datetime="d", extra={"a": 1})
        bar.extra = {"b": 2}
        assert dict(bar.extra) == {"b": 2}
        bar.extra = None
        assert bar.extra == {}

    def test_custom_keys_without_schema(self) -> None:
        quote = Quote(code="c")
        quote.extra["任意键"] = {"nested": [1, 2]}
        assert quote.to_dict()["任意键"] == {"nested": [1, 2]}
        assert quote.extra["任意键"]["nested"] == [1, 2]

    def test_object_equality_includes_extra(self) -> None:
        left = Bar(datetime="d", extra={"a": 1})
        right = Bar(datetime="d", extra={"a": 1})
        assert left == right
        right.extra["a"] = 2
        assert left != right
        assert Quote(code="c") == Quote(code="c")

    def test_turnover_rate_reads_extra(self) -> None:
        quote = Quote(code="c", volume=1000, extra={"float_shares": 100000})
        assert quote.turnover_rate == pytest.approx(1.0)

    def test_datetime_strings_are_canonicalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.domain.models as models

        #: 进程内其它用例可能已把有界缓存填满（饱和后不再去重），先复位。
        monkeypatch.setattr(models, "_DT_CACHE", {})
        monkeypatch.setattr(models, "_DT_CACHE_FULL", False)
        left = "".join(["2026-01-01", " 15:00"])
        right = "".join(["2026-01-01", " 15:00"])
        assert left is not right
        assert Bar(datetime=left).datetime is Bar(datetime=right).datetime

    def test_repr_keeps_mapping_shape(self) -> None:
        assert repr(Bar(datetime="d")).endswith("extra={})")
        assert "extra={'a': 1}" in repr(Bar(datetime="d", extra={"a": 1}))


class TestLazyOrderBook:
    """五档内部压成数值、首次访问时才物化为 Level 列表。"""

    def test_packed_until_accessed(self) -> None:
        quote = Quote(bid=[Level(9.99, 1000)], ask=[Level(10.01, 2000)])
        assert tuple(quote._bid_raw or ()) == (9.99, 1000)
        assert tuple(quote._ask_raw or ()) == (10.01, 2000)
        assert quote._bid_list is None
        assert quote._ask_list is None

    def test_materialize_and_cache(self) -> None:
        quote = Quote(bid=[Level(9.99, 1000)])
        levels = quote.bid
        assert isinstance(levels, list)
        assert isinstance(levels[0], Level)
        assert quote.bid is levels
        levels[0].price = 9.5
        assert quote.to_dict()["bid"] == [{"price": 9.5, "volume": 1000}]

    def test_empty_book_supports_append(self) -> None:
        quote = Quote()
        assert quote.bid == []
        assert quote.to_dict()["bid"] == []
        quote.bid.append(Level(1.0, 2))
        assert quote.to_dict()["bid"] == [{"price": 1.0, "volume": 2}]

    def test_setter_repacks(self) -> None:
        quote = Quote(bid=[Level(1.0, 2)])
        quote.bid = [Level(3.0, 4)]
        assert quote.to_dict()["bid"] == [{"price": 3.0, "volume": 4}]
        quote.ask = None
        assert quote.to_dict()["ask"] == []

    def test_row_style_mappings_are_accepted(self) -> None:
        quote = Quote(bid=[{"price": 1.0, "volume": 5}], ask=[{"price": 2.0, "volume": 6}])
        assert quote.to_dict()["bid"] == [{"price": 1.0, "volume": 5}]
        assert quote.to_dict()["ask"] == [{"price": 2.0, "volume": 6}]
