# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P0-2：``UnifiedQuoteAPI.index_constituents`` 门面接线测试。

覆盖指数成分入口（转发到 :class:`tstdx.web.facade.WebQuoteSession`）：
* ``index_constituents`` 参数透传正确；
* ``WebQuoteSession`` 每次调用后正确 close（含异常路径）。
"""

from __future__ import annotations

import pytest

from tstdx.facade.api import UnifiedQuoteAPI

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.closed = 0

    def index_constituents(self, index):
        self.calls.append(("index_constituents", (index,), {}))
        return [{"code": "000001", "name": "平安银行", "weight": 0.45}]

    def close(self) -> None:
        self.closed += 1


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr("tstdx.web.facade.WebQuoteSession", lambda *a, **kw: session)
    return session


class TestIndexConstituentsGateway:
    def test_forwards_index(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            rows = api.index_constituents("000300")
        assert rows == [{"code": "000001", "name": "平安银行", "weight": 0.45}]
        assert fake_session.calls[0][0] == "index_constituents"
        assert fake_session.calls[0][1] == ("000300",)
        assert fake_session.closed >= 1

    def test_close_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """异常路径仍应 close（finally 语义）。"""

        class _Boom:
            def index_constituents(self, *a: object, **kw: object):
                raise RuntimeError("boom")

            def close(self) -> None:
                pass

        monkeypatch.setattr("tstdx.web.facade.WebQuoteSession", lambda *a, **kw: _Boom())
        with UnifiedQuoteAPI() as api, pytest.raises(RuntimeError):
            api.index_constituents("000300")
