"""Web 行情源测试（§33）：12 个用例覆盖符号归一、响应解析、降级检测、限流。

全部离线：调用 adapter 内部 parse() 方法，不发起真实 HTTP 请求。
"""

from __future__ import annotations

import json

import pytest

from tstdx.diagnostics import WarningCode, warning_sink
from tstdx.domain.models import Quote
from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.base import (
    HttpResponse,
    RateLimiter,
    TokenBucket,
    _EastmoneyJson,
    normalize_symbol,
    split_symbol,
    to_eastmoney_secid,
    to_sina_symbol,
    to_tencent_symbol,
)
from tstdx.web.eastmoney.adapters import EastmoneySource
from tstdx.web.sina.adapters import SinaSource
from tstdx.web.tencent.adapters import TencentSource

# --------------------------------------------------------------------------- #
# 罐头响应数据
# --------------------------------------------------------------------------- #
SINA_RESPONSE = (
    'var hq_str_sh600519="贵州茅台,1292.10,1286.00,1292.10,1305.00,1286.00,'
    "1292.00,1291.00,1520800,7946130000,640000,1300.00,480000,1299.00,"
    "320000,1298.00,210000,1297.00,129556,1296.00,150000,1295.00,1294.00,"
    "1294.00,1293.00,1293.00,1292.00,1291.00,1290.00,1290.00,20260831,"
    '150000,00";\n'
    'var hq_str_sz000001="平安银行,10.50,10.45,10.55,10.60,10.40,'
    "10.54,10.53,800000,84000000,200000,10.55,150000,10.54,100000,10.53,"
    "50000,10.52,30000,10.51,30000,10.56,20000,10.57,10.58,10.59,10.60,"
    '10.61,10.62,10.63,20260831,150000,00";\n'
)

TENCENT_RESPONSE = (
    'v_sh600519="1~贵州茅台~600519~1292.10~1286.00~1290.00~15208~7946.13'
    "~640~1300.00~480~1299.00~320~1298.00~210~1297.00~1295.56~1296.00~150"
    "~1295.00~1294.00~1294.00~1293.00~1293.00~1292.00~1291.00~1290.00~1290.00"
    "~1290.00~20260831150000~0.47~-0.60~-0.47~2.55~8.82~3320.00~1.10~"
    "45.21~-12.34~1295.00~1290.00~1.23~-5.67~10.56~8.90~1.10~2.30~12.50~"
    '1310.00~0.45~7946.13~1.25~3.45~1.56~-1234.56~7890.12~100.00~1.56";\n'
)

EASTMONEY_RESPONSE = json.dumps(
    {
        "data": {
            "f43": 10500,
            "f44": 10600,
            "f45": 10400,
            "f46": 10550,
            "f47": 8000,
            "f48": 84000000.0,
            "f49": 300000,
            "f50": 100,
            "f51": 11000,
            "f52": 10000,
            "f57": "000001",
            "f58": "平安银行",
            "f60": 10450,
            "f168": 0.55,
            "f169": 50,
            "f170": 48,
            "f171": 120,
            "f116": 2300000000000,
            "f117": 2300000000000,
            "f162": 890,
            "f167": 123,
        }
    }
)


