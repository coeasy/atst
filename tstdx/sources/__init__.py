# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""数据源路由（§34）：把「多源数据获取」收敛成一个统一入口。

为什么要路由
------------
同一个「最新价 / 日线」可能来自：TDX 主站（在线）、HTTP Web 源（腾讯/新浪）、
本地 vipdoc 离线文件、golden 缓存，甚至离线合成。不同源的单位契约、可用性、
延迟、反爬风险差异巨大。路由层负责：

* **按配置顺序降级**（配置驱动，非硬编码）；
* **统一单位契约**（元/股/元）；
* **失败隔离**：单源故障不污染整体，自动切下一源；
* **可观测**：记录每源成功/失败原因。

降级层级
--------
``tdx`` → ``web`` → ``reader`` → ``cache`` → ``synthetic``
（见 :class:`~tstdx.config.schema.SourcesConfig`）。

CSV / WebSocket 口径锚点（§3-3 尾）
-----------------------------------
本包**没有任何 CSV 或 WebSocket 读写通道**（审计复核：全模块零 csv/ws
import）。持久化产物只有 golden 样本目录（``payload.bin`` + ``meta.yaml``
/ ``meta.json``，见 :func:`_golden_kline` / :func:`_golden_quote`）。
全库 CSV 唯一写侧是 :func:`tstdx.output.to_csv`（utf-8-sig BOM 默认、
逗号分隔、DictWriter 首行键序，BOM 策略见 ``tstdx/output/__init__.py``）；
WebSocket 通道在 :mod:`tstdx.streaming`（独立域）。**在 sources 内新增
CSV/WS 导出前先对齐 sinks 口径，禁止另起炉灶**——这是本注释存在的
原因：审计发现的「口径漂移」实为文档措辞歧义，代码层面无第二套实现。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config.schema import SourcesConfig
from ..errors import AllSourcesExhausted, TdxError

__all__ = [
    "DataSourceRouter",
    "SourceUnavailable",
    "build_router",
]

logger = logging.getLogger(__name__)


class SourceUnavailable(TdxError):
    """某个数据源当前不可用（应被路由层捕获并继续降级）。"""

    code = "E3400"


def _period_to_reader_period(period: str) -> str:
    """把通用周期名映射到 reader 的 Period 常量名。"""
    from ..reader.profile import Period

    p = (period or "day").lower()
    if p in ("day", "daily", "d"):
        return Period.DAY
    if p in ("1min", "min", "m1"):
        return Period.M1
    if p in ("5min", "5m", "m5"):
        return Period.M5
    # 其它周期本地文件无对应格式，reader 源直接不可用
    raise SourceUnavailable(f"本地 reader 不支持周期 {period!r}（仅 day/1min/5min）")


def _split_for_cache(symbol: str) -> tuple[int, str]:
    """从 symbol 提取 (tdx市场编号, 6位代码)，用于 golden 样本查找。

    委托统一符号引擎 :func:`tstdx.domain.symbol.split_symbol`（单一事实源，
    审计 §2-1：不再旁路实现市场推断）。tdx 编号口径（v5 DC1）：0=深，
    1=沪，2=北交所——直接复用 ``Symbol.tdx_market``，不再旁路二值映射。
    """
    from ..domain.symbol import parse_symbol

    sym = parse_symbol(symbol)
    return sym.tdx_market, sym.code.upper()


def _golden_kline(
    golden_root: Path, code: str, category: int, count: int
) -> list[dict[str, Any]] | None:
    """从 golden 缓存回放 K 线（离线真数据，自采集样本）。"""
    from ..codec.framing import ResponseFrame
    from ..protocol.registry import dispatch

    glob = f"0x052d_security_bars_{code}_cat{category}"
    candidates = sorted(golden_root.glob(f"quotation/{glob}/20*"), reverse=True)
    if not candidates:
        return None
    meta_path = candidates[0] / "meta.json"
    payload_path = candidates[0] / "payload.bin"
    if not (meta_path.exists() and payload_path.exists()):
        return None
    import json

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = payload_path.read_bytes()
    resp = meta["response"]
    frame = ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=1,
        seq=0,
        method=0x052D,
        zip_size=resp["zip_size"],
        unzip_size=resp["unzip_size"],
        payload=payload,
    )
    result = dispatch(frame, category=category)
    rows = result.rows[-count:] if count else result.rows
    return rows or None


