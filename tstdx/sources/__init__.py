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
    raise SourceUnavailable(f"本地 reader 不支持周期 {period!r}（仅 day/1min/5min）")


def _split_for_cache(symbol: str) -> tuple[int, str]:
    """从 symbol 提取 (tdx市场编号, 6位代码)，用于 golden 样本查找。"""
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
    result = dispatch(frame, code=ctx.get("code", code), market=ctx.get("market"), price_scale=100)
    if not result.rows:
        return None
    return result.rows[0]


class DataSourceRouter:
    """统一数据源路由（配置驱动的多级降级）。

    Legacy Quote/Kline cache 没有 Provider provenance，因此只允许用于默认的
    auto-route 兼容路径。调用方显式传 ``order=`` 时必须绕过这些旧缓存，
    防止 Web/reader/cache 数据污染显式 TDX 或其它单源请求。
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
            raise SourceUnavailable(f"{source}: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            if self.config.continue_on_error:
                raise SourceUnavailable(f"{source}: {type(exc).__name__}: {exc}") from exc
            raise

    def quotes(
        self,
        symbols: Sequence[str],
        *,
        as_format: str = "dict",
        default_empty_ok: bool = False,
        order: Sequence[str] | None = None,
    ) -> list[Any]:
        """获取实时行情；显式 ``order`` 时绕过无 provenance 的 legacy cache。"""
        from ..client import _emit

        symbols = list(symbols)
        legacy_cache_allowed = order is None
        if legacy_cache_allowed and self.quote_cache is not None:
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
                        data = self._safe_run("web", lambda c=client: c.quotes(symbols))
                    finally:
                        client.close()
                elif source == "cache":
                    if self.golden_root:
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
                if legacy_cache_allowed and self.quote_cache is not None and data:
                    with contextlib.suppress(Exception):
                        self.quote_cache.put(symbols, [_as_dict(x) for x in data])
                if as_format == "dict":
                    return [_as_dict(x) for x in data]
                return _emit([_to_quote(x) for x in data], as_format)
            self.last_errors.append((source, SourceUnavailable(f"{source}: 返回空行情数据")))
        raise AllSourcesExhausted(
            "全部行情源失败",
            context={
                "sources": active,
                "errors": [f"{s}: {e}" for s, e in self.last_errors],
            },
        )

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
        """获取 K 线；显式 ``order`` 时绕过无 provenance 的 legacy cache。"""
        from ..client import TdxClient, period_to_category
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        category = period_to_category(period)
        data: list[Any] | None = None
        legacy_cache_allowed = order is None

        if legacy_cache_allowed and self.kline_cache is not None:
            try:
                cached = self.kline_cache.get(symbol, period, count)
                if cached is not None and len(cached) >= max(1, count):
                    self.last_source = "cache"
                    if as_format == "dict":
                        return [_as_dict(x) for x in cached]
                    return cached
            except Exception:  # noqa: BLE001
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
                if legacy_cache_allowed and self.kline_cache is not None and data:
                    with contextlib.suppress(Exception):
                        self.kline_cache.merge(symbol, period, [_as_dict(x) for x in data])
                if as_format == "dict":
                    return [_as_dict(x) for x in data]
                return data
            self.last_errors.append((source, SourceUnavailable(f"{source}: 返回空 K 线数据")))

        raise AllSourcesExhausted(
            "全部 K 线源失败",
            context={
                "sources": active,
                "errors": [f"{s}: {e}" for s, e in self.last_errors],
            },
        )

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
        if start:
            end = max(0, len(bars) - start)
            begin = max(0, end - count) if count else 0
            return bars[begin:end]
        return bars[-count:] if count else bars

    def _synthetic_kline(self, symbol: str, count: int) -> list[dict[str, Any]]:
        """离线合成占位 K 线（恒 1.0 元 / 零量，**非真实行情**）。"""
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
    """从全局配置构建路由（供 CLI / 上层调用）。"""
    try:
        from ..config import get_config

        cfg = get_config()
        sources_cfg = cfg.sources
        if not bool(getattr(cfg.web, "enabled", True)):
            enabled = dict(sources_cfg.enabled)
            enabled["web"] = False
            sources_cfg = replace(sources_cfg, enabled=enabled)
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