@pytest.mark.unit
class TestWebSources:
    """12 个 Web 行情源用例。"""

    def test_symbol_normalization_prefixes(self):
        """#1 符号归一化：sh/sz/bj 前缀。"""
        assert split_symbol("sh600519") == ("sh", "600519")
        assert split_symbol("sz000001") == ("sz", "000001")
        assert split_symbol("bj830799") == ("bj", "830799")
        assert split_symbol("hk00700") == ("hk", "00700")

    def test_symbol_normalization_default(self):
        """#2 符号归一化：无前缀默认 sh。"""
        assert split_symbol("600519") == ("sh", "600519")
        assert normalize_symbol("600519") == "sh600519"
        assert normalize_symbol("sz000001") == "sz000001"

    def test_symbol_to_source_format(self):
        """#3 各源格式转换。"""
        assert to_sina_symbol("600519") == "sh600519"
        assert to_tencent_symbol("600519") == "sh600519"
        assert to_eastmoney_secid("sh600519") == "1.600519"
        assert to_eastmoney_secid("sz000001") == "0.000001"

    def test_sina_parse(self):
        """#4 Sina var-format 解析。"""
        src = SinaSource(max_retries=0)
        quotes = src.parse(SINA_RESPONSE, ["sh600519", "sz000001"])
        assert len(quotes) == 2
        q = quotes[0]
        assert q.code == "sh600519"
        assert isinstance(q, Quote)
        assert q.price == 1292.10
        assert q.open == 1292.10
        assert q.high == 1305.00
        assert q.low == 1286.00
        assert q.volume == 1520800  # 股（Sina 已是股）

    def test_tencent_parse(self):
        """#5 Tencent ~-format 解析。"""
        src = TencentSource(max_retries=0)
        quotes = src.parse(TENCENT_RESPONSE, ["sh600519"])
        assert len(quotes) == 1
        q = quotes[0]
        assert q.code == "sh600519"
        assert isinstance(q, Quote)
        assert q.price == 1292.10
        assert q.open == 1290.00
        # volume 从手归一化为股 (×100)
        assert q.volume == 15208 * 100

    def test_eastmoney_parse(self):
        """#6 Eastmoney JSON 解析。"""
        src = EastmoneySource(max_retries=0)
        quotes = src._parse_payload(json.loads(EASTMONEY_RESPONSE))
        assert len(quotes) == 1
        q = quotes[0]
        assert q.code == "000001"
        assert isinstance(q, Quote)
        assert q.price == 105.00  # 10500/100
        assert q.open == 105.50
        assert q.high == 106.00
        assert q.low == 104.00
        # volume 从手归一化为股 (×100)
        assert q.volume == 8000 * 100

    def test_sina_empty_response(self):
        """#7 Sina 空响应 → SourceDeprecated。"""
        src = SinaSource(max_retries=0)
        with pytest.raises(SourceDeprecated):
            src.parse('var hq_str_sh600519="";\n', ["sh600519"])

    def test_sina_insufficient_fields(self):
        """#8 Sina 字段不足 → WebSourceError。"""
        src = SinaSource(max_retries=0)
        with pytest.raises(WebSourceError):
            src.parse('var hq_str_sh600519="a,b,c";\n', ["sh600519"])

    def test_eastmoney_invalid_json(self):
        """#9 Eastmoney 非 JSON → SourceDeprecated（走主机池 failover 的解析分支）。"""
        src = EastmoneySource(max_retries=0)

        class BadJsonClient:
            def get(self, url, **kw):
                return HttpResponse(200, b"not json at all", {})

            def close(self):
                pass

        src.client = BadJsonClient()
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch(["sz000001"])

    def test_eastmoney_empty_data(self):
        """#10 Eastmoney 空 data → SourceDeprecated。"""
        src = EastmoneySource(max_retries=0)
        with pytest.raises(SourceDeprecated):
            src._parse_payload({"data": None})

    def test_deprecation_detection(self):
        """#11 连续失败达阈值 → SourceDeprecated。"""
        src = SinaSource(max_retries=0, timeout=1.0)
        src.DEPRECATE_AFTER_FAILURES = 3
        # 模拟连续失败
        src._failures = 3
        from tstdx.web.base import HttpResponse

        class FailingClient:
            def get(self, url, **kw):
                return HttpResponse(500, b"", {})

            def close(self):
                pass

        src.client = FailingClient()
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch(["sh600519"])

    def test_rate_limiter_respected(self):
        """#12 限流器：非阻塞模式无令牌时返回 False。"""
        bucket = TokenBucket(rate=0.1, capacity=1.0)
        # 第一个请求应成功（capacity=1）
        assert bucket.acquire(block=False) is True
        # 立即第二个请求应失败（速率太低）
        assert bucket.acquire(block=False) is False

        # RateLimiter 按源分组
        rl = RateLimiter(rates={"test": 0.01})
        assert rl.acquire("test", block=False) is True
        assert rl.acquire("test", block=False) is False


def _sina_market_page(page: int, rows: int) -> str:
    """构造一页 Market_Center.getHQNodeData 响应（页内 symbol 有序）。"""
    return json.dumps(
        [
            {
                "symbol": f"sh{(page - 1) * rows + i:06d}",
                "name": f"股票{(page - 1) * rows + i}",
                "trade": 10.0 + i,
                "settlement": 10.0,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "volume": 1000,
                "amount": 100000.0,
                "ticktime": "20260901 150000",
            }
            for i in range(rows)
        ]
    )