def _golden_quote(golden_root: Path, code: str) -> dict[str, Any] | None:
    """从 golden 缓存回放实时行情（离线真数据，0x0530 自采集样本）。"""
    from ..codec.framing import ResponseFrame
    from ..protocol.registry import dispatch

    candidates = sorted(
        golden_root.glob(f"quotation/0x0530_realtime_quote_{code}/20*"), reverse=True
    )
    if not candidates:
        return None
    meta_path = candidates[0] / "meta.json"
    payload_path = candidates[0] / "payload.bin"
    if not (meta_path.exists() and payload_path.exists()):
        return None
    import json

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = payload_path.read_bytes()
    resp = meta["response"]
    frame = ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=1,
        seq=0,
        method=0x0530,
        zip_size=resp["zip_size"],
        unzip_size=resp["unzip_size"],
        payload=payload,
    )
    ctx = dict(meta.get("parse_ctx") or {})
    # price_scale=100 为 golden 0x0530 样本的固定价格缩放口径（样本即契约：
    # 采集计划 ctx 里 price_scale=100，见 tstdx.tools.capture PLANS —— 换口径
    # 需同步采集器与此处，禁止单点硬改）。
    result = dispatch(frame, code=ctx.get("code", code), market=ctx.get("market"), price_scale=100)
    if not result.rows:
        return None
    return result.rows[0]


