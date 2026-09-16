# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Compatibility data router on top of the v12 Provider service.

This module no longer owns a second live-routing engine. All real online data is
executed by :class:`tstdx.service.UnifiedMarketDataService`; this compatibility
layer only translates old ``order=`` / ``source=`` arguments and retains
explicit test/replay plus TDX vipdoc local-history paths.

Production invariants:

* TDX is the deterministic default Provider.
* One query binds to exactly one Provider.
* TDX host failover happens inside TDX transport only.
* TDX failure never triggers Sina/Tencent/Eastmoney.
* ``source=`` is a compatibility selector for ProviderId, not a second entity.
* live quote/bar paths never consult replay/synthetic/stale cache first.
* multi-item legacy ``order`` is rejected because it encodes cross-Provider
  fallback semantics removed by v12.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..config.schema import SourcesConfig
from ..errors import SourceUnavailable, TdxError, ValidationError
from ..providers import PROVIDERS, normalize_provider_id, resolve_provider
from ..service import QueryResult, UnifiedMarketDataService

__all__ = ["DataSourceRouter", "SourceUnavailable", "build_router"]

logger = logging.getLogger(__name__)


def _period_to_reader_period(period: str) -> str:
    from ..reader.profile import Period

    p = (period or "day").lower()
    if p in ("day", "daily", "d"):
        return Period.DAY
    if p in ("1min", "min", "m1", "1m"):
        return Period.M1
    if p in ("5min", "5m", "m5"):
        return Period.M5
    raise SourceUnavailable(
        f"TDX vipdoc 不支持周期 {period!r}（仅 day/1min/5min）",
        context={"provider": "tdx", "channel": "vipdoc", "period": period},
    )


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
    golden_root: Path,
    code: str,
    category: int,
    count: int,
) -> list[dict[str, Any]] | None:
    """Replay a captured TDX payload. Test/replay only, never live fallback."""
    import json

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
    """Replay a captured 0x0530 payload. Test/replay only."""
    import json

    from ..codec.framing import ResponseFrame
    from ..protocol.registry import dispatch

    candidates = sorted(
        golden_root.glob(f"quotation/0x0530_realtime_quote_{code}/20*"),
        reverse=True,
    )
    if not candidates:
        return None
    meta_path = candidates[0] / "meta.json"
    payload_path = candidates[0] / "payload.bin"
    if not (meta_path.exists() and payload_path.exists()):
        return None
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
    result = dispatch(
        frame,
        code=ctx.get("code", code),
        market=ctx.get("market"),
        price_scale=100,
    )
    return result.rows[0] if result.rows else None