@pytest.mark.unit
class TestFetchAllConcurrency:
    """M3：fetch_all 并发分页（短页终止 + 顺序保持 + 限流控速）。"""

    def test_short_page_stops_early(self, monkeypatch):
        """第 1 页短页 → 直接返回，不发起更多请求。"""
        src = SinaSource(max_retries=0)
        calls: list[int] = []
        monkeypatch.setattr(
            src,
            "_request_text",
            lambda url, **kw: (calls.append(1), _sina_market_page(1, 3))[1],
        )
        quotes = src.fetch_all(page_size=5)
        assert len(quotes) == 3
        assert calls == [1], "短页应只请求一次"

    def test_multiple_pages_ordered_and_concurrent(self, monkeypatch):
        """多页：结果按页序合并，且触发并发（workers>1 请求数不变）。"""
        src = SinaSource(max_retries=0)
        calls: list[int] = []

        def fake_request(url, **kw):
            calls.append(1)
            # 从 URL 提取 page
            page = int(url.split("page=")[1].split("&")[0])
            return _sina_market_page(page, 4)

        monkeypatch.setattr(src, "_request_text", fake_request)
        quotes = src.fetch_all(page_size=4, max_pages=3, workers=3)
        assert len(quotes) == 4 * 3
        # 顺序保持：symbol 升序
        codes = [q.code for q in quotes]
        assert codes == sorted(codes)
        assert len(calls) == 3

    def test_short_page_in_middle_truncates(self, monkeypatch):
        """中间页变短（服务端数据到此为止）→ 之后页码不再返回。"""
        src = SinaSource(max_retries=0)

        def fake_request(url, **kw):
            page = int(url.split("page=")[1].split("&")[0])
            if page <= 2:
                return _sina_market_page(page, 4)
            return "[]"  # 第 3 页空

        monkeypatch.setattr(src, "_request_text", fake_request)
        quotes = src.fetch_all(page_size=4, max_pages=10, workers=3)
        assert len(quotes) == 4 * 2


def _rank_page(offset: int, n: int) -> str:
    """构造一页 getBoardRankList 响应（code 从 sh600000+offset 起）。"""
    rows = [{"code": f"sh{600000 + offset + i:06d}", "name": f"股票{offset + i}"} for i in range(n)]
    return json.dumps({"code": 0, "msg": "ok", "data": {"rank_list": rows}})