class DataSourceRouter:
    """统一数据源路由（配置驱动的多级降级）。

    Parameters
    ----------
    order: 降级顺序覆盖（否则取 ``config.sources.order``）。
    vipdoc_root: 本地 vipdoc 根目录（reader 源）。
    golden_root: golden 缓存根目录（cache 源，离线回放自采集样本）。
    tdx_hosts: TDX 主站列表（tdx 源）。
    kline_cache: 本地 SQLite K 线缓存（U4）。注入后自动「读命中短路 +
        写穿透增量合并」；``None`` 表示禁用。
    quote_cache: 实时行情 TTL 缓存（C5，:class:`~tstdx.cache.QuoteCache`）。
        注入后 ``quotes()`` 命中短路、未命中回写；``None`` 表示禁用。
    """

    def __init__(
        self,
        *,
        config: SourcesConfig | None = None,
        order: Sequence[str] | None = None,
        vipdoc_root: str | Path | None = None,
        golden_root: str | Path | None = None,
        tdx_hosts: Sequence[Any] | None = None,
        kline_cache: Any | None = None,
        quote_cache: Any | None = None,
    ) -> None:
        self.config = config or SourcesConfig()
        self.order = list(order) if order else list(self.config.order)
        self.vipdoc_root = (
            Path(vipdoc_root)
            if vipdoc_root
            else (Path(self.config.vipdoc_root) if self.config.vipdoc_root else None)
        )
        self.golden_root = Path(golden_root) if golden_root else None
        self.tdx_hosts = tdx_hosts
        self.kline_cache = kline_cache
        self.quote_cache = quote_cache
        self.last_errors: list[tuple[str, BaseException]] = []
        self.last_source: str | None = None

    # -- 内部：按 order + enabled 过滤 -------------------------------------- #
    def _active_sources(self) -> list[str]:
        enabled = self.config.enabled
        out = []
        for s in self.order:
            if enabled.get(s, False):
                out.append(s)
        return out

    def _safe_run(self, source: str, fn) -> Any:
        """执行单源；捕获可控异常，返回数据或抛 SourceUnavailable。"""
        try:
            return fn()
        except SourceUnavailable:
            raise
        except AllSourcesExhausted as exc:
            raise SourceUnavailable(f"{source}: {exc}") from exc
        except TdxError as exc:
            # 连接/超时类错误视为本源不可用，继续降级
            raise SourceUnavailable(f"{source}: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            if self.config.continue_on_error:
                raise SourceUnavailable(f"{source}: {type(exc).__name__}: {exc}") from exc
            raise

    # -- 公开：实时行情 ---------------------------------------------------- #
    def quotes(
        self,
        symbols: Sequence[str],
        *,
        as_format: str = "dict",
        default_empty_ok: bool = False,
        order: Sequence[str] | None = None,
    ) -> list[Any]:
        """获取实时行情，按 ``tdx`` → ``web`` 顺序降级。

        C5：注入 ``quote_cache`` 时先读 TTL 缓存（命中短路），未命中走
        降级链并回写；默认 TTL 3s 对齐盘中刷新节奏。

        ``default_empty_ok``（Q1-b 对齐 :meth:`kline`）：False（默认）时某源
        返回空列表视为不可用继续降级；True 时空列表也算该源成功，直接返回
        空结果（facade 合并后的「空=成功」口径，见 ADR-012 D2）。

        ``order``（Q1-b）：本次调用的降级顺序覆盖（如 facade 单源委托传
        ``["tdx"]``）；``None`` 用实例配置。显式传参**绕过**
        ``enabled`` 开关（调用方已明确指定源，如 facade 显式路由）。
        """
        from ..client import _emit

        symbols = list(symbols)
        # C5：读穿命中短路
        if self.quote_cache is not None:
            cached = self.quote_cache.get(symbols)
            if cached is not None:
                self.last_source = "quote_cache"
                if as_format == "dict":
                    return cached
                return _emit([_to_quote(x) for x in cached], as_format)
        data: list[Any] | None = None
        active = list(order) if order else self._active_sources()
        for source in active:
            if source not in ("tdx", "web", "cache"):
                continue
            try:
                if source == "tdx":
                    from ..client import TdxClient

                    with TdxClient(hosts=self.tdx_hosts) as c:
                        data = self._safe_run("tdx", lambda: c.quotes(symbols, as_format="dict"))
                elif source == "web":
                    from ..web import WebQuoteClient

                    client = WebQuoteClient()
                    try:
                        # WebQuoteClient.quotes 已返回 list[Quote]；直接使用，
                        # 勿再经 to_dict/重建 Quote（会把 bid/ask 退化为 dict，破坏 to_dict）
                        data = self._safe_run("web", lambda c=client: c.quotes(symbols))
                    finally:
                        client.close()
                elif source == "cache":
                    if self.golden_root:
                        # _safe_run 包裹：golden 样本损坏（payload 畸形 → struct.error
                        # 等非 TdxError 异常）不再炸穿整条降级链（审计 §2-15）
                        rows = self._safe_run(
                            "cache",
                            lambda symbols=symbols: [
                                q
                                for sym in symbols
                                for q in (
                                    _golden_quote(self.golden_root, _split_for_cache(sym)[1]),
                                )
                                if q
                            ],
                        )
                        if rows:
                            data = rows
                        else:
                            raise SourceUnavailable("cache: 无匹配 golden 行情样本")
                    else:
                        raise SourceUnavailable("cache: 未配置 golden_root")
            except SourceUnavailable as exc:
                self.last_errors.append((source, exc))
                if not self.config.continue_on_error:
                    raise
                continue
            if data or (default_empty_ok and data is not None):
                self.last_source = source
                # C5：回写 TTL 缓存（写穿透）
                if self.quote_cache is not None and data:
                    with contextlib.suppress(Exception):
                        self.quote_cache.put(symbols, [_as_dict(x) for x in data])
                if as_format == "dict":
                    return [_as_dict(x) for x in data]
                return _emit([_to_quote(x) for x in data], as_format)
            # 空列表 = 本源全部失败（如 TDX 主站不可达但 client 吞掉异常返回 []），
            # 视为不可用，继续降级到下一源（default_empty_ok=True 时上面已返回）
            self.last_errors.append((source, SourceUnavailable(f"{source}: 返回空行情数据")))
        raise AllSourcesExhausted(
            "全部行情源失败",
            context={
                "sources": active,
                "errors": [f"{s}: {e}" for s, e in self.last_errors],
            },
        )

    # -- 公开：K 线 -------------------------------------------------------- #
    def kline(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
        adjust: str = "",
        default_empty_ok: bool = False,
        order: Sequence[str] | None = None,
    ) -> list[Any]:
        """获取 K 线，按 ``tdx`` → ``web`` → ``reader`` → ``cache`` → ``synthetic`` 降级。

        Parameters
        ----------
        start:
            偏移语义（Q1-a 对齐 facade ：meth:`UnifiedQuoteAPI.bars`）：
            从最新往回跳过 ``start`` 根再取 ``count`` 根（``end = len - start``，
            ``begin = end - count``）。``start=0``（默认）行为与历史版本完全一致
            （末尾 ``count`` 根）。仅 tdx / reader 源支持；web 源无该参数，
            非零 ``start`` 传 web 时忽略（web 分支不透传）。
        adjust:
            web 源复权口径透传（Q1-b 对齐 facade ：meth:`UnifiedQuoteAPI.bars`）：
            ``""``（默认，原始价）与历史行为完全一致；显式 ``"qfq"/"hfq"``
            透传给 ``WebQuoteClient.klines``（facade web 路由合并后需要）。
            仅 web 源消费该参数；tdx/reader 仅原始价，不受影响。
        default_empty_ok:
            「空但成功」显式开关（默认 False）。False 时某源返回空列表视为
            不可用并继续降级（与 :meth:`quotes` 的 ``if data:`` 语义对齐，
            审计 §2-15）；True 时空列表也算该源成功，直接返回空结果。
        order:
            本次调用的降级顺序覆盖（Q1-b：facade 单源委托传 ``["tdx"]`` 等）；
            ``None`` 用实例配置。显式传参绕过 ``enabled`` 开关（调用方已
            明确指定源）。
        """
        from ..client import TdxClient, period_to_category
        from ..domain.symbol import split_symbol

        # 统一符号引擎归一化：兼容 sh600519 / 600519.SH / 600519（不再内联拆前缀）
        _, code = split_symbol(symbol)
        category = period_to_category(period)
        data: list[Any] | None = None

        # U4：本地 SQLite K 线缓存 —— 读命中短路（重复请求零网络）。
        if self.kline_cache is not None:
            try:
                cached = self.kline_cache.get(symbol, period, count)
                if cached is not None and len(cached) >= max(1, count):
                    self.last_source = "cache"
                    if as_format == "dict":
                        return [_as_dict(x) for x in cached]
                    return cached
            except Exception:  # noqa: BLE001 - 缓存异常不阻塞主链路
                self.kline_cache = None

        active = list(order) if order else self._active_sources()
        for source in active:
            try:
                if source == "tdx":
                    with TdxClient(hosts=self.tdx_hosts) as c:
                        data = self._safe_run(
                            "tdx",
                            lambda: c.bars(
                                symbol, period=period, count=count, start=start, as_format="dict"
                            ),
                        )
                elif source == "web":
                    from ..web import KLINE, WebQuoteClient

                    client = WebQuoteClient(sources=[KLINE])
                    try:
                        # adjust 默认 ""：降级链原始价，与 tdx 分支口径一致。
                        # （深审 W#2：此前未传 adjust，落入 WebQuoteClient.klines
                        # 默认 qfq —— tdx 抖动降级时同一次调用的价格口径从
                        # 「不复权」静默切为「前复权」，除权日附近价差可达数十元。
                        # Q1-b 起支持显式透传，供 facade web 路由合并使用）
                        data = self._safe_run(
                            "web",
                            lambda c=client: c.klines(
                                symbol, period=period, count=count, adjust=adjust
                            ),
                        )
                    finally:
                        client.close()
                elif source == "reader":
                    data = self._safe_run(
                        "reader", lambda: self._reader_kline(symbol, period, count, start=start)
                    )
                elif source == "cache":
                    if self.golden_root:
                        # _safe_run 包裹：golden 样本损坏（payload 畸形 → struct.error
                        # 等非 TdxError 异常）不再炸穿整条降级链（审计 §2-15）
                        rows = self._safe_run(
                            "cache",
                            lambda code=code, category=category: _golden_kline(
                                self.golden_root, code, category, count
                            ),
                        )
                        if rows:
                            data = rows
                        else:
                            raise SourceUnavailable("cache: 无匹配 golden 样本")
                    else:
                        raise SourceUnavailable("cache: 未配置 golden_root")
                elif source == "synthetic":
                    data = self._synthetic_kline(symbol, count)
            except SourceUnavailable as exc:
                self.last_errors.append((source, exc))
                if not self.config.continue_on_error:
                    raise
                continue

            if data or (default_empty_ok and data is not None):
                self.last_source = source
                # U4：写穿透增量合并（按 datetime 去重，只落「末根之后」新数据）。
                if self.kline_cache is not None and data:
                    with contextlib.suppress(Exception):  # 缓存写入失败不影响返回
                        self.kline_cache.merge(symbol, period, [_as_dict(x) for x in data])
                if as_format == "dict":
                    return [_as_dict(x) for x in data]
                return data
            # 空结果 = 本源无有效数据（如主站可达但代码无 K 线），视为不可用，
            # 继续降级（与 :meth:`quotes` 的空列表语义对齐，审计 §2-15）
            self.last_errors.append((source, SourceUnavailable(f"{source}: 返回空 K 线数据")))

        raise AllSourcesExhausted(
            "全部 K 线源失败",
            context={
                "sources": active,
                "errors": [f"{s}: {e}" for s, e in self.last_errors],
            },
        )

    # -- 本地 reader 策略 -------------------------------------------------- #
    def _reader_kline(
        self, symbol: str, period: str, count: int, *, start: int = 0
    ) -> list[dict[str, Any]]:
        if not self.vipdoc_root:
            raise SourceUnavailable("reader: 未配置 vipdoc_root")
        from ..reader import DayBarReader, MinBarReader, resolve_vipdoc_path
        from ..reader.profile import Period

        rperiod = _period_to_reader_period(period)
        path = resolve_vipdoc_path(self.vipdoc_root, symbol, rperiod)
        if not path.exists():
            raise SourceUnavailable(f"reader: 文件不存在 {path}")
        reader = MinBarReader() if rperiod == Period.M1 else DayBarReader()
        bars = reader.read(path, output="dict")
        # start 偏移（Q1-a 对齐 facade：end=len-start, begin=end-count；
        # start=0 时与旧「末尾 count 根」行为完全一致）
        if start:
            end = max(0, len(bars) - start)
            begin = max(0, end - count) if count else 0
            return bars[begin:end]
        return bars[-count:] if count else bars

    # -- 离线合成（占位，明确标注非真实数据） ------------------------------ #
    def _synthetic_kline(self, symbol: str, count: int) -> list[dict[str, Any]]:
        """离线合成占位 K 线（恒 1.0 元 / 零量，**非真实行情**）。

        与 golden 回放口径（真实主站自采集样本，价格 ×100 缩放）完全不同：
        合成数据只用于「全链路冒烟」，不参与任何数值口径断言（golden 口径
        见 :func:`_golden_kline` 与 tests/golden）。
        """
        from ..domain.models import Bar

        bars = []
        for i in range(max(1, count)):
            bars.append(
                Bar(
                    datetime=f"2020-01-{i % 28 + 1:02d} 15:00",
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=0,
                    amount=0.0,
                    extra={"synthetic": True, "code": symbol},
                ).to_dict()
            )
        return bars


# --------------------------------------------------------------------------- #
# 辅助：统一输出
# --------------------------------------------------------------------------- #
def _to_row(q: Any) -> dict[str, Any]:
    """WebQuoteClient 返回 Quote 或 dict，统一成 dict。"""
    if hasattr(q, "to_dict"):
        return q.to_dict()
    return dict(q)


def _to_quote(x: Any):
    from ..domain.models import Quote

    if isinstance(x, Quote):
        return x
    if isinstance(x, dict):
        return Quote(
            code=x.get("code", ""),
            price=x.get("price", 0.0),
            last_close=x.get("last_close", 0.0),
            open=x.get("open", 0.0),
            high=x.get("high", 0.0),
            low=x.get("low", 0.0),
            volume=int(x.get("volume", 0) or 0),
            amount=x.get("amount", 0.0),
        )
    return x


def _as_dict(x: Any) -> dict[str, Any]:
    if hasattr(x, "to_dict"):
        return x.to_dict()
    if isinstance(x, dict):
        return x
    return dict(x)


def build_router(**kw: Any) -> DataSourceRouter:
    """从全局配置构建路由（供 CLI / 上层调用）。

    注意两点（审计 §2-15 / §3）：

    * 读取全局配置失败不再静默吞异常 —— 记 warning 后回退默认路由；
    * ``web`` 源受 ``WebConfig.enabled`` 总闸约束（以 WebConfig 为准，
      消除「WebConfig.enabled=False vs SourcesConfig.web=True」的默认矛盾，
      审计 §3-3）：总闸关闭时即使 ``sources.enabled["web"]=True`` 也不路由 web。
    """
    try:
        from ..config import get_config

        cfg = get_config()
        sources_cfg = cfg.sources
        if not bool(getattr(cfg.web, "enabled", True)):
            enabled = dict(sources_cfg.enabled)
            enabled["web"] = False
            sources_cfg = replace(sources_cfg, enabled=enabled)
        # U4：配置了 sources.kline_cache_db 即一键装配本地 K 线缓存。
        if kw.get("kline_cache") is None and sources_cfg.kline_cache_db:
            from ..cache import KlineCache

            kw["kline_cache"] = KlineCache(sources_cfg.kline_cache_db)
        return DataSourceRouter(config=sources_cfg, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "build_router: 读取全局配置失败，回退默认路由配置：%s: %s",
            type(exc).__name__,
            exc,
        )
        return DataSourceRouter(**kw)
