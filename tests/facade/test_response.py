"""统一响应形态 :class:`tstdx.facade.response.ApiResponse` 测试。

覆盖：工厂函数、真值语义、序列化、DataFrame 惰性转换、异常包裹，
以及 :meth:`UnifiedQuoteAPI.query` 统一响应入口的成功/失败两路。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from tstdx.errors import TdxError
from tstdx.facade.api import UnifiedQuoteAPI
from tstdx.facade.response import ApiResponse, err, from_result, ok, wrap

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 工厂与真值语义
# --------------------------------------------------------------------------- #
class TestFactories:
    def test_ok_success(self) -> None:
        r = ok([{"code": "600519"}], extra={"source": "sina"})
        assert r.success is True
        assert r.error == ""
        assert bool(r) is True
        assert r.data == [{"code": "600519"}]
        assert r.extra == {"source": "sina"}

    def test_ok_empty_data_is_still_success(self) -> None:
        """「成功但空」与失败严格区分（停牌日空 K 线是合法状态）。"""
        r = ok([])
        assert r.success and r.data == []

    def test_err_failure(self) -> None:
        r = err("主站不可达", code="E2000")
        assert r.success is False
        assert bool(r) is False
        assert "主站不可达" in r.error
        assert r.code == "E2000"

    def test_default_construction(self) -> None:
        r = ApiResponse()
        assert r.success is True and r.data is None and r.code == ""


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
class TestSerialization:
    def test_to_dict_json_friendly(self) -> None:
        r = ok(
            {"price": 1690.0, "ts": datetime(2026, 9, 1, 10, 0, 0)},
            extra={"source": "tencent"},
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"]["ts"] == "2026-09-01T10:00:00"
        assert d["extra"] == {"source": "tencent"}

    def test_to_dict_dataclass(self) -> None:
        @dataclass
        class Row:
            code: str
            price: float

        r = ok([Row("600519", 1690.0)])
        assert r.to_dict()["data"] == [{"code": "600519", "price": 1690.0}]

    def test_to_dict_nested_list_tuple(self) -> None:
        r = ok([(datetime(2026, 9, 1), 1.0)])
        assert r.to_dict()["data"] == [["2026-09-01T00:00:00", 1.0]]

    def test_repr(self) -> None:
        r = ok([1, 2, 3])
        assert "data_count=3" in repr(r)
        assert "data_count=-" in repr(err("x"))


# --------------------------------------------------------------------------- #
# DataFrame 惰性转换
# --------------------------------------------------------------------------- #
class TestDf:
    def test_df_list_of_dicts(self) -> None:
        pd = pytest.importorskip("pandas")
        r = ok([{"a": 1}, {"a": 2}])
        df = r.df
        assert isinstance(df, pd.DataFrame)
        assert list(df["a"]) == [1, 2]

    def test_df_single_dict(self) -> None:
        pytest.importorskip("pandas")
        r = ok({"a": 1})
        assert len(r.df) == 1

    def test_df_none(self) -> None:
        pytest.importorskip("pandas")
        assert len(err("x").df) == 0


# --------------------------------------------------------------------------- #
# from_result / wrap
# --------------------------------------------------------------------------- #
class TestFromResult:
    def test_passthrough_apiresponse(self) -> None:
        r = ok(1)
        assert from_result(r) is r

    def test_tdx_error_to_failure(self) -> None:
        exc = TdxError("boom", context={"k": 1})
        exc.code = "E1234"
        r = from_result(exc)
        assert r.success is False
        assert r.code == "E1234"
        assert r.extra.get("k") == 1

    def test_generic_exception_to_failure(self) -> None:
        r = from_result(ValueError("bad"))
        assert r.success is False and r.code == "E9999"

    def test_plain_value_to_success(self) -> None:
        assert from_result([1, 2]).data == [1, 2]

    def test_wrap_success(self) -> None:
        assert wrap(lambda a, b: a + b, 1, 2).data == 3

    def test_wrap_exception(self) -> None:
        def boom() -> None:
            raise RuntimeError("x")

        assert wrap(boom).success is False


# --------------------------------------------------------------------------- #
# UnifiedQuoteAPI.query 统一响应入口
# --------------------------------------------------------------------------- #
class TestUnifiedQuery:
    def test_query_success(self) -> None:
        api = UnifiedQuoteAPI()
        resp = api.query("index_list")
        assert resp.success is True
        assert isinstance(resp.data, list) and resp.data

    def test_query_unknown_method(self) -> None:
        api = UnifiedQuoteAPI()
        resp = api.query("no_such_method")
        assert resp.success is False
        assert "no_such_method" in resp.error

    def test_query_exception_to_failure(self) -> None:
        class BoomAPI(UnifiedQuoteAPI):
            def index_list(self):  # noqa: ANN201
                raise TdxError("网络不可达")

        resp = BoomAPI().query("index_list")
        assert resp.success is False
        assert "网络不可达" in resp.error

    def test_query_empty_is_success(self) -> None:
        class EmptyAPI(UnifiedQuoteAPI):
            @staticmethod
            def search_symbols(pattern: str, **kw):  # noqa: ANN001, ANN202
                return []

        resp = EmptyAPI().query("search_symbols", "zzzz")
        assert resp.success is True and resp.data == []