@pytest.mark.unit
class TestTencentFetchAll:
    """U5：腾讯全市场（getBoardRankList 枚举代码 + qt.gtimg.cn 批量行情）。"""

    @staticmethod
    def _q(code: str) -> Quote:
        return Quote(
            code=code,
            price=1.0,
            last_close=1.0,
            open=1.0,
            high=1.0,
            low=1.0,
            volume=0,
            amount=0.0,
            extra={},
        )

    @staticmethod
    def _offset_of(url: str) -> int:
        return int(url.split("offset=")[1].split("&")[0])

    def test_parse_rank_codes(self):
        """排行接口 JSON → 代码列。"""
        src = TencentSource(max_retries=0)
        assert src._parse_rank_codes(_rank_page(0, 3)) == ["sh600000", "sh600001", "sh600002"]

    def test_parse_rank_codes_not_json(self):
        """非 JSON 体 → SourceDeprecated。"""
        src = TencentSource(max_retries=0)
        with pytest.raises(SourceDeprecated):
            src._parse_rank_codes("<html>oops</html>")

    def test_parse_rank_codes_missing_rank_list(self):
        """结构缺 rank_list → SourceDeprecated。"""
        src = TencentSource(max_retries=0)
        with pytest.raises(SourceDeprecated):
            src._parse_rank_codes('{"code": 0, "msg": "ok", "data": {}}')

    def test_fetch_codes_paginates_and_dedups(self, monkeypatch):
        """offset 分页：短页终止、页码递增、重复代码去重。"""
        src = TencentSource(max_retries=0)
        total = 12
        offsets: list[int] = []

        def fake_request(url, **kw):
            off = self._offset_of(url)
            offsets.append(off)
            n = max(0, min(4, total - off))
            return _rank_page(off, n)

        monkeypatch.setattr(src, "_request_text", fake_request)
        codes = src._fetch_codes("aStock", page_size=4, max_pages=10)
        assert codes == [f"sh{600000 + i:06d}" for i in range(total)]
        assert offsets == [0, 4, 8, 12]

    def test_fetch_codes_page_size_clamped(self, monkeypatch):
        """page_size 钳制到 [1, 200]（服务端上限）。"""
        src = TencentSource(max_retries=0)
        counts: list[int] = []

        def fake_request(url, **kw):
            counts.append(int(url.split("count=")[1]))
            return _rank_page(0, 2)  # 短页终止

        monkeypatch.setattr(src, "_request_text", fake_request)
        src._fetch_codes("aStock", page_size=999)
        assert counts and counts[0] == TencentSource.RANK_PAGE_MAX
        counts.clear()
        src._fetch_codes("aStock", page_size=0)
        assert counts and counts[0] == 1

    def test_fetch_codes_stops_on_page_failure(self, monkeypatch):
        """单页瞬断 → 停止枚举，返回已收代码（同新浪 fetch_all 语义）。"""
        src = TencentSource(max_retries=0)

        def fake_request(url, **kw):
            if self._offset_of(url) == 0:
                return _rank_page(0, 4)
            raise WebSourceError("boom", context={})

        monkeypatch.setattr(src, "_request_text", fake_request)
        codes = src._fetch_codes("aStock", page_size=4, max_pages=10)
        assert codes == [f"sh{600000 + i:06d}" for i in range(4)]

    def test_fetch_codes_truncation_warns(self, monkeypatch):
        """枚举阶段某页失败即停 → 必须留下 signal，不能静默返回"部分全市场"。

        新浪缺页时发 ``WEB_SINA_PAGES_MISSING``；腾讯过去只在**批量行情**阶段发告警，
        枚举阶段的截断一声不吭（第 29 轮）。两条路径各管一个阶段，缺一即调用方拿到
        被截断的代码表却以为它是全市场。
        """
        src = TencentSource(max_retries=0)

        def fake_request(url, **kw):
            if self._offset_of(url) == 0:
                return _rank_page(0, 4)
            raise WebSourceError("boom", context={})

        monkeypatch.setattr(src, "_request_text", fake_request)
        with warning_sink() as caveats:
            codes = src._fetch_codes("aStock", page_size=4, max_pages=10)
        assert codes == [f"sh{600000 + i:06d}" for i in range(4)]
        assert [item.code for item in caveats] == [WarningCode.WEB_TENCENT_PAGES_MISSING]
        assert "被截断" in caveats[0].message

    def test_fetch_all_batches_and_orders(self, monkeypatch):
        """枚举 → 批量行情：结果按代码序、分批正确（5/5/2）。"""
        src = TencentSource(max_retries=0)
        src.TENCENT_BATCH = 5
        total = 12
        batches: list[list[str]] = []

        def fake_request(url, **kw):
            off = self._offset_of(url)
            n = max(0, min(4, total - off))
            return _rank_page(off, n)

        def fake_fetch(batch, **kw):
            batches.append(list(batch))
            return [self._q(c) for c in batch]

        monkeypatch.setattr(src, "_request_text", fake_request)
        monkeypatch.setattr(src, "fetch", fake_fetch)
        quotes = src.fetch_all(page_size=4, max_pages=10)
        assert [q.code for q in quotes] == [f"sh{600000 + i:06d}" for i in range(total)]
        assert [len(b) for b in batches] == [5, 5, 2]

    def test_fetch_all_skips_failed_batch(self, monkeypatch):
        """单批失败被跳过，其余批照常返回（不拖垮全市场）。"""
        src = TencentSource(max_retries=0)
        src.TENCENT_BATCH = 5
        total = 12

        def fake_request(url, **kw):
            off = self._offset_of(url)
            n = max(0, min(4, total - off))
            return _rank_page(off, n)

        def fake_fetch(batch, **kw):
            if any(c == "sh600005" for c in batch):
                raise WebSourceError("boom", context={})
            return [self._q(c) for c in batch]

        monkeypatch.setattr(src, "_request_text", fake_request)
        monkeypatch.setattr(src, "fetch", fake_fetch)
        quotes = src.fetch_all(page_size=4, max_pages=10)
        codes = [q.code for q in quotes]
        assert "sh600005" not in codes
        assert len(codes) == total - 5  # 第 2 批（5 只）被跳过

    def test_fetch_all_all_batches_fail(self, monkeypatch):
        """全部批失败 → SourceDeprecated。"""
        src = TencentSource(max_retries=0)
        src.TENCENT_BATCH = 5

        def fake_request(url, **kw):
            if self._offset_of(url) == 0:
                return _rank_page(0, 6)
            return _rank_page(0, 0)

        def fake_fetch(batch, **kw):
            raise WebSourceError("boom", context={})

        monkeypatch.setattr(src, "_request_text", fake_request)
        monkeypatch.setattr(src, "fetch", fake_fetch)
        with pytest.raises(SourceDeprecated):
            src.fetch_all(page_size=4, max_pages=10)

    def test_fetch_all_empty_codes(self, monkeypatch):
        """空代码表 → SourceDeprecated。"""
        src = TencentSource(max_retries=0)

        def fake_request(url, **kw):
            return _rank_page(0, 0)

        monkeypatch.setattr(src, "_request_text", fake_request)
        with pytest.raises(SourceDeprecated):
            src.fetch_all(page_size=4, max_pages=10)

    def test_fetch_all_unsupported_node(self):
        """不支持的节点 → WebSourceError（含可选值），不发起任何请求。"""
        src = TencentSource(max_retries=0)
        with pytest.raises(WebSourceError, match="hs_a"):
            src.fetch_all(node="sh_a")


