# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""板块/排行会话 Mixin 离线单测（覆盖率补强）。

会话层是纯编排：按域名挑适配器、注入进程级共享 client、透传分页参数、
``finally`` 关闭。这里用假适配器验证参数翻译与关闭语义，并覆盖上游异常
穿透与空结果原样返回。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources._session_boards import BoardSessionMixin

pytestmark = pytest.mark.unit


class _Fake:
    """通用假适配器：记录构造参数与每次方法调用。"""

    results: dict[str, Any] = {}
    instances: list[_Fake] = []

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.client = client
        self.kwargs = kwargs
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        type(self).instances.append(self)

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            handler = type(self).results.get(name, [])
            if isinstance(handler, Exception):
                raise handler
            if callable(handler):
                return handler(*args, **kwargs)
            return handler

        return _call


def _install(monkeypatch: pytest.MonkeyPatch, attr: str, **results: Any) -> type[_Fake]:
    fake_cls = type(f"_Fake{attr}", (_Fake,), {"results": results})
    monkeypatch.setattr(f"atst.sources.boards.{attr}", fake_cls)
    return fake_cls


@pytest.fixture(autouse=True)
def _clean_instances() -> Any:
    _Fake.instances.clear()
    yield
    _Fake.instances.clear()


@pytest.fixture(autouse=True)
def _shared(monkeypatch: pytest.MonkeyPatch) -> Any:
    sentinel = object()
    monkeypatch.setattr("atst.sources._session_boards._shared_http", lambda: sentinel)
    return sentinel


def test_industry_boards_and_board_list(monkeypatch: pytest.MonkeyPatch, _shared: Any) -> None:
    _install(monkeypatch, "SinaIndustryBoardSource", fetch_boards=[{"code": "new_dzxx"}])
    _install(monkeypatch, "SinaBoardListSource", fetch_boards=[{"code": "gn_hwqc"}])

    assert BoardSessionMixin.industry_boards() == [{"code": "new_dzxx"}]
    assert BoardSessionMixin.board_list("region") == [{"code": "gn_hwqc"}]

    first, second = _Fake.instances
    assert first.client is _shared
    assert first.calls == [("fetch_boards", (), {})]
    assert second.calls == [("fetch_boards", ("region",), {})]
    assert first.closed and second.closed


def test_board_members_forwards_paging(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "SinaBoardMemberSource", fetch_members=[{"code": "600519"}])
    out = BoardSessionMixin.board_members("gn_hwqc", page_size=50, max_pages=2)
    assert out == [{"code": "600519"}]
    inst = _Fake.instances[0]
    assert inst.calls == [("fetch_members", ("gn_hwqc",), {"page_size": 50, "max_pages": 2})]
    assert inst.closed is True


def test_tencent_and_eastmoney_board_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "TencentBoardRankSource", fetch_boards=[{"code": "bank"}])
    _install(monkeypatch, "EastmoneyBoardSource", fetch_boards=[{"code": "BK0475"}])

    assert BoardSessionMixin.board_rank("concept", limit=5, page=2) == [{"code": "bank"}]
    assert BoardSessionMixin.em_boards("industry", limit=10, page=3) == [{"code": "BK0475"}]

    tencent, eastmoney = _Fake.instances
    assert tencent.calls == [("fetch_boards", ("concept",), {"limit": 5, "page": 2})]
    assert eastmoney.calls == [("fetch_boards", ("industry",), {"limit": 10, "page": 3})]
    assert tencent.closed and eastmoney.closed


def test_eastmoney_members_and_stock_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "EastmoneyBoardSource",
        fetch_members=[{"code": "600519"}],
        fetch_stock_boards=[{"code": "BK0896"}],
    )
    assert BoardSessionMixin.em_board_members("BK0475", limit=20, page=2) == [{"code": "600519"}]
    assert BoardSessionMixin.stock_boards("sh600519") == [{"code": "BK0896"}]

    first, second = _Fake.instances
    assert first.calls == [("fetch_members", ("BK0475",), {"limit": 20, "page": 2})]
    assert second.calls == [("fetch_stock_boards", ("sh600519",), {})]
    assert first.closed and second.closed