class DataSourceRouter:
    """Legacy router surface delegating all live execution to one service."""

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
        web_sources: Sequence[str] | None = None,
        timeout: float | None = None,
        allow_replay: bool = False,
        allow_synthetic: bool = False,
        web_master_enabled: bool = True,
        service: UnifiedMarketDataService | None = None,
    ) -> None:
        self.config = config or SourcesConfig()
        self.order = list(order) if order else None
        self.vipdoc_root = (
            Path(vipdoc_root)
            if vipdoc_root
            else (Path(self.config.vipdoc_root) if self.config.vipdoc_root else None)
        )
        self.golden_root = Path(golden_root) if golden_root else None
        self.tdx_hosts = list(tdx_hosts) if tdx_hosts is not None else None
        # Kept only for constructor compatibility. Live v12 queries intentionally
        # do not use these caches before an upstream request.
        self.kline_cache = kline_cache
        self.quote_cache = quote_cache
        self.web_sources = [normalize_provider_id(x) for x in (web_sources or ())]
        # ``WebConfig.enabled`` 总闸：由 :func:`build_router` 从全局配置注入。
        # 关闭时兼容层的 legacy ``web`` 选择器必须 fail-closed。
        self.web_master_enabled = bool(web_master_enabled)
        self.timeout = float(timeout if timeout is not None else 5.0)
        self.allow_replay = bool(allow_replay)
        self.allow_synthetic = bool(allow_synthetic)
        self._owns_service = service is None
        if service is None:
            # 默认绑定 **planned** 服务（v12 QueryPlan/SingleFlight 层），
            # 与公共 runtime / 官方门面使用同一执行引擎（单一事实源）。
            # 延迟导入以避免 planned_service ↔ sources 的模块级环。
            from ..planned_service import UnifiedMarketDataService as _PlannedService

            service = _PlannedService(
                hosts=self.tdx_hosts,
                timeout=self.timeout,
            )
        self._service: UnifiedMarketDataService = service
        # Compatibility diagnostics only; reset at request start.
        self.last_errors: list[tuple[str, BaseException]] = []
        self.last_source: str | None = None

    def _reset_diagnostics(self) -> None:
        self.last_errors = []
        self.last_source = None

    def _legacy_web_provider(self) -> str:
        """Map legacy ``web`` to one deterministic Provider, never a chain."""
        if not self.web_master_enabled:
            raise SourceUnavailable(
                "WebConfig.enabled 总闸已关闭：兼容层不提供任何 Web Provider",
                context={"provider": "web", "capability": "legacy_web", "fallback": False},
            )
        if self.web_sources:
            return self.web_sources[0]
        try:
            from ..config import get_config

            configured = list(get_config().web.enabled_sources)
            if configured:
                candidate = normalize_provider_id(configured[0])
                if candidate in PROVIDERS.ids():
                    return candidate
        except (AttributeError, TypeError, ValueError):
            # Missing optional Web settings may use the documented compatibility
            # default; invalid top-level config is handled by build_router().
            pass
        return "tencent"

    def _selection(
        self,
        *,
        provider: str | None,
        source: str | None,
        order: Sequence[str] | None,
    ) -> tuple[str, str | None, str | None]:
        """Return ``(provider, channel, special_mode)``."""
        if provider is not None or source is not None:
            pid = resolve_provider(provider=provider, source=source, default="tdx")
            PROVIDERS.get(pid)
            return pid, None, None

        legacy = list(order) if order is not None else (list(self.order) if self.order else [])
        if not legacy:
            return "tdx", None, None
        if len(legacy) != 1:
            raise ValidationError(
                "v12 已禁止多 Provider 自动 fallback；order 必须只选择一个执行路径",
                context={"order": legacy, "fallback": False},
            )

        # 兼容选择器 ``reader``/``local``/``vipdoc`` 在 providers 别名表中会被
        # 归一化为 ``local_vipdoc``（v13 起的 canonical Provider id），但本层
        # 的 legacy ``order`` 语义需要区分「本地 vipdoc 读取」与 web/cache/
        # synthetic 三种特殊模式，故先按 **原始** 选择器匹配，再回退到
        # Provider 别名解析。
        raw = str(legacy[0]).strip().lower().replace("-", "_")
        if raw == "tdx":
            return "tdx", None, None
        if raw in ("reader", "local", "vipdoc"):
            return "tdx", "vipdoc", None
        if raw == "web":
            return self._legacy_web_provider(), None, None
        if raw == "cache":
            if not self.allow_replay:
                raise SourceUnavailable(
                    "golden replay 未获生产查询授权",
                    context={"mode": "replay", "real": False},
                )
            return "tdx", "quotation", "replay"
        if raw == "synthetic":
            if not self.allow_synthetic:
                raise SourceUnavailable(
                    "synthetic 仅允许测试运行时显式启用",
                    context={"mode": "synthetic", "real": False},
                )
            return "tdx", "quotation", "synthetic"
        item = normalize_provider_id(raw)
        PROVIDERS.get(item)
        return item, None, None

    def _record_error(self, provider: str, exc: BaseException) -> None:
        self.last_errors.append((provider, exc))

    def quotes(
        self,
        symbols: Sequence[str],
        *,
        as_format: str = "dict",
        default_empty_ok: bool = False,
        order: Sequence[str] | None = None,
        provider: str | None = None,
        source: str | None = None,
    ) -> list[Any]:
        """Fetch current quotes from exactly one Provider."""
        from ..client import _emit

        self._reset_diagnostics()
        symbols = list(symbols)
        pid, channel, special = self._selection(
            provider=provider,
            source=source,
            order=order,
        )
        try:
            if special == "replay":
                if not self.golden_root:
                    raise SourceUnavailable("replay: 未配置 golden_root", context={"real": False})
                data: list[Any] = [
                    q
                    for sym in symbols
                    for q in (_golden_quote(self.golden_root, _split_for_cache(sym)[1]),)
                    if q
                ]
            elif special == "synthetic":
                raise SourceUnavailable("synthetic 不提供实时 quotes", context={"real": False})
            elif channel == "vipdoc":
                raise SourceUnavailable(
                    "TDX vipdoc 是 local_historical Channel，不提供实时 quotes",
                    context={"provider": "tdx", "channel": "vipdoc"},
                )
            else:
                quotes = self._service.quotes(symbols, provider=pid)
                data = list(quotes.data if isinstance(quotes, QueryResult) else quotes)
        except TdxError as exc:
            self._record_error(pid, exc)
            if default_empty_ok and "返回空" in str(exc):
                return []
            raise

        if not data and not default_empty_ok:
            err = SourceUnavailable(
                f"Provider {pid!r} 返回空行情数据",
                context={"provider": pid, "capability": "quotes", "fallback": False},
            )
            self._record_error(pid, err)
            raise err

        self.last_source = pid
        if as_format == "dict":
            return [_as_dict(x) for x in data]
        return _emit([_to_quote(x) for x in data], as_format)

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
        provider: str | None = None,
        source: str | None = None,
        channel: str | None = None,
    ) -> list[Any]:
        """Fetch bars from one Provider; unsupported semantics fail explicitly."""
        from ..domain.symbol import split_symbol

        self._reset_diagnostics()
        pid, legacy_channel, special = self._selection(
            provider=provider,
            source=source,
            order=order,
        )
        selected_channel = channel or legacy_channel
        _, code = split_symbol(symbol)

        try:
            if special == "replay":
                # TDX category 映射仅 replay/golden 回放需要；非 TDX Provider
                # （tencent/sina/eastmoney/...）按自身周期别名解析，不得先被
                # TDX category 表拦下（例如 sina 专有周期 "120min"）。
                from ..client import period_to_category

                if start:
                    raise ValidationError("replay K 线不支持 start 偏移")
                if adjust:
                    raise ValidationError("replay K 线不支持复权口径")
                if not self.golden_root:
                    raise SourceUnavailable("replay: 未配置 golden_root", context={"real": False})
                try:
                    data: list[Any] = (
                        _golden_kline(
                            self.golden_root,
                            code,
                            period_to_category(period),
                            count,
                        )
                        or []
                    )
                except TdxError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 审计 §2-15
                    # 自采集 golden 样本可能损坏（payload 畸形 → struct.error /
                    # zlib.error 等非 TdxError）。受控转换为 SourceUnavailable，
                    # 既不炸穿调用栈，也不改变 Provider（fail-closed 不变）。
                    raise SourceUnavailable(
                        f"replay golden 样本解析失败：{type(exc).__name__}: {exc}",
                        context={"provider": pid, "mode": "replay", "real": False},
                    ) from exc
            elif special == "synthetic":
                data = self._synthetic_kline(symbol, count)
            elif pid == "tdx" and selected_channel == "vipdoc":
                if adjust:
                    raise ValidationError("TDX vipdoc 仅提供原始价，不支持 adjust")
                data = self._reader_kline(symbol, period, count, start=start)
            else:
                if selected_channel is not None:
                    PROVIDERS.require(pid, "bars", channel=selected_channel)
                    if pid == "tdx" and selected_channel != "quotation":
                        raise ValidationError(
                            "统一 kline 兼容入口仅执行 tdx/quotation；"
                            "extended/goods 请使用 md.tdx.<channel>.bars()",
                            context={
                                "provider": pid,
                                "channel": selected_channel,
                                "capability": "bars",
                            },
                        )
                bars = self._service.bars(
                    symbol,
                    period=period,
                    count=count,
                    start=start,
                    adjust=adjust,
                    provider=pid,
                )
                data = list(bars.data if isinstance(bars, QueryResult) else bars)
        except TdxError as exc:
            self._record_error(pid, exc)
            if default_empty_ok and "返回空" in str(exc):
                return []
            raise

        if not data and not default_empty_ok:
            err = SourceUnavailable(
                f"Provider {pid!r} 返回空 K 线数据",
                context={"provider": pid, "capability": "bars", "fallback": False},
            )
            self._record_error(pid, err)
            raise err

        self.last_source = pid
        if as_format == "dict":
            return [_as_dict(x) for x in data]
        return list(data)

    def _reader_kline(
        self,
        symbol: str,
        period: str,
        count: int,
        *,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        if not self.vipdoc_root:
            raise SourceUnavailable(
                "TDX vipdoc 未配置 vipdoc_root",
                context={"provider": "tdx", "channel": "vipdoc"},
            )
        from ..reader import DayBarReader, MinBarReader, resolve_vipdoc_path
        from ..reader.profile import Period

        rperiod = _period_to_reader_period(period)
        path = resolve_vipdoc_path(self.vipdoc_root, symbol, rperiod)
        if not path.exists():
            raise SourceUnavailable(
                f"TDX vipdoc 文件不存在 {path}",
                context={"provider": "tdx", "channel": "vipdoc", "path": str(path)},
            )
        # lc1/lc5 use the minute-record layout; both must use MinBarReader.
        reader = MinBarReader() if rperiod in (Period.M1, Period.M5) else DayBarReader()
        bars = reader.read(path, output="dict")
        if start:
            end = max(0, len(bars) - start)
            begin = max(0, end - count) if count else 0
            return bars[begin:end]
        return bars[-count:] if count else bars

    @staticmethod
    def _synthetic_kline(symbol: str, count: int) -> list[dict[str, Any]]:
        from ..domain.models import Bar

        return [
            Bar(
                datetime=f"2020-01-{i % 28 + 1:02d} 15:00",
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=0,
                amount=0.0,
                extra={"synthetic": True, "real": False, "code": symbol},
            ).to_dict()
            for i in range(max(1, count))
        ]

    def close(self) -> None:
        if self._owns_service:
            self._service.close()

    def __enter__(self) -> DataSourceRouter:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


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
    """从全局配置构建兼容路由（供 CLI / 上层调用）。

    注意两点（审计 §2-15 / §3-3）：

    * 读取全局配置失败不再静默吞异常 —— 记 warning 后回退默认路由；
    * ``web`` 源受 ``WebConfig.enabled`` 总闸约束（以 WebConfig 为准，消除
      「WebConfig.enabled=False vs SourcesConfig.web=True」的默认矛盾）：
      总闸关闭时不向兼容层暴露任何 Web Provider。
    """
    try:
        from ..config import get_config

        cfg = get_config()
        sources_cfg = cfg.sources
        web_enabled = bool(getattr(cfg.web, "enabled", True))
        kw.setdefault("web_sources", list(getattr(cfg.web, "enabled_sources", ()) or ()))
        kw.setdefault("timeout", float(getattr(cfg.core, "timeout", 3.0)))
        if not web_enabled:
            # 总闸关闭：清空 legacy web 选择器可见的 Provider 列表。
            kw["web_sources"] = []
        router = DataSourceRouter(config=sources_cfg, **kw)
        router.web_master_enabled = web_enabled
        return router
    except Exception as exc:  # noqa: BLE001 - 配置中心不可用时必须可降级
        logger.warning(
            "build_router: 读取全局配置失败，回退默认路由配置：%s: %s",
            type(exc).__name__,
            exc,
        )
        # 默认服务（planned）本身也依赖全局配置；配置中心不可用时必须继续
        # 降级到不读配置的 Provider 执行核心，否则回退路径会二次抛错。
        if kw.get("service") is None:
            from ..service import UnifiedMarketDataService as _CoreService

            kw["service"] = _CoreService(timeout=float(kw.get("timeout") or 5.0))
        return DataSourceRouter(**kw)