@pytest.mark.unit
class TestEastmoneyHostBlacklist:
    """R3：东财主机池进程级黑名单（失败主机 TTL 内绕过，成功清除）。"""

    class _EMTestSource(_EastmoneyJson):
        JSON_LABEL = "test"
        HOSTS = ("https://h1.test", "https://h2.test", "https://h3.test")

        @property
        def source_name(self):
            return "eastmoney"

    @pytest.fixture(autouse=True)
    def _clean_blacklist(self):
        from tstdx.web.base import reset_em_blacklist

        reset_em_blacklist()
        yield
        reset_em_blacklist()

    def _make(self, fail_hosts: set[str]):
        src = self._EMTestSource(max_retries=0)
        urls: list[str] = []

        class Fake:
            def get(self, url, **kw):  # noqa: ARG002
                urls.append(url)
                host = url.split("/path")[0]
                if host in fail_hosts:
                    return HttpResponse(500, b"", {})
                return HttpResponse(200, b'{"ok": true}', {})

            def close(self):
                pass

        src.client = Fake()
        src.rate_limiter = RateLimiter()
        return src, urls

    def test_failed_host_blacklisted_next_call_skips(self):
        """首站失败 → 黑名单；下次调用直接从备站开始。"""
        src, urls = self._make(fail_hosts={"https://h1.test"})
        assert src._get_json("/path")["ok"] is True
        assert urls == ["https://h1.test/path", "https://h2.test/path"]
        # 第二次：h1 已被拉黑，直接尝试 h2
        urls.clear()
        assert src._get_json("/path")["ok"] is True
        assert urls == ["https://h2.test/path"]

    def test_ttl_expiry_restores_priority(self):
        """黑名单 TTL 到期后回到首站优先（不会永久降权）。"""
        from tstdx.web.base import _EM_HOST_BLACKLIST, _em_blacklist_add

        src, _ = self._make(fail_hosts={"https://h1.test"})
        _em_blacklist_add("https://h1.test")
        assert src._ordered_hosts() == (
            "https://h2.test",
            "https://h3.test",
        )
        # 模拟 TTL 到期：把过期时间拨到过去 → 恢复完整池
        import time as _t

        _EM_HOST_BLACKLIST["https://h1.test"] = _t.time() - 1
        assert src._ordered_hosts() == (
            "https://h1.test",
            "https://h2.test",
            "https://h3.test",
        )

    def test_all_hosts_blacklisted_falls_back_to_full_pool(self):
        """全部主机被拉黑时回退完整池（避免死路）。"""
        from tstdx.web.base import _em_blacklist_add

        src, _ = self._make(fail_hosts={"https://h1.test"})
        for h in src.HOSTS:
            _em_blacklist_add(h)
        assert src._ordered_hosts() == src.HOSTS
