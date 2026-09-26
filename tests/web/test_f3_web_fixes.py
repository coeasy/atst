"""F3「Web 源」修复批次回归测试（W1/W3/W4/W5/W6/W7/W8/W9/W10/W14/P2）。

全部离线：罐头响应 + 假 HTTP 客户端，不发起真实网络请求。
覆盖计划文档 docs/archive/plans/INDUSTRIAL_OPTIMIZATION_PLAN.md F3 批次各项修复，
含「四板斧」防御用例（缺字段 / None / 短行 / 非 JSON）与 §7 双栈冒烟矩阵
（urllib / httpx 同罐头序列回归）。
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import (
    SourceDeprecated,
    WebRateLimited,
    WebSourceError,
)
from tstdx.web import WebQuoteClient, get_quotes, get_rates
from tstdx.web.base import (
    DEFAULT_ACQUIRE_TIMEOUT,
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES_CAP,
    HttpResponse,
    HttpxClient,
    RateLimiter,
    TokenBucket,
    UrllibClient,
    _EastmoneyJson,
    reset_shared_buckets,
    shared_bucket,
)
from tstdx.web.boards import EastmoneyBoardSource
from tstdx.web.boc.adapters import BocSource
from tstdx.web.corporate import (
    VALID_REPORTS,
    EastmoneyDataCenterSource,
    EastmoneyForecastSource,
    EastmoneyProfileSource,
    EastmoneyShareholderSource,
)
from tstdx.web.eastmoney.adapters import EastmoneyHistoryKlineSource, EastmoneySource
from tstdx.web.fundflow import (
    EastmoneyFundFlowSource,
    EastmoneyRankSource,
    _symbol_from_market,
)
from tstdx.web.hot_rank import EastmoneyHotRankSource
from tstdx.web.longhu import parse_lhb_row
from tstdx.web.market_stats import aggregate_breadth, aggregate_limit_pool
from tstdx.web.session import WebQuoteSession
from tstdx.web.sina.adapters import SinaHistoryKlineSource, SinaSource
from tstdx.web.tencent.adapters import KlineSource, MinuteKlineSource, MinuteSource, TencentSource
from tstdx.web.ticks import MAX_TICK_PAGES, EastmoneyTrendsSource, TencentTickSource
from tstdx.web.wencai import WencaiSource

pytestmark = pytest.mark.unit

try:  # §7 双栈冒烟：httpx 缺席时仅 httpx 侧参数 skip，urllib 侧照常跑
    import httpx as _httpx  # noqa: F401

    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    _HAS_HTTPX = False


# --------------------------------------------------------------------------- #
# 假 HTTP 客户端
# --------------------------------------------------------------------------- #
class FakeHttp:
    """始终返回固定响应并记录 URL。"""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


class FlakyClient:
    """第 N 次请求前抛传输异常，之后返回成功响应。"""

    def __init__(self, exc: Exception, ok: HttpResponse, fail_times: int = 1):
        self.exc = exc
        self.ok = ok
        self.fail_times = fail_times
        self.calls = 0

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return self.ok

    def close(self) -> None:
        pass


#: 新浪单只标的合法响应（33 字段，gbk）
SINA_OK = (
    'var hq_str_sh600519="贵州茅台,1292.10,1286.00,1292.10,1305.00,1286.00,'
    "1292.00,1291.00,1520800,7946130000,640000,1300.00,480000,1299.00,"
    "320000,1298.00,210000,1297.00,129556,1296.00,150000,1295.00,1294.00,"
    "1294.00,1293.00,1293.00,1292.00,1291.00,1290.00,1290.00,20260831,"
    '150000,00";\n'
).encode("gbk")

#: 东财单只标的合法响应（utf-8，中文名）
EM_OK = json.dumps(
    {
        "data": {
            "f43": 129956,
            "f44": 130500,
            "f45": 128600,
            "f46": 129210,
            "f47": 15208,
            "f48": 7946130000.0,
            "f57": "600519",
            "f58": "贵州茅台",
            "f60": 128600,
        }
    }
).encode("utf-8")

#: datacenter-web 成功响应壳
DC_OK = json.dumps(
    {
        "version": "x",
        "success": True,
        "message": "ok",
        "code": 0,
        "result": {"pages": 1, "data": [{"SECURITY_CODE": "600519"}]},
    }
).encode("utf-8")


@pytest.fixture()
def clean_shared_buckets():
    """W5 用：隔离进程级限流桶注册表，测试后还原。"""
    reset_shared_buckets()
    yield
    reset_shared_buckets()


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """把 base.time.sleep 换成记录器（避免退避等待），返回记录列表。

    只记录**主线程**的 sleep：全量测试进程中其它用例启动的 TDX 连接池
    心跳 daemon 线程（``transport/pool._start_heartbeat`` 的
    ``time.sleep(interval)``）在 sleep 被全局替换为无阻塞后高速空转，
    会向记录器写入大量 30s 假条目，使 ``test_backoff_capped_at_8s``
    偶发失败。按线程过滤后后台线程的 sleep 不进入记录列表。
    """
    recorded: list[float] = []
    main_thread = threading.main_thread()

    def _rec(s: float) -> None:
        if threading.current_thread() is main_thread:
            recorded.append(s)

    monkeypatch.setattr(time, "sleep", _rec)
    return recorded


# --------------------------------------------------------------------------- #
# W1：排序方向参数 po
# --------------------------------------------------------------------------- #
class TestW1PoDirection:
    def test_po_ascending_false_is_1(self):
        """默认（降序）po=1——与库内 boards po=1 + 「按涨跌幅降序」一致。"""
        src = EastmoneyRankSource()
        url = src._path(market="all_a", sort="change_pct", limit=10, page=1, ascending=False)
        assert "po=1" in url

    def test_po_ascending_true_is_0(self):
        """升序 po=0（旧实现两分支恒 1，方向参数完全失效）。"""
        src = EastmoneyRankSource()
        url = src._path(market="all_a", sort="change_pct", limit=10, page=1, ascending=True)
        assert "po=0" in url

    def test_fetch_rows_both_directions(self):
        """两向锁定：ascending 参数真实落到请求 URL。"""
        src_asc = EastmoneyRankSource(max_retries=0)
        src_asc.client = FakeHttp(b"{}")
        src_asc.rate_limiter = RateLimiter()
        src_asc.fetch_rows("all_a", ascending=True)
        assert "po=0" in src_asc.client.calls[0]

        src_desc = EastmoneyRankSource(max_retries=0)
        src_desc.client = FakeHttp(b"{}")
        src_desc.rate_limiter = RateLimiter()
        src_desc.fetch_rows("all_a", ascending=False)
        assert "po=1" in src_desc.client.calls[0]


# --------------------------------------------------------------------------- #
# W3：网络层异常纳入重试
# --------------------------------------------------------------------------- #
class TestW3TransportRetry:
    def test_fetch_retries_on_web_source_error(self, monkeypatch):
        """断连（WebSourceError）重试后成功。"""
        _no_sleep(monkeypatch)
        src = SinaSource(max_retries=2)
        src.client = FlakyClient(
            WebSourceError("网络错误: 断连", context={"url": "x"}),
            HttpResponse(200, SINA_OK, {}),
        )
        src.rate_limiter = RateLimiter()
        quotes = src.fetch(["sh600519"])
        assert len(quotes) == 1
        assert src.client.calls == 2

    def test_request_text_retries_on_read_timeout(self, monkeypatch):
        """超时（ReadTimeout）在 _request_text 中同样重试。"""
        from tstdx.errors import ReadTimeout

        _no_sleep(monkeypatch)
        src = SinaSource(max_retries=2)
        src.client = FlakyClient(
            ReadTimeout("请求超时", context={"url": "x"}),
            HttpResponse(200, "ok-text".encode("gbk"), {}),
        )
        src.rate_limiter = RateLimiter()
        assert src._request_text("https://x") == "ok-text"
        assert src.client.calls == 2


# --------------------------------------------------------------------------- #
# W4：钳制 / 退避封顶 / 状态码集合
# --------------------------------------------------------------------------- #
class TestW4RetryPolicy:
    @pytest.mark.parametrize("status", [400, 404])
    def test_deterministic_status_no_retry(self, monkeypatch, status):
        """404/400 确定性失败直接抛出，不再重试。"""
        _no_sleep(monkeypatch)
        src = SinaSource(max_retries=3)
        src.client = FakeHttp(b"<html>404</html>", status=status)
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError):
            src.fetch(["sh600519"])
        assert len(src.client.calls) == 1

    def test_max_retries_clamped(self, monkeypatch):
        """负值 → 0（单次尝试，不再空 range + 裸断言）；超大值 → 5。"""
        assert SinaSource(max_retries=-3).max_retries == 0
        assert SinaSource(max_retries=99).max_retries == MAX_RETRIES_CAP == 5
        _no_sleep(monkeypatch)
        src = SinaSource(max_retries=-3)
        src.client = FakeHttp(b"boom", status=500)
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError):
            src.fetch(["sh600519"])
        assert len(src.client.calls) == 1  # 恰好一次尝试

    def test_backoff_capped_at_8s(self, monkeypatch):
        """指数退避封顶 8s；5xx 重试 MAX_RETRIES_CAP 次。

        ``sleep`` 被换成即时记录器后，令牌桶等待循环也会空转计数，
        故只统计退避序列（>0.1s）——令牌桶粒度为 ``1/rate*0.2``（rate=5 → 0.04）。
        """
        sleeps = _no_sleep(monkeypatch)
        src = SinaSource(max_retries=5)
        src.client = FakeHttp(b"boom", status=503)
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError):
            src.fetch(["sh600519"])
        assert len(src.client.calls) == 6
        backoff = [d for d in sleeps if d > 0.1]
        assert len(backoff) == 5
        assert all(d <= MAX_BACKOFF_SECONDS for d in backoff)
        assert all(d <= MAX_BACKOFF_SECONDS for d in sleeps)  # 全体（含限流等待）封顶


# --------------------------------------------------------------------------- #
# W3/W4：_failures 传输 / 解析分桶计数
# --------------------------------------------------------------------------- #
class TestFailureBuckets:
    def test_parse_failures_in_own_bucket(self, monkeypatch):
        """解析异常只进解析桶，不污染传输桶；成功后双桶清零。"""
        _no_sleep(monkeypatch)

        def _boom(text, symbols, **kwargs):
            raise ValueError("上游结构漂移")

        src = SinaSource(max_retries=0)
        src.client = FakeHttp(SINA_OK)
        src.rate_limiter = RateLimiter()
        monkeypatch.setattr(src, "parse", _boom)
        with pytest.raises(WebSourceError, match="解析失败"):
            src.fetch(["sh600519"])
        assert src._parse_failures == 1
        assert src._failures == 0

        monkeypatch.undo()  # 恢复真实 parse → 成功
        quotes = src.fetch(["sh600519"])
        assert len(quotes) == 1
        assert src._parse_failures == 0
        assert src._failures == 0

    def test_both_buckets_trigger_deprecation(self):
        """任一桶达阈值 → SourceDeprecated（携带桶类型）。"""
        src = SinaSource(max_retries=0)
        src._failures = src.DEPRECATE_AFTER_FAILURES
        with pytest.raises(SourceDeprecated, match="传输"):
            src.fetch(["sh600519"])

        src2 = SinaSource(max_retries=0)
        src2._parse_failures = src2.DEPRECATE_AFTER_PARSE_FAILURES
        with pytest.raises(SourceDeprecated, match="解析"):
            src2.fetch(["sh600519"])

    def test_get_json_counts_into_buckets(self):
        """_EastmoneyJson 单一实现把 failover 失败计入双桶（P2 绕开修复）。"""

        class _Probe(_EastmoneyJson):
            @property
            def source_name(self):
                return "eastmoney"

        src = _Probe(max_retries=0)
        src.client = FakeHttp(b"boom", status=500)
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError):
            src._get_json("/api/x")
        assert src._failures == len(src.HOSTS)
        assert src._parse_failures == 0

        src2 = _Probe(max_retries=0)
        src2.client = FakeHttp(b"not-json")
        src2.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src2._get_json("/api/x")
        assert src2._parse_failures == len(src2.HOSTS)
        assert src2._failures == 0

        src3 = _Probe(max_retries=0)
        src3.client = FakeHttp(b"{}")
        src3.rate_limiter = RateLimiter()
        src3._parse_failures = src3.DEPRECATE_AFTER_PARSE_FAILURES
        with pytest.raises(SourceDeprecated):
            src3._get_json("/api/x")
        assert src3.client.calls == []  # 下线检测前置，未发请求


# --------------------------------------------------------------------------- #
# W5：进程级限流桶
# --------------------------------------------------------------------------- #
class TestW5SharedBuckets:
    def test_shared_bucket_identity(self, clean_shared_buckets):
        """同 source_name 同桶；不同源不同桶；不同速率显式更新（M12）。"""
        b1 = shared_bucket("probe-a", 5)
        b2 = shared_bucket("probe-a", 9)  # 同桶，且速率显式更新（不再首建者胜）
        b3 = shared_bucket("probe-b", 5)
        assert b1 is b2
        assert b1 is not b3
        assert b1.rate == 9.0  # 后到配置生效（set_rate 线程安全）

    def test_two_instances_share_bucket(self, clean_shared_buckets):
        """门面「每次调用新建源实例」时令牌桶不再满血复活。"""
        a = SinaSource(rate_limit=0.001)
        b = SinaSource(rate_limit=0.001)
        assert a.rate_limiter._buckets["sina"] is b.rate_limiter._buckets["sina"]
        # 同一个小桶被两个实例消耗
        assert a.rate_limiter.acquire("sina", block=False) is True
        assert b.rate_limiter.acquire("sina", block=False) is False

    def test_default_construction_uses_shared_bucket(self, clean_shared_buckets):
        """默认构造的源全部挂到进程级注册表。"""
        a = SinaSource()
        b = SinaSource()
        assert a.rate_limiter._buckets["sina"] is b.rate_limiter._buckets["sina"]
        assert a.rate_limiter._buckets["sina"] is shared_bucket("sina", 8)


# --------------------------------------------------------------------------- #
# C7-web：限流等待超时 + rate<=0 校验
# --------------------------------------------------------------------------- #
class TestC7RateLimiterTimeout:
    def test_blocking_acquire_raises_on_timeout(self, clean_shared_buckets):
        """block=True 等待超时 → WebRateLimited（带可重试 advice），不再死等。"""
        rl = RateLimiter(rates={"probe": 0.001})
        assert rl.acquire("probe", block=False) is True
        with pytest.raises(WebRateLimited) as ei:
            rl.acquire("probe", block=True, timeout=0.05)
        assert ei.value.advice.retryable is True

    def test_blocking_acquire_default_deadline(self):
        """缺省 timeout 使用 DEFAULT_ACQUIRE_TIMEOUT（30s）。"""
        assert DEFAULT_ACQUIRE_TIMEOUT == 30.0

    def test_nonblocking_returns_false_contract(self):
        """block=False 保持既有 bool 契约。"""
        bucket = TokenBucket(rate=0.1, capacity=1.0)
        assert bucket.acquire(block=False) is True
        assert bucket.acquire(block=False) is False

    def test_token_bucket_deadline_returns_false(self):
        """TokenBucket 层阻塞超时返回 False（不抛）。"""
        bucket = TokenBucket(rate=0.001, capacity=1.0)
        assert bucket.acquire(block=False) is True
        assert bucket.acquire(block=True, timeout=0.05) is False

    @pytest.mark.parametrize("rate", [0, -1])
    def test_non_positive_rate_rejected(self, rate):
        """rate<=0 显式 ValueError。"""
        with pytest.raises(ValueError):
            TokenBucket(rate)
        with pytest.raises(ValueError):
            RateLimiter(rates={"probe": rate})


# --------------------------------------------------------------------------- #
# W6：MinuteSource off-by-one + 降级链兜底
# --------------------------------------------------------------------------- #
class TestW6MinuteGuard:
    def test_short_row_skipped_not_indexerror(self):
        """恰好 3 列的行被跳过（旧守卫 <3 后取 parts[3] → IndexError）。"""
        src = MinuteSource()
        payload = json.dumps(
            {
                "code": 0,
                "data": {
                    "sh600519": {
                        "data": {
                            "date": "20260831",
                            "data": [
                                "0930 1690.00 12",  # 3 列：旧实现 IndexError
                                "0931 1691.00 22 112040.00",  # 4 列：正常
                            ],
                        }
                    }
                },
            }
        )
        pts = src.parse_minute(payload, "sh600519")
        assert len(pts) == 1
        assert pts[0].time == "20260831 09:31"

    def test_degradation_chain_survives_non_tdx_error(self, caplog):
        """降级链捕获非 TdxError（AttributeError 等），分类记日志后继续。"""

        class _Boom:
            def fetch(self, symbols, **kwargs):  # noqa: ARG002
                raise AttributeError("上游结构漂移")

            def close(self) -> None:
                pass

        class _Ok:
            def fetch(self, symbols, **kwargs):  # noqa: ARG002
                return [
                    Quote(
                        code=s,
                        price=1.0,
                        last_close=1.0,
                        open=1.0,
                        high=1.0,
                        low=1.0,
                        volume=0,
                        amount=0.0,
                    )
                    for s in symbols
                ]

            def close(self) -> None:
                pass

        client = WebQuoteClient(sources=["sina", "tencent"])
        client._instances = {"sina": _Boom(), "tencent": _Ok()}
        with caplog.at_level("WARNING", logger="tstdx.web"):
            quotes = client.quotes(["sh600519"])
        assert len(quotes) == 1
        assert [n for n, _ in client.errors] == ["sina"]
        assert any("AttributeError" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# W7：longhu @staticmethod
# --------------------------------------------------------------------------- #
class TestW7LonghuStatic:
    def test_longhu_is_staticmethod(self):
        assert isinstance(inspect.getattr_static(WebQuoteSession, "longhu"), staticmethod)

    def test_longhu_instance_call_binds_args_correctly(self, monkeypatch):
        """实例调用时 session 不再被绑进 date 参数（W7 原始缺陷）。"""

        class _Fake:
            def __init__(self, **kwargs):  # noqa: ARG002
                pass

            def fetch_lhb(self, date=None, *, symbol=None, page=1, size=50):  # noqa: ARG002
                return [{"date": date, "size": size}]

            def close(self) -> None:
                pass

        monkeypatch.setattr("tstdx.web.longhu.EastmoneyTopListSource", _Fake)
        rows = WebQuoteSession().longhu(date="2026-09-01", size=7)
        assert rows == [{"date": "2026-09-01", "size": 7}]


# --------------------------------------------------------------------------- #
# W8：hot_rank null / 双形态守卫
# --------------------------------------------------------------------------- #
class TestW8HotRankGuards:
    def test_null_and_string_fields(self):
        """rk/rc/hisRc 为 null（None）时兜底 0；字符串数字正常转换。"""
        src = EastmoneyHotRankSource()
        rows = src.parse_rank(
            json.dumps(
                {
                    "status": 0,
                    "data": [
                        {"sc": "SH600127", "rk": None, "rc": None, "hisRc": None},
                        {"sc": "SZ000001", "rk": "2", "rc": "5", "hisRc": "-1"},
                    ],
                }
            )
        )
        assert rows[0]["rank"] == 0
        assert rows[0]["rank_change"] == 0
        assert rows[0]["his_rank_change"] == 0
        assert rows[1]["rank"] == 2
        assert rows[1]["rank_change"] == 5
        assert rows[1]["his_rank_change"] == -1

    def test_hisrc_array_form(self):
        """hisRc 偶发数组形态（[n]）取首元素。"""
        src = EastmoneyHotRankSource()
        rows = src.parse_rank(json.dumps({"status": 0, "data": [{"sc": "SH600127", "hisRc": [3]}]}))
        assert rows[0]["his_rank_change"] == 3


# --------------------------------------------------------------------------- #
# W9：北交所市场归属
# --------------------------------------------------------------------------- #
class TestW9BseSymbol:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("920222", "bj920222"),
            ("430047", "bj430047"),
            ("830799", "bj830799"),
            ("000001", "sz000001"),
            ("300750", "sz300750"),
        ],
    )
    def test_symbol_from_market_bj_segment(self, code, expected):
        """f13=0 时按 domain.symbol BJ 段（4/8/92 开头）判 bj，其余 sz。"""
        assert _symbol_from_market(code, 0) == expected

    def test_symbol_from_market_other_marks(self):
        assert _symbol_from_market("600519", 1) == "sh600519"
        assert _symbol_from_market("BK0433", 90) == "BK0433"

    def test_fetch_rows_bse_market(self):
        """fetch_rows(market='bse') 结果不再错误标 sz。"""
        payload = json.dumps(
            {
                "data": {
                    "total": 2,
                    "diff": [
                        {"f12": "920222", "f13": 0, "f14": "益坤电气", "f2": 29.0, "f3": 18.17},
                        {"f12": "000001", "f13": 0, "f14": "平安银行", "f2": 10.5, "f3": 0.5},
                    ],
                }
            }
        ).encode("utf-8")
        src = EastmoneyRankSource(max_retries=0)
        src.client = FakeHttp(payload)
        src.rate_limiter = RateLimiter()
        rows = src.fetch_rows("bse")
        symbols = {r["code"]: r["symbol"] for r in rows}
        assert symbols["920222"] == "bj920222"
        assert symbols["000001"] == "sz000001"


# --------------------------------------------------------------------------- #
# W10：按源声明编码
# --------------------------------------------------------------------------- #
class TestW10Encoding:
    def test_source_encoding_declarations(self):
        """东财/JSON 系声明 utf-8，新浪/腾讯系保持 gbk。"""
        assert EastmoneySource.encoding == "utf-8"
        assert EastmoneyRankSource.encoding == "utf-8"
        assert EastmoneyBoardSource.encoding == "utf-8"
        assert EastmoneyHistoryKlineSource.encoding == "utf-8"
        assert EastmoneyTrendsSource.encoding == "utf-8"
        assert KlineSource.encoding == "utf-8"
        assert MinuteSource.encoding == "utf-8"
        assert SinaSource.encoding == "gbk"
        assert TencentSource.encoding == "gbk"

    def test_fetch_decodes_utf8_json(self):
        """东财 fetch() 经 utf-8 解码，中文名不再 mojibake。"""
        src = EastmoneySource(max_retries=0)
        src.client = FakeHttp(EM_OK)
        src.rate_limiter = RateLimiter()
        quotes = src.fetch(["sh600519"])
        assert quotes[0].extra["name"] == "贵州茅台"

    def test_fetch_decodes_gbk_text(self):
        """新浪 fetch() 经 gbk 解码（基类默认）。"""
        src = SinaSource(max_retries=0)
        src.client = FakeHttp(SINA_OK)
        src.rate_limiter = RateLimiter()
        quotes = src.fetch(["sh600519"])
        assert quotes[0].extra["name"] == "贵州茅台"


# --------------------------------------------------------------------------- #
# W14：逐源防御批
# --------------------------------------------------------------------------- #
class TestW14Defenses:
    def test_em_board_members_tolerates_bad_rows(self):
        """boards：diff 含 None/字符串行被跳过；缺字段 / '-' 按空处理。"""
        payload = json.dumps(
            {
                "data": {
                    "diff": [
                        None,
                        "garbage",
                        {"f12": "600519", "f14": "贵州茅台", "f2": "-", "f3": "-"},
                    ]
                }
            }
        ).encode("utf-8")
        src = EastmoneyBoardSource(max_retries=0)
        src.client = FakeHttp(payload)
        src.rate_limiter = RateLimiter()
        members = src.fetch_members("BK0475")
        assert members == [{"code": "600519", "name": "贵州茅台", "price": 0.0, "pct_change": 0.0}]

    def test_em_board_boards_missing_fields(self):
        """boards：行缺 f12/f14 → 空串兜底，不崩。"""
        payload = json.dumps({"data": {"diff": [{"f3": 1.5}]}}).encode("utf-8")
        src = EastmoneyBoardSource(max_retries=0)
        src.client = FakeHttp(payload)
        src.rate_limiter = RateLimiter()
        assert src.fetch_boards("concept") == [{"code": "", "name": "", "pct_change": 1.5}]

    def test_breadth_missing_and_none_pct(self):
        """market_stats：缺 change_pct / None / 非数字 → 平盘计数，不崩。"""
        breadth = aggregate_breadth(
            [{"change_pct": 1.0}, {}, {"change_pct": None}, {"change_pct": "-"}]
        )
        assert breadth.up == 1
        assert breadth.down == 0
        assert breadth.flat == 3
        assert breadth.total == 4

    def test_limit_pool_none_days(self):
        """market_stats：limit_up_days None / 缺失 → 至少 1 板。"""
        ladder = aggregate_limit_pool([{"limit_up_days": None}, {}, {"limit_up_days": "3"}])
        assert ladder.highest_board == 3
        assert ladder.board_distribution[1] == 2

    def test_lhb_row_all_none(self):
        """longhu：全 None 行不崩（super_net/big_net None 减法claim 复核为已防护）。"""
        row = parse_lhb_row({})
        assert row["code"] == ""
        assert row["super_net"] == 0.0
        assert row["big_net"] == 0.0
        assert row["prime_net"] == 0.0
        assert row["reason"] == ""

    def test_forecast_row_missing_fields(self, monkeypatch):
        """corporate：缺字段行 → _s/_f 兜底空串 / 0.0。"""
        src = EastmoneyForecastSource()
        monkeypatch.setattr(src, "fetch_rows", lambda **kw: [{}])
        rows = src.fetch_forecast()
        assert rows[0]["code"] == ""
        assert rows[0]["profit_lower"] == 0.0
        assert rows[0]["forecast_state"] == ""

    def test_holder_num_report_via_parameter(self):
        """corporate：holder_num 报表经参数传递，self.report 不被临时替换。"""
        src = EastmoneyShareholderSource(max_retries=0)
        src.client = FakeHttp(DC_OK)
        src.rate_limiter = RateLimiter()
        src.fetch_holder_num("sh600519")
        assert "RPT_HOLDERNUMLATEST" in src.client.calls[0]
        assert src.report == VALID_REPORTS["free_holders"]

    def test_free_holders_bj_secucode(self):
        """corporate：北交所 SECUCODE 后缀 .BJ。"""
        src = EastmoneyShareholderSource(max_retries=0)
        src.client = FakeHttp(DC_OK)
        src.rate_limiter = RateLimiter()
        src.fetch_free_holders("bj830799")
        url = urllib.parse.unquote(src.client.calls[0])
        assert 'SECUCODE="830799.BJ"' in url

    def test_history_unknown_period_raises(self):
        """history：未知周期显式 ValueError（新浪/东财双侧）。"""
        sina = SinaHistoryKlineSource(max_retries=0)
        sina.rate_limiter = RateLimiter()
        with pytest.raises(ValueError, match="周期"):
            sina.fetch_bars("sh600519", period="quarterly")
        em = EastmoneyHistoryKlineSource(max_retries=0)
        em.rate_limiter = RateLimiter()
        with pytest.raises(ValueError, match="周期"):
            em.fetch_bars("sh600519", period="month")

    def test_history_unknown_adjust_raises(self):
        """history：未知复权显式 ValueError（不再静默 qfq）。"""
        em = EastmoneyHistoryKlineSource(max_retries=0)
        em.rate_limiter = RateLimiter()
        with pytest.raises(ValueError, match="复权"):
            em.fetch_bars("sh600519", adjust="ofq")

    def test_history_nan_inf_filtered(self):
        """history：NaN/Infinity 价格按 0 处理（float32 溢出防御）。"""
        text = json.dumps(
            {
                "data": {
                    "klines": [
                        "2026-08-31,NaN,1299.52,1305.00,-Infinity,23248,3003033720.00",
                        "2026-08-30,10.0,11.0,12.0,9.0,100,1000.0",
                    ]
                }
            }
        )
        bars = EastmoneyHistoryKlineSource().parse_bars(text, "sh600519")
        assert bars[0].open == 0.0
        assert bars[0].low == 0.0
        assert bars[0].close == 1299.52
        assert bars[1].close == 11.0

    def test_wencai_non_list_row_skipped(self):
        """wencai：rows 元素非 list（dict / 字符串）时守卫跳过。"""
        src = WencaiSource(cookie="token")
        payload = json.dumps(
            {
                "success": True,
                "data": {
                    "result": {
                        "title": ["代码"],
                        "result": [["600519"], {"bad": 1}, "oops"],
                    }
                },
            }
        )
        rows = src.parse_strategy(payload)
        assert len(rows) == 1
        assert rows[0]["代码"] == "600519"

    def test_flow_history_short_lines_skipped(self):
        """fundflow：历史资金流短行（<6 列）跳过，不崩。"""
        payload = json.dumps(
            {
                "data": {
                    "klines": [
                        "2026-09-01,1.0,2.0",
                        "2026-09-02,2.0,3.0,4.0,5.0,6.0",
                    ]
                }
            }
        )
        rows = EastmoneyFundFlowSource().parse_history(payload)
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-09-02"
        assert rows[0]["main_net"] == 2.0


# --------------------------------------------------------------------------- #
# P2：翻页硬上限
# --------------------------------------------------------------------------- #
class TestP2PaginationCaps:
    def test_fetch_all_default_cap_100(self):
        """fetch_all 未给 max_pages → 100 页硬上限（不再依赖服务端空页）。"""

        class _Endless:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, url, **kwargs):  # noqa: ARG002
                self.calls += 1
                return HttpResponse(200, json.dumps([{"symbol": "sh600000"}]).encode("gbk"), {})

            def close(self) -> None:
                pass

        src = SinaSource(max_retries=0)
        src.client = _Endless()
        src.rate_limiter = RateLimiter(rates={"sina": 1_000_000})
        quotes = src.fetch_all(page_size=1)
        assert src.client.calls == 100
        assert len(quotes) == 100

    def test_fetch_all_non_list_rows_raises(self):
        """fetch_all rows 非 list（错误体）→ SourceDeprecated，不再 AttributeError。"""
        src = SinaSource(max_retries=0)
        src.client = FakeHttp(json.dumps({"result": {"status": 2}}).encode("gbk"))
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated, match="结构异常"):
            src.fetch_all(page_size=10)

    def test_fetch_ticks_endless_capped(self):
        """fetch_ticks 无限翻页（max_pages<=0）受 100 页硬上限保护。"""
        body = (
            'v_detail_data_sh600519=[0,"'
            + "|".join(f"{i}/09:30:0{i % 10}/10.00/0.01/1/10.0/S" for i in range(70))
            + '"]'
        ).encode("gbk")
        src = TencentTickSource()
        src.client = FakeHttp(body)
        src.rate_limiter = RateLimiter(rates={"ticks": 1_000_000})
        ticks = src.fetch_ticks("sh600519", max_pages=0)
        assert len(src.client.calls) == MAX_TICK_PAGES == 100
        assert len(ticks) == 70 * 100


# --------------------------------------------------------------------------- #
# P1 #14：资源释放
# --------------------------------------------------------------------------- #
class TestResourceRelease:
    def test_get_quotes_closes_single_source(self, monkeypatch):
        """get_quotes(source=...) 单源路径 finally close。"""
        closed: list[bool] = []

        class _Fake:
            def fetch(self, symbols, **kwargs):  # noqa: ARG002
                return []

            def close(self) -> None:
                closed.append(True)

        monkeypatch.setattr("tstdx.web.create_source", lambda name, **kw: _Fake())
        get_quotes(["600519"], source="sina")
        assert closed == [True]

    def test_get_rates_closes_source(self, monkeypatch):
        """get_rates() finally close。"""
        closed: list[bool] = []

        class _FakeBoc:
            def fetch_rates(self):
                return [{"currency": "USD"}]

            def close(self) -> None:
                closed.append(True)

        monkeypatch.setattr("tstdx.web.BocSource", _FakeBoc)
        assert get_rates()[0]["currency"] == "USD"
        assert closed == [True]


# --------------------------------------------------------------------------- #
# 结构性：_get_json 单一实现 + 周期显式报错
# --------------------------------------------------------------------------- #
class TestStructural:
    def test_get_json_single_implementation(self):
        """5 份 failover 拷贝上收：各 Eastmoney 源共用基类实现。"""
        assert EastmoneyBoardSource._get_json is _EastmoneyJson._get_json
        assert EastmoneyDataCenterSource._get_json is _EastmoneyJson._get_json
        assert EastmoneyProfileSource._get_json is _EastmoneyJson._get_json
        assert EastmoneyRankSource._get_json is _EastmoneyJson._get_json
        assert EastmoneyFundFlowSource._get_json is _EastmoneyJson._get_json
        assert EastmoneyTrendsSource._get_json is _EastmoneyJson._get_json

    def test_datacenter_hosts_override(self):
        """datacenter 报表族覆盖为主机池（datacenter-web 优先）。"""
        assert EastmoneyDataCenterSource.HOSTS[0] == "https://datacenter-web.eastmoney.com"
        assert EastmoneyDataCenterSource.JSON_LABEL == "datacenter-web"
        assert EastmoneyProfileSource.JSON_LABEL == "F10"

    def test_facade_klines_unknown_period_raises(self):
        """门面 klines 未知周期显式报错（不再静默 day）。"""
        sess = WebQuoteSession("sina")
        with pytest.raises(ValueError, match="周期"):
            sess.klines("sh600519", period="quarterly")

    def test_kline_source_unknown_period_raises(self):
        with pytest.raises(ValueError, match="周期"):
            KlineSource().build_url(["sh600519"], period="quarterly")

    def test_minute_kline_unknown_period_raises(self):
        with pytest.raises(ValueError, match="周期"):
            MinuteKlineSource().build_url(["sh600519"], period="m2")

    def test_em_history_1min_supported(self):
        """1min 周期有确定性映射（klt=1），不再静默落到日线。"""
        assert EastmoneyHistoryKlineSource.KLTS["1min"] == 1

    def test_boc_encoding(self):
        """中行牌价页 UTF-8 声明（W10 覆盖面冒烟）。"""
        assert BocSource.encoding == "utf-8"


# --------------------------------------------------------------------------- #
# §7 双栈冒烟矩阵：urllib / httpx 同罐头序列回归（F0-2 CI 承诺）
# --------------------------------------------------------------------------- #
#: 人气榜罐头（POST 形态）
HR_OK = json.dumps(
    {
        "status": 0,
        "data": [
            {"sc": "SH600127", "rk": 1, "rc": 0, "hisRc": 0},
            {"sc": "SZ000001", "rk": 2, "rc": 3, "hisRc": -1},
        ],
    }
).encode("utf-8")

_EXPECTED_RANK_ROWS = [
    {
        "rank": 1,
        "symbol": "sh600127",
        "code": "600127",
        "market": "sh",
        "rank_change": 0,
        "his_rank_change": 0,
    },
    {
        "rank": 2,
        "symbol": "sz000001",
        "code": "000001",
        "market": "sz",
        "rank_change": 3,
        "his_rank_change": -1,
    },
]


class _FakeUrllibResponse:
    """``urlopen`` 上下文管理器形态的假响应。"""

    def __init__(self, body: bytes):
        self.body = body
        self.status = 200
        self.headers = {"Content-Encoding": ""}

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _FakeUrllibResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeHttpxResponse:
    def __init__(self, body: bytes):
        self.status_code = 200
        self.content = body
        self.headers = {"content-type": "application/json"}


class _ScriptedUrlopen:
    """按序回放响应 / 异常的假 ``urllib.request.urlopen``。"""

    def __init__(self, script: list[bytes | Exception]):
        self.script = list(script)
        self.calls: list[str] = []
        self.bodies: list[bytes | None] = []

    def __call__(self, req: urllib.request.Request, timeout: float = 5.0, **kwargs: object):
        self.calls.append(req.full_url)
        self.bodies.append(req.data)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return _FakeUrllibResponse(item)


class _ScriptedHttpxClient:
    """按序回放响应 / 异常的假 ``httpx.Client``。"""

    def __init__(self, script: list[bytes | Exception], **kwargs: object) -> None:
        self.script = list(script)
        self.calls: list[str] = []
        self.bodies: list[bytes | None] = []
        self._kwargs = kwargs

    def get(self, url: str, *, headers: object = None, timeout: object = None):
        return self._next(url, None)

    def post(
        self,
        url: str,
        *,
        content: bytes | None = None,
        headers: object = None,
        timeout: object = None,
    ):
        return self._next(url, content)

    def _next(self, url: str, body: bytes | None) -> _FakeHttpxResponse:
        self.calls.append(url)
        self.bodies.append(body)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return _FakeHttpxResponse(item)

    def close(self) -> None:
        pass


def _make_urllib_stack(monkeypatch: pytest.MonkeyPatch, script: list[bytes | Exception]):
    """替换 urllib.request.urlopen，返回真实 UrllibClient + 脚本记录器。"""
    opener = _ScriptedUrlopen(script)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    return UrllibClient(), opener


def _make_httpx_stack(monkeypatch: pytest.MonkeyPatch, script: list[bytes | Exception]):
    """替换 httpx.Client（须在 HttpxClient 构造前），返回真实 HttpxClient + 脚本记录器。"""
    pytest.importorskip("httpx")
    client_holder: list[_ScriptedHttpxClient] = []

    def _factory(**kwargs: object) -> _ScriptedHttpxClient:
        client = _ScriptedHttpxClient(script, **kwargs)
        client_holder.append(client)
        return client

    monkeypatch.setattr(_httpx, "Client", _factory)
    return HttpxClient(), client_holder[0]


class TestDualStackSmoke:
    """同一罐头响应序列经 urllib / httpx 双栈，结果与异常分诊逐字段一致。"""

    _SINA_SCRIPT = [SINA_OK]
    _HR_SCRIPT = [HR_OK]
    _URLError = urllib.error.URLError("connection reset by peer")
    _HTTPX_ERROR = RuntimeError("connection reset by peer")

    def test_fetch_quotes_parity(self, monkeypatch):
        """GET（BaseWebSource.fetch）：双栈 Quote 逐字段一致。"""
        results = []
        for make_stack in (_make_urllib_stack, _make_httpx_stack):
            client, _recorder = make_stack(monkeypatch, self._SINA_SCRIPT)
            src = SinaSource(max_retries=0)
            src.client = client
            src.rate_limiter = RateLimiter()
            results.append(src.fetch(["sh600519"]))

        q_urllib, q_httpx = results[0][0], results[1][0]
        assert q_urllib == q_httpx  # dataclass 逐字段相等
        assert q_urllib.code == "sh600519"
        assert q_urllib.price == 1292.10
        assert q_urllib.volume == 1520800
        assert q_urllib.extra["name"] == "贵州茅台"

    def test_post_hot_rank_parity(self, monkeypatch):
        """POST（人气榜形态）：双栈行列表一致，请求体同为合法 JSON。"""
        rows_by_stack = []
        for make_stack in (_make_urllib_stack, _make_httpx_stack):
            client, recorder = make_stack(monkeypatch, self._HR_SCRIPT)
            src = EastmoneyHotRankSource()
            src.client = client
            src.rate_limiter = RateLimiter()
            rows = src.fetch_hot_rank()
            rows_by_stack.append(rows)
            # 请求体一致性：两栈都发出人气榜 JSON（appId/pageNo 形态）
            body = recorder.bodies[0]
            assert body is not None
            payload = json.loads(body)
            assert payload["appId"] == "appId01"
            assert payload["pageNo"] == 1
            assert payload["pageSize"] == 100

        assert rows_by_stack[0] == rows_by_stack[1] == _EXPECTED_RANK_ROWS

    @pytest.mark.parametrize(
        "stack",
        [
            "urllib",
            pytest.param(
                "httpx",
                marks=pytest.mark.skipif(not _HAS_HTTPX, reason="httpx 未安装"),
            ),
        ],
    )
    def test_transport_error_wraps_web_source_error(self, monkeypatch, stack):
        """传输异常双栈同型：均包装为 WebSourceError（带 url 上下文与 cause）。"""
        if stack == "urllib":
            client, _ = _make_urllib_stack(monkeypatch, [self._URLError])
        else:
            client, _ = _make_httpx_stack(monkeypatch, [self._HTTPX_ERROR])
        src = SinaSource(max_retries=0)
        src.client = client
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError) as ei:
            src.fetch(["sh600519"])
        assert ei.value.context.get("url")
        assert ei.value.cause is not None

    @pytest.mark.parametrize(
        "stack",
        [
            "urllib",
            pytest.param(
                "httpx",
                marks=pytest.mark.skipif(not _HAS_HTTPX, reason="httpx 未安装"),
            ),
        ],
    )
    def test_post_transport_error_wraps_web_source_error(self, monkeypatch, stack):
        """POST 传输异常双栈同型（人气榜请求失败 → WebSourceError）。"""
        if stack == "urllib":
            client, _ = _make_urllib_stack(monkeypatch, [self._URLError])
        else:
            client, _ = _make_httpx_stack(monkeypatch, [self._HTTPX_ERROR])
        src = EastmoneyHotRankSource()
        src.client = client
        src.rate_limiter = RateLimiter()
        with pytest.raises(WebSourceError):
            src.fetch_hot_rank()