def test_empty_payload_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "SinaBoardMemberSource", fetch_members=[])
    _install(monkeypatch, "EastmoneyBoardSource", fetch_members=[])
    assert BoardSessionMixin.board_members("empty") == []
    assert BoardSessionMixin.em_board_members("BK0000") == []


def test_upstream_error_propagates_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "SinaBoardMemberSource",
        fetch_members=WebSourceError("board upstream down", context={"source": "sina"}),
    )
    with pytest.raises(WebSourceError, match="upstream down") as caught:
        BoardSessionMixin.board_members("gn_hwqc")
    assert caught.value.code == "E7000"
    assert _Fake.instances[0].closed is True


# --------------------------------------------------------------------------- #
# board_attribution：symbol/board_code/board_name 三个互斥维度
# --------------------------------------------------------------------------- #
class _FakeDc:
    instances: list[_FakeDc] = []
    rows: list[dict[str, Any]] = [{"BOARD_NAME": "白酒"}]

    def __init__(self, *, report: str | None = None, client: Any = None, **kwargs: Any) -> None:
        self.report = report
        self.client = client
        self.kwargs = kwargs
        self.closed = False
        self.fetch_kwargs: dict[str, Any] | None = None
        type(self).instances.append(self)

    def fetch_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fetch_kwargs = kwargs
        return list(type(self).rows)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def dc(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDc]:
    _FakeDc.instances.clear()
    monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDc)
    return _FakeDc


def test_board_attribution_symbol_filter_and_paging(dc: type[_FakeDc]) -> None:
    rows = BoardSessionMixin.board_attribution(
        "sh600519", page=2, size=50, all_pages=True, max_pages=3
    )
    assert rows == [{"BOARD_NAME": "白酒"}]
    inst = dc.instances[0]
    assert inst.report == "board_attribution"
    assert inst.fetch_kwargs == {
        "filters": ['SECURITY_CODE="600519"'],
        "sort_columns": "BOARD_RANK",
        "sort_types": "1",
        "page": 2,
        "size": 50,
        "all_pages": True,
        "max_pages": 3,
        "allow_empty": True,
    }
    assert inst.closed is True


def test_board_attribution_index_guard_skips_request(dc: type[_FakeDc]) -> None:
    """指数不在 F10 覆盖范围：直接空集，且一次请求都不发。"""
    assert BoardSessionMixin.board_attribution("sh000001") == []
    assert dc.instances == []


def test_board_attribution_board_code_shapes(dc: type[_FakeDc]) -> None:
    BoardSessionMixin.board_attribution(board_code="bk0896")
    assert dc.instances[-1].fetch_kwargs["filters"] == ['NEW_BOARD_CODE="BK0896"']

    BoardSessionMixin.board_attribution(board_code=" 896 ")
    assert dc.instances[-1].fetch_kwargs["filters"] == ['BOARD_CODE="896"']


def test_board_attribution_board_name_and_precedence(dc: type[_FakeDc]) -> None:
    BoardSessionMixin.board_attribution(board_name=" 白酒 ")
    assert dc.instances[-1].fetch_kwargs["filters"] == ['BOARD_NAME="白酒"']

    # 三个维度同时给出时只保留 symbol 一道过滤
    BoardSessionMixin.board_attribution("600519", board_code="BK0896", board_name="白酒")
    assert dc.instances[-1].fetch_kwargs["filters"] == ['SECURITY_CODE="600519"']


def test_board_attribution_empty_report_is_not_an_error(
    dc: type[_FakeDc], monkeypatch: pytest.MonkeyPatch
) -> None:
    """稀疏报表查不到是正常空集（allow_empty=True 已下发）。"""
    monkeypatch.setattr(dc, "rows", [])
    assert BoardSessionMixin.board_attribution(board_name="不存在") == []
    assert dc.instances[-1].fetch_kwargs["allow_empty"] is True
    assert dc.instances[-1].closed is True
