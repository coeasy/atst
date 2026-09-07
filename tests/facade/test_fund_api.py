# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P0-1：``UnifiedQuoteAPI.fund_*`` 门面接线测试。

覆盖三个东财基金入口（转发到 :class:`tstdx.web.facade.WebQuoteSession`）：
* ``fund_nav_history`` / ``fund_estimate`` / ``fund_list`` 参数透传正确；
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

    def fund_nav_history(self, code, *, page_size=100, page_index=1):
        self.calls.append(
            ("fund_nav_history", (code,), dict(page_size=page_size, page_index=page_index))
        )
        return [{"date": "2026-09-03", "unit_nav": 0.8948}]

    def fund_estimate(self, code):
        self.calls.append(("fund_estimate", (code,), {}))
        return {"code": code, "gsz": 0.8950}

    def fund_list(self):
        self.calls.append(("fund_list", (), {}))
        return [{"code": "161725", "name": "招商中证白酒指数(LOF)A"}]

    def close(self) -> None:
        self.closed += 1


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr("tstdx.web.facade.WebQuoteSession", lambda *a, **kw: session)
    return session


class TestFundGateways:
    def test_nav_history_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            rows = api.fund_nav_history("161725", page_size=50, page_index=2)
        assert rows == [{"date": "2026-09-03", "unit_nav": 0.8948}]
        assert fake_session.calls[0][0] == "fund_nav_history"
        assert fake_session.calls[0][1] == ("161725",)
        assert fake_session.calls[0][2] == {"page_size": 50, "page_index": 2}
        assert fake_session.closed >= 1

    def test_nav_history_defaults(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            api.fund_nav_history("161725")
        assert fake_session.calls[0][2] == {"page_size": 100, "page_index": 1}

    def test_estimate_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            est = api.fund_estimate("161725")
        assert est["code"] == "161725"
        assert fake_session.calls[0][0] == "fund_estimate"

    def test_list_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            rows = api.fund_list()
        assert rows and rows[0]["code"] == "161725"
        assert fake_session.calls[0][0] == "fund_list"

    def test_close_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """异常路径仍应 close（finally 语义）。"""

        class _Boom:
            def fund_nav_history(self, *a: object, **kw: object):
                raise RuntimeError("boom")

            def close(self) -> None:
                pass

        monkeypatch.setattr("tstdx.web.facade.WebQuoteSession", lambda *a, **kw: _Boom())
        with UnifiedQuoteAPI() as api, pytest.raises(RuntimeError):
            api.fund_nav_history("161725")
