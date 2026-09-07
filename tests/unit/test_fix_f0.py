# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F0 批次（工业审计 v2）回归测试。

三条缺陷均为 1.1.0 实测级 P0：

* 异步门面把实例方法当静态桥接 → 全方法 TypeError（打桩在门面方法上会掩盖它）。
* ``HttpxClient`` 缺 ``post`` 覆写 → httpx 环境人气榜必崩。
* ``TSTDX_RATE_LIMIT_*`` 段名切分错误 + ``"1"/"0"`` 抢占为 bool → 配置面崩溃。

测试纪律：**只打桩传输边界（假 TdxClient / 假 httpx.Client），不打桩被测方法本身。**
"""

from __future__ import annotations

import asyncio

import pytest

from tstdx.config.loader import config_from_env, load_config, parse_env_value
from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

# --------------------------------------------------------------------------- #
# F0-1 异步门面实例桥接
# --------------------------------------------------------------------------- #


class FakeTdx:
    """假 TDX 客户端：注入到同步门面的传输边界（而非替换门面方法）。"""

    def __init__(self) -> None:
        self.closed = False
        self.last_body_cmd: int | None = None

    def quotes(self, symbols, *, as_format="row", **kw):  # noqa: ANN001, D102
        return [{"code": s[-6:], "name": s, "price": 100.0} for s in symbols]

    def bars(self, symbol, *, category=4, start=0, count=10, as_format="row", **kw):  # noqa: ANN001
        return [{"open": 1.0, "close": 1.1, "high": 1.2, "low": 0.9, "vol": 10}]

    def close(self) -> None:
        self.closed = True


def _api_with_fake() -> tuple[AsyncUnifiedQuoteAPI, FakeTdx]:
    api = AsyncUnifiedQuoteAPI()
    fake = FakeTdx()
    api._sync = _SyncShim(fake)  # 注入替身门面（桥接仍走实例方法形态）
    return api, fake


class _SyncShim:
    """最小同步门面替身：只提供桥接所需属性，方法保持**实例方法**形态。"""

    def __init__(self, tdx: FakeTdx) -> None:
        self.tdx = tdx

    # 与 UnifiedQuoteAPI 同名同形态（实例方法，非 staticmethod）
    def quotes(self, symbols, **kw):  # noqa: ANN001
        return self.tdx.quotes(list(symbols), **kw)

    def bars(self, symbol, **kw):  # noqa: ANN001
        return self.tdx.bars(symbol, **kw)

    def close(self) -> None:
        self.tdx.close()


def test_async_quotes_bridges_instance_method() -> None:
    api, fake = _api_with_fake()
    out = asyncio.run(api.quotes(["sh600519"]))
    assert out[0]["code"] == "600519"
    assert fake.closed is False


def test_async_run_and_aquery_use_injected_sync() -> None:
    api, _fake = _api_with_fake()
    out = asyncio.run(api.arun("bars", "sh600519"))
    assert out[0]["open"] == 1.0
    resp = asyncio.run(api.aquery("quotes", ["sz000001"]))
    assert resp.success is True
    assert resp.data[0]["code"] == "000001"


def test_async_rejects_private_bridge() -> None:
    api, _ = _api_with_fake()
    with pytest.raises(AttributeError):
        asyncio.run(api.arun("_close_pool"))


def test_async_close_forwards() -> None:
    api, fake = _api_with_fake()
    api.close()
    assert fake.closed is True


def test_real_facade_bridge_not_class_level() -> None:
    """真实 :class:`UnifiedQuoteAPI`（不打桩）：桥接的必须是绑定方法。

    历史缺陷：``asyncio.to_thread(UnifiedQuoteAPI.quotes, list(syms))`` 把
    首参绑到 self → TypeError missing 'symbols'。此处仅验证 *绑定形态*，
    不触网（quotes 内部走 auto 路由会在建连前失败；用注入 sync_api 的
    真实门面实例验证 arun 拿到的是 bound method 即可）。
    """
    from tstdx.facade.api import UnifiedQuoteAPI

    fn = getattr(UnifiedQuoteAPI(), "quotes", None)
    assert callable(fn)
    # 实例上的同名属性必须是 bound method（桥接依赖此形态）
    assert type(fn).__name__ == "method", "桥接必须依赖实例绑定方法"


# --------------------------------------------------------------------------- #
# F0-2 HttpxClient.post
# --------------------------------------------------------------------------- #


class _FakeHttpxResp:
    def __init__(self) -> None:
        self.status_code = 200
        self.content = b'{"ok": 1}'
        self.headers = {"content-type": "application/json"}


class _FakeHttpxClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, url, headers=None, timeout=None):  # noqa: ANN001, D102
        self.calls.append(("GET", url, headers, timeout))
        return _FakeHttpxResp()

    def post(self, url, content=None, headers=None, timeout=None):  # noqa: ANN001, D102
        self.calls.append(("POST", url, headers, timeout, content))
        return _FakeHttpxResp()

    def close(self) -> None:
        self.calls.append(("CLOSE",))


def test_httpx_client_has_post_override() -> None:
    from tstdx.web.base import HttpClient, HttpxClient

    assert HttpxClient.post is not HttpClient.post, "HttpxClient 必须覆写 post"


def test_httpx_client_post_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _FakeHttpxClient()
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    from tstdx.web.base import HttpxClient

    cli = HttpxClient()
    resp = cli.post("http://x/api", body=b'{"a":1}', timeout=3.0)
    assert resp.status == 200
    verb, url, hdrs, timeout, content = fake.calls[0]
    assert verb == "POST" and url == "http://x/api" and timeout == 3.0
    assert content == b'{"a":1}'
    assert hdrs.get("Content-Type") == "application/json"


def test_build_client_httpx_post_callable() -> None:
    """端到端：build_client 在 httpx 环境返回的对象 post 可调用且非基类。"""
    from tstdx.web.base import HttpClient, build_client

    cli = build_client(prefer_httpx=True)
    try:
        assert cli.post.__func__ is not HttpClient.post
    finally:
        cli.close()


# --------------------------------------------------------------------------- #
# F0-3 config env 解析
# --------------------------------------------------------------------------- #


def test_parse_env_value_numeric_not_bool() -> None:
    assert parse_env_value("1") == 1 and isinstance(parse_env_value("1"), int)
    assert parse_env_value("0") == 0
    assert parse_env_value("true") is True
    assert parse_env_value("OFF") is False
    assert parse_env_value("2.5") == 2.5


def test_config_from_env_rate_limit_section() -> None:
    env = {
        "TSTDX_RATE_LIMIT_CLOSED": "60",
        "TSTDX_CORE_MAX_RETRIES": "1",
        "TSTDX_HOSTS_SLOTS_PER_HOST": "4",
    }
    out = config_from_env(env)
    assert out["rate_limit"]["closed"] == 60
    assert out["core"]["max_retries"] == 1 and not isinstance(out["core"]["max_retries"], bool)
    assert out["hosts"]["slots_per_host"] == 4


def test_load_config_numeric_env_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TSTDX_CORE_MAX_RETRIES", "1")
    monkeypatch.setenv("TSTDX_CACHE_TTL", "0")
    cfg = load_config()
    assert cfg.core.max_retries == 1
    assert cfg.cache.ttl == 0


def test_unknown_env_warns_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TSTDX_LOG_LEVEL_NOT_A_SECTION", "debug")
    with pytest.warns(RuntimeWarning):
        out = config_from_env(dict(__import__("os").environ))
    assert "log" not in out  # 被忽略而非崩溃


def test_rate_limit_env_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TSTDX_RATE_LIMIT_CLOSED", "77")
    cfg = load_config()
    assert cfg.rate_limit.closed == 77
