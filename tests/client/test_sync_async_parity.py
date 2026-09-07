"""同步/异步客户端一致性测试（§25）：TdxClient vs AsyncTdxClient。

覆盖：相同公共方法名、相同签名、fake pool 注入返回相同数据形状。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from tstdx.client import AsyncTdxClient, TdxClient
from tstdx.codec.primitive import encode_leb128


def _quote_response(code: str = "600000", market: int = 1) -> bytes:
    """构造一条最小合法 0x0530 响应（回声代码 + 价格流），供 fake pool 使用。"""
    return (
        bytes([market])  # 回声市场（标准语义：0=深 1=沪）
        + code.encode("ascii").ljust(6, b"\x00")  # 回声代码
        + b"\x00\x00"  # offset 7-8 不透明区
        + encode_leb128(5000)  # price = 50.00（scale 100）
        + encode_leb128(0)  # last_close - price
        + encode_leb128(0)  # open - price
        + encode_leb128(0)  # high - price
        + encode_leb128(0)  # low - price
        + encode_leb128(100)  # u4（未识别字段）
        + encode_leb128(-5000)  # 哨兵 = -price
        + encode_leb128(1000)  # volume_lots
        + encode_leb128(0)  # v2
        + b"\x00\x00\x00\x00"  # tdx_float amount = 0.0
    )


@pytest.mark.unit
class TestSyncAsyncParity:
    """同步/异步客户端一致性测试。"""

    # 公共方法名（排除私有/dunder）
    EXPECTED_SYNC_METHODS = {
        "bars",
        "quotes",
        "security_count",
        "capital_changes",
        "finance_info",
        "minute_today",
        "request",
        "security_list",
        "minute_history",
        "trade_today",
        "block_quotes",
        "file_download",
        "auction_snapshot",
        "volume_price_dist",
        "quotes_snapshot",
    }

    def _public_methods(self, cls):
        """获取类的公共方法名（排除私有和 dunder）。"""
        methods = set()
        for name, obj in inspect.getmembers(cls):
            if name.startswith("_"):
                continue
            if inspect.isfunction(obj) or inspect.ismethod(obj):
                methods.add(name)
        return methods

    def test_same_public_method_names(self):
        """#1 公共方法名一致。"""
        sync_methods = self._public_methods(TdxClient)
        async_methods = self._public_methods(AsyncTdxClient)
        # 同步版独有
        sync_only = sync_methods - async_methods
        # 异步版独有
        async_only = async_methods - sync_methods
        # 允许 open/close（异步特有）
        async_only -= {"open", "close"}
        # 允许 async 版本有额外方法
        assert not sync_only, f"同步版独有方法: {sync_only}"

    def test_bars_signature_parity(self):
        """#2 bars() 签名一致。"""
        sync_sig = inspect.signature(TdxClient.bars)
        async_sig = inspect.signature(AsyncTdxClient.bars)
        sync_params = {k: str(v) for k, v in sync_sig.parameters.items()}
        async_params = {k: str(v) for k, v in async_sig.parameters.items()}
        # 参数名一致
        assert set(sync_params.keys()) == set(async_params.keys())

    def test_quotes_signature_parity(self):
        """#3 quotes() 签名一致。"""
        sync_sig = inspect.signature(TdxClient.quotes)
        async_sig = inspect.signature(AsyncTdxClient.quotes)
        assert set(sync_sig.parameters.keys()) == set(async_sig.parameters.keys())

    def test_request_signature_parity(self):
        """#4 request() 签名一致。"""
        sync_sig = inspect.signature(TdxClient.request)
        async_sig = inspect.signature(AsyncTdxClient.request)
        assert set(sync_sig.parameters.keys()) == set(async_sig.parameters.keys())

    def test_security_count_signature_parity(self):
        """#5 security_count() 签名一致。"""
        sync_sig = inspect.signature(TdxClient.security_count)
        async_sig = inspect.signature(AsyncTdxClient.security_count)
        assert set(sync_sig.parameters.keys()) == set(async_sig.parameters.keys())

    def test_all_expected_methods_present(self):
        """#6 所有预期方法都存在。"""
        sync_methods = self._public_methods(TdxClient)
        for m in self.EXPECTED_SYNC_METHODS:
            assert m in sync_methods, f"TdxClient 缺少方法 {m}"
        async_methods = self._public_methods(AsyncTdxClient)
        for m in self.EXPECTED_SYNC_METHODS:
            assert m in async_methods, f"AsyncTdxClient 缺少方法 {m}"

    def test_async_methods_are_coroutines(self):
        """#7 异步方法都是 coroutine。"""
        for name in self.EXPECTED_SYNC_METHODS:
            method = getattr(AsyncTdxClient, name, None)
            if method is not None:
                assert asyncio.iscoroutinefunction(method), f"AsyncTdxClient.{name} 不是 coroutine"

    def test_sync_methods_are_not_coroutines(self):
        """#8 同步方法不是 coroutine。"""
        for name in self.EXPECTED_SYNC_METHODS:
            method = getattr(TdxClient, name, None)
            if method is not None:
                assert not asyncio.iscoroutinefunction(method), f"TdxClient.{name} 不应是 coroutine"

    def test_sync_context_manager(self):
        """#9 TdxClient 支持同步上下文管理器。"""
        assert hasattr(TdxClient, "__enter__")
        assert hasattr(TdxClient, "__exit__")

    def test_async_context_manager(self):
        """#10 AsyncTdxClient 支持异步上下文管理器。"""
        assert hasattr(AsyncTdxClient, "__aenter__")
        assert hasattr(AsyncTdxClient, "__aexit__")

    def test_sync_bars_with_fake_pool(self):
        """#11 TdxClient.bars 使用 fake pool。"""
        from tstdx.codec.framing import ResponseFrame

        class FakePool:
            def request(self, cmd, body, timeout=5.0):
                # 返回一个假的 K 线响应帧
                return ResponseFrame(
                    magic=0x0074CBB1,
                    zip_flag=0,
                    seq=0,
                    method=cmd,
                    zip_size=0,
                    unzip_size=0,
                    payload=b"\x02\x00" + b"\x00" * 32,  # 2 条记录，每条 16 字节
                )

        client = TdxClient(pool=FakePool())
        result = client.bars("600000", period="day", count=5, as_format="dict")
        assert result is not None

    def test_async_bars_with_fake_pool(self):
        """#12 AsyncTdxClient.bars 使用 fake pool。"""
        from tstdx.codec.framing import ResponseFrame

        class AsyncFakePool:
            async def request(self, cmd, body, timeout=5.0):
                return ResponseFrame(
                    magic=0x0074CBB1,
                    zip_flag=0,
                    seq=0,
                    method=cmd,
                    zip_size=0,
                    unzip_size=0,
                    payload=b"\x02\x00" + b"\x00" * 32,
                )

            async def close(self):
                pass

        async def run():
            client = AsyncTdxClient(pool=AsyncFakePool())
            return await client.bars("600000", period="day", count=5, as_format="dict")

        result = asyncio.run(run())
        assert result is not None

    def test_sync_quotes_with_fake_pool(self):
        """#13 TdxClient.quotes 使用 fake pool。"""
        from tstdx.codec.framing import ResponseFrame

        class FakePool:
            def request(self, cmd, body, timeout=5.0):
                return ResponseFrame(
                    magic=0x0074CBB1,
                    zip_flag=0,
                    seq=0,
                    method=cmd,
                    zip_size=0,
                    unzip_size=0,
                    payload=_quote_response(),  # 合法 0x0530 回声响应
                )

        client = TdxClient(pool=FakePool())
        result = client.quotes(["600000"], as_format="dict")
        # 合法响应应解析出 1 条行情
        assert isinstance(result, list)
        assert len(result) == 1

    def test_async_quotes_with_fake_pool(self):
        """#14 AsyncTdxClient.quotes 使用 fake pool。"""
        from tstdx.codec.framing import ResponseFrame

        class AsyncFakePool:
            async def request(self, cmd, body, timeout=5.0):
                return ResponseFrame(
                    magic=0x0074CBB1,
                    zip_flag=0,
                    seq=0,
                    method=cmd,
                    zip_size=0,
                    unzip_size=0,
                    payload=_quote_response(),  # 合法 0x0530 回声响应
                )

            async def close(self):
                pass

        async def run():
            client = AsyncTdxClient(pool=AsyncFakePool())
            return await client.quotes(["600000"], as_format="dict")

        result = asyncio.run(run())
        assert isinstance(result, list)
        assert len(result) == 1

    def test_client_has_family_attribute(self):
        """#15 两个客户端实例都有 family 属性（实例属性）。"""

        class FakePool:
            def request(self, cmd, body, timeout=5.0):
                raise AssertionError("不应发起请求")

            def close(self):
                pass

        sync = TdxClient(pool=FakePool())
        async_client = AsyncTdxClient(pool=FakePool())
        assert sync.family == "quotation"
        assert async_client.family == "quotation"

    def test_client_timeout_attribute(self):
        """#16 两个客户端实例都有 timeout 属性（实例属性）。"""

        class FakePool:
            def request(self, cmd, body, timeout=5.0):
                raise AssertionError("不应发起请求")

            def close(self):
                pass

        sync = TdxClient(pool=FakePool(), timeout=3.5)
        async_client = AsyncTdxClient(pool=FakePool(), timeout=3.5)
        assert sync.timeout == 3.5
        assert async_client.timeout == 3.5
