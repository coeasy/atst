# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""统一行情门面 :class:`UnifiedQuoteAPI`（覆盖竞品公开能力全集）。

把 tstdx 的三条数据通路收口为**一套统一接口**：

    本地 vipdoc  →  TDX 二进制协议  →  HTTP Web 源

覆盖能力对照（全部为 tstdx 原生命名，不沿用任何竞品 API 约定）
--------------------------------------------------------------
===================  ==========================================
能力                 方法
===================  ==========================================
实时行情（批量）     :meth:`UnifiedQuoteAPI.quotes`
五档快照             :meth:`UnifiedQuoteAPI.snapshot`
K 线 / 分钟线        :meth:`UnifiedQuoteAPI.bars`
当日分时             :meth:`UnifiedQuoteAPI.minute`
当日成交明细         :meth:`UnifiedQuoteAPI.trades`
历史分时             :meth:`UnifiedQuoteAPI.minute_history`
证券列表             :meth:`UnifiedQuoteAPI.security_list`
证券总数             :meth:`UnifiedQuoteAPI.security_count`
财务信息             :meth:`UnifiedQuoteAPI.finance`
除权除息             :meth:`UnifiedQuoteAPI.capital_changes`
复权 K 线            :meth:`UnifiedQuoteAPI.adjusted_bars`
板块行情             :meth:`UnifiedQuoteAPI.block_quotes`
F10 资料下载         :meth:`UnifiedQuoteAPI.f10`
F10 栏目目录         :meth:`UnifiedQuoteAPI.f10_catalog`
竞价快照 / 量价分布  :meth:`UnifiedQuoteAPI.auction` / :meth:`volume_price`
全市场快照（HTTP）   :meth:`UnifiedQuoteAPI.all_market`
外汇牌价（HTTP）     :meth:`UnifiedQuoteAPI.rates`
扩展市场 / 商品期权  :meth:`UnifiedQuoteAPI.ex_bars` / :meth:`goods_bars`
统一证券搜索         :meth:`UnifiedQuoteAPI.search_symbols` / :meth:`suggest`
问财自然语言选股     :meth:`UnifiedQuoteAPI.wencai`
指数目录             :meth:`UnifiedQuoteAPI.index_list`
权息资料（公司行为） :meth:`UnifiedQuoteAPI.corporate_action`
统一响应形态         :meth:`UnifiedQuoteAPI.query`（success/error/data/extra）
===================  ==========================================

设计要点
--------
* **自动路由**：``route="auto"`` 时按「离线缓存 → TDX 主站 → HTTP Web」
  顺序尝试；也可显式 ``route="local" | "tdx" | "web"``。
* **路由熔断（W11）**：某通路连续失败 ≥3 次进入 30s 冷却（实例级），
  auto 序冷却期内跳过该路由（显式路由仍尝试）；成功清零，``close()``
  不复位（进程级体验），``reset_circuit()`` 可手动复位。每路由失败记一条
  ``tstdx.facade`` warning；全路由失败时聚合 ``route_errors`` 进最终异常
  context，仍重抛最后一路由异常。
* **口径守卫（W13）**：路由不支持的参数（web 的 ``start``、tdx/local 的
  ``adjust``）显式 ``ValueError`` 或剔除该路由并告警——绝不静默变口径。
* **route 治理（W12）**：仅 tdx 能力的方法显式传非 tdx 路由 → ``ValueError``；
  web 有对应能力的方法接双通路；无实现的路由报
  「路由 … 在方法 … 无对应实现，可用: …」而非误导性「全部路由均无数据」。
* **符号变种全兼容**：所有含 ``symbol`` 的入参一律先经
  :mod:`tstdx.domain.symbol` 归一，``600000.SH`` / ``SH.600000`` /
  ``600000sh`` 等写法等价。
* **惰性建连**：TDX 客户端按需创建，``close()`` 统一释放。
* **单源内核（Q1-b）**：quotes / bars 各路由取数单源委托实例级
  :class:`tstdx.sources.DataSourceRouter`（对拍与裁定见
  ``docs/adr/ADR-012-路由链合并口径对拍.md``）；熔断/口径守卫为本类独有。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..domain.models import Bar, MinutePoint, Quote, Tick, to_dicts
from ..domain.symbol import normalize_symbol
from ..errors import AllHostsUnreachable, CommandOffline, SourceUnavailable, TdxError
from .routing import Route, RouteSelector

if TYPE_CHECKING:  # 仅注解引用，运行时不导入（保持零硬依赖与无环）
    from .response import ApiResponse

__all__ = ["UnifiedQuoteAPI", "quote_api"]

_LOG = logging.getLogger("tstdx.facade")

#: block_quotes 的 block_type → web board_rank 分组映射（P13-A 兜底）。
#: 0/1/2 有近似对应；3（指数）无对应，保持仅 tdx。
_BLOCK_TYPE_TO_WEB_BOARD: dict[int, str] = {0: "concept", 1: "industry", 2: "region"}


def _as_quote(row: Any) -> Quote:
    """dict 行 → :class:`Quote`（Q1-b：router 返回 dict 行，facade 出模型）。"""
    if isinstance(row, Quote):
        return row
    from ..client_core import _row_to_quote

    return _row_to_quote(row)


def _as_bar(row: Any) -> Bar:
    """dict 行 → :class:`Bar`（Q1-b：同上，保持 facade list[Bar] 契约）。"""
    if isinstance(row, Bar):
        return row
    from ..client_core import _row_to_bar

    return _row_to_bar(row)


def _try_tdx_or_unavailable(
    route: Route | None,
    fn: Any,
    *,
    method: str,
    command: str,
    alternatives: list[str],
    hint: str = "",
) -> Any:
    """P13-A：包裹仅 tdx 路由方法，把 offline/主站全超时转成 SourceUnavailable。

    语义（W12 契约保持不变）：

    * ``route="tdx"`` 显式路由：透传原异常（用户显式要求 tdx 时看真实错误）；
    * ``route=None`` 或 ``route="auto"``（走 default_route）：
      ``CommandOffline`` / ``AllHostsUnreachable`` 转成
      :class:`~tstdx.errors.SourceUnavailable`，context 附
      ``alternatives``（可用替代方法）与 ``hint``（诊断提示）；
    * 其他异常透传。

    与 W12「仅 tdx 路由」的显式拒绝（``ValueError``）配合：调用方先过
    ``_require_tdx_route``（显式非 tdx → ValueError），再到本 helper
    （tdx 通路失败 → 转换）。
    """
    try:
        return fn()
    except (CommandOffline, AllHostsUnreachable) as exc:
        # 显式 tdx 路由：透传原异常
        if route == "tdx":
            raise
        ctx: dict[str, Any] = {
            "method": method,
            "command": command,
            "cause": type(exc).__name__,
            "alternatives": list(alternatives),
        }
        if hint:
            ctx["hint"] = hint
        _LOG.warning(
            "%s: tdx 通路 %s（命令 %s），转换 SourceUnavailable；替代方案: %s",
            method,
            type(exc).__name__,
            command,
            ", ".join(alternatives),
        )
        raise SourceUnavailable(
            f"{method} 数据源当前不可用：TDX {command} {type(exc).__name__}",
            context=ctx,
            cause=exc,
        ) from exc


def quote_api(**kwargs: Any) -> UnifiedQuoteAPI:
    """工厂：返回 :class:`UnifiedQuoteAPI` 实例。"""
    return UnifiedQuoteAPI(**kwargs)


class UnifiedQuoteAPI(RouteSelector):
    """tstdx 统一行情接口（local / tdx / web 三通路自动路由）。

    Parameters
    ----------
    route:
        默认路由策略：``auto``（推荐）/ ``local`` / ``tdx`` / ``web``。
    vipdoc_root:
        本地通达信 vipdoc 目录（``route="local"`` 或 auto 兜底时使用）。
    hosts:
        TDX 主站候选池；``None`` 用内置池。
    web_sources:
        HTTP Web 源降级顺序；``None`` 用配置或内置默认。
    """

    def __init__(
        self,
        *,
        route: Route = "auto",
        vipdoc_root: str | None = None,
        hosts: Sequence[str] | None = None,
        web_sources: Sequence[str] | None = None,
        timeout: float = 5.0,
        factor_cache: Any | None = None,
        golden_root: str | None = None,
    ) -> None:
        self.default_route: Route = route
        self.vipdoc_root = vipdoc_root
        self.hosts = list(hosts) if hosts else None
        self.web_sources = list(web_sources) if web_sources else None
        self.timeout = timeout
        #: 除权除息事件 TTL 缓存（N4）；None → 惰性取进程级单例。
        self._factor_cache: Any = factor_cache
        self._tdx: Any = None
        self._web: Any = None
        #: golden 缓存根目录（Q1-b：透传给实例级 DataSourceRouter 的 cache 源）。
        self.golden_root = golden_root
        #: 实例级取数内核（Q1-b 合并：单源委托 :class:`DataSourceRouter`，
        #: 惰性创建，见 :meth:`_get_router`；close() 释放引用）。
        self._router: Any = None
        # W11 熔断状态初始化收口在 RouteSelector.__init__（P10-2 拆分）
        super().__init__()

    # ------------------------------------------------------------------ #
    # 资源作用域帮助方法（收口「构造 → 调用 → close」薄委托模板）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _with(factory: Any, fn: Any) -> Any:
        """构造 ``factory()`` 返回的客户端/Source，执行 ``fn(client)`` 后统一 ``close()``。

        异常路径同样保证 ``close()``；返回 ``fn`` 的结果。
        """
        client = factory()
        try:
            return fn(client)
        finally:
            client.close()

    # -- 底层客户端（惰性） -------------------------------------------------- #
    @property
    def tdx(self) -> Any:
        """TDX 二进制客户端（惰性创建）。"""
        if self._tdx is None:
            from ..client import TdxClient

            kw: dict[str, Any] = {"timeout": self.timeout}
            if self.hosts:
                kw["hosts"] = self.hosts
            self._tdx = TdxClient(**kw)
        return self._tdx

    @property
    def web(self) -> Any:
        """HTTP Web 统一客户端（惰性创建）。"""
        if self._web is None:
            from ..web import WebQuoteClient

            kw: dict[str, Any] = {"timeout": self.timeout}
            if self.web_sources:
                kw["sources"] = self.web_sources
            self._web = WebQuoteClient(**kw)
        return self._web

    @property
    def factor_cache(self) -> Any:
        """除权除息事件缓存（N4；未注入时惰性取进程级单例）。"""
        if self._factor_cache is None:
            from ..domain.finance import get_capital_change_cache

            self._factor_cache = get_capital_change_cache()
        return self._factor_cache

    def _get_router(self) -> Any:
        """实例级取数内核（Q1-b：惰性创建，供单源路由委托）。

        传 ``vipdoc_root``（reader 源）/ ``golden_root``（cache 源）/
        ``tdx_hosts``（tdx 源）。``timeout``/``web_sources`` 不透传——
        router 无对应构造参数（ADR-012 D7：行为兼容，不强加）。
        """
        if self._router is None:
            from ..sources import DataSourceRouter

            self._router = DataSourceRouter(
                vipdoc_root=self.vipdoc_root,
                golden_root=self.golden_root,
                tdx_hosts=self.hosts,
            )
        return self._router

    def close(self) -> None:
        """释放全部底层连接。

        不复位路由熔断状态（W11：进程级体验——主站宕机恢复判定不随
        连接重建而清零；需要复位请调用 :meth:`reset_circuit`）。
        Q1-b：一并释放实例级 :class:`DataSourceRouter`（其客户端即用即关，
        无持久连接，仅需丢弃引用；下次调用按需重建）。
        """
        for attr in ("_tdx", "_web"):
            c = getattr(self, attr, None)
            if c is not None:
                with contextlib.suppress(Exception):
                    c.close()
                setattr(self, attr, None)
        self._router = None

    def __enter__(self) -> UnifiedQuoteAPI:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 内部路由（W11/W12）：已收口至 tstdx.facade.routing.RouteSelector（P10-2）。
    # 本类继承其全部路由/熔断方法（_try_routes/_require_tdx_route/reset_circuit
    # 等），实例状态名 _route_fail_counts/_route_cooldown_until 不变。
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 实时行情
    # ------------------------------------------------------------------ #
    def quotes(self, symbols: str | Sequence[str], *, route: Route | None = None) -> list[Quote]:
        """实时行情（批量，返回 ``list[Quote]``，单位已归一化：股 / 元）。"""
        if isinstance(symbols, str):
            symbols = [symbols]
        syms = [normalize_symbol(s) for s in symbols]

        # Q1-b：取数单源委托 router（order 只含对应源；default_empty_ok=True
        # 保持 facade「空列表=成功」语义，不抛 AllSourcesExhausted——ADR-012 D2）。
        # 返回口径与历史一致：tdx 路由为 dict 行（TdxClient as_format="obj"
        # 实经 _emit → to_dicts）；web 路由 router 归一为 dict 行后转回
        # Quote 模型（与旧 self.web.quotes 直返 list[Quote] 一致）。
        def _tdx() -> list[Quote]:
            return list(self._get_router().quotes(syms, order=["tdx"], default_empty_ok=True))

        def _web() -> list[Quote]:
            rows = self._get_router().quotes(syms, order=["web"], default_empty_ok=True)
            return [_as_quote(r) for r in rows]

        return self._try_routes(route, {"tdx": _tdx, "web": _web})

    def quotes_concurrent(self, symbols: str | Sequence[str], *, workers: int = 8) -> list[Quote]:
        """并发批量实时行情（E1：逐只 0x0530 线程池并发，tdx 通路）。

        适用于批量命令 0x054C 已下线的大批量场景：N 只延迟 ≈
        ``ceil(N / workers) × 单次 RTT``；失败标的跳过（记入
        ``client.last_errors``），结果保序。
        """
        if isinstance(symbols, str):
            symbols = [symbols]
        syms = [normalize_symbol(s) for s in symbols]
        return self.tdx.quotes_concurrent(syms, workers=workers, as_format="obj")

    def snapshot(self, symbol: str, *, route: Route | None = None) -> dict[str, Any]:
        """单标的五档全量快照（含买卖五档 / 均价 / 涨跌停）。

        W12：仅 tdx 通路有对应实现；显式传 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)

        def _tdx() -> dict[str, Any]:
            return self.tdx.snapshot(sym, as_format="dict")

        return self._try_routes(route or self.default_route, {"tdx": _tdx})

    # ------------------------------------------------------------------ #
    # K 线
    # ------------------------------------------------------------------ #
    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str | None = None,
        route: Route | None = None,
    ) -> list[Bar]:
        """K 线 / 分钟线（返回 ``list[Bar]``）。

        口径契约（W13，绝不静默变口径）：

        * ``local`` / ``tdx`` 通路为**原始价**（不复权），支持 ``start`` 偏移；
        * ``web`` 通路（腾讯 fqkline）**不支持 ``start``**，``adjust`` 原样透传；
        * ``adjust=None``（默认）→ 请求原始价：三通路口径一致（修正旧实现
          web 兜底强制 ``qfq`` 造成的口径漂移）；
        * ``adjust`` 显式传复权请求（如 ``"qfq"``/``"hfq"``）→ 仅 web 通路
          可满足：auto 序剔除 local/tdx 并告警；显式 ``route="local"/"tdx"``
          → ``ValueError``；
        * ``start`` 非零 → web 兜底剔除并告警；显式 ``route="web"`` →
          ``ValueError``。
        """
        sym = normalize_symbol(symbol)
        resolved: Route = route or self.default_route
        native_ok = adjust is None or adjust == ""
        web_ok = start == 0
        if resolved == "web" and not web_ok:
            raise ValueError(
                f"bars(route='web') 不支持 start={start!r}：web 通路无该参数，拒绝改变数据窗口"
            )
        if resolved in ("local", "tdx") and not native_ok:
            raise ValueError(
                f"bars(route={resolved!r}) 不支持 adjust={adjust!r}：tdx/local 仅原始价，"
                "拒绝改变复权口径"
            )

        def _local() -> list[Bar]:
            # Q1-b：单源委托 router._reader_kline（同源 vipdoc 引擎），空=成功
            # （default_empty_ok=True，ADR-012 D2）；router 返回 dict 行，
            # 此处转 Bar 模型保持 list[Bar] 契约（历史行为）。
            if period != "day":
                # vipdoc 本地文件只有 day（lc1/lc5 为分钟线）；week/month 需 tdx/web。
                # 守卫保留在 facade（W13 口径文案），不下放 router。
                raise TdxError(
                    f"本地 vipdoc 仅支持 day（week/month 无本地文件），收到 {period!r}",
                    context={"period": period},
                )
            rows = self._get_router().kline(
                sym,
                period=period,
                count=count,
                start=start,
                order=["reader"],
                default_empty_ok=True,
            )
            return [_as_bar(r) for r in rows]

        def _tdx() -> list[Bar]:
            rows = self._get_router().kline(
                sym,
                period=period,
                count=count,
                start=start,
                order=["tdx"],
                default_empty_ok=True,
            )
            return [_as_bar(r) for r in rows]

        def _web() -> list[Bar]:
            # HTTP Web 兜底：adjust 透传（None → 原始价，与 tdx/local 口径一致；
            # router kline 的 adjust 参数默认 ""，行为与历史一致）
            rows = self._get_router().kline(
                sym,
                period=period,
                count=count,
                order=["web"],
                adjust=adjust if adjust is not None else "",
                default_empty_ok=True,
            )
            return [_as_bar(r) for r in rows]

        fns: dict[str, Any] = {}
        if native_ok:
            fns["local"] = _local
            fns["tdx"] = _tdx
        else:
            _LOG.warning(
                "bars: auto 路由剔除 local/tdx（adjust=%r 需复权能力，tdx/local 仅原始价）",
                adjust,
            )
        if web_ok:
            fns["web"] = _web
        else:
            _LOG.warning(
                "bars: auto 路由剔除 web 兜底（start=%r 仅 tdx/local 通路支持，拒绝改变数据窗口）",
                start,
            )
        if not fns:  # 口径约束下无路由可用
            raise TdxError(
                f"bars: 无路由满足口径要求（start={start!r}, adjust={adjust!r}）",
                context={"start": start, "adjust": adjust},
            )
        return self._try_routes(route, fns)

    # ------------------------------------------------------------------ #
    # 本地落盘
    # ------------------------------------------------------------------ #
    def sync_daily(
        self,
        symbols: Sequence[str],
        *,
        root: str | None = None,
        profile: str | Any = "a_share_day",
        chunk: int = 800,
        max_windows: int = 64,
    ) -> dict[str, dict[str, Any]]:
        """增量同步日线到本地 vipdoc（P1-3：断点续传 + 幂等）。

        对每只证券：读本地 ``.day`` 末日期 → 从 TDX 主站拉取其后的增量日线
        → 追加写入本地文件。已存在的日期一律跳过（重跑不重复）；写入后
        文件可直接由 :class:`~tstdx.reader.formats.DayBarReader` 回读。

        Parameters
        ----------
        symbols:
            证券代码列表（任意书写变种，如 ``"sh600519"`` / ``"600519"``）。
        root:
            本地 vipdoc 根目录；``None`` 用构造时的 ``vipdoc_root``。
        profile:
            本地 ``.day`` 档案名（默认 A 股；期货传 ``"future_day"`` 等）。
        chunk / max_windows:
            断点续传的单窗口拉取根数与最大回退窗口数。

        Returns
        -------
        ``{symbol: {"added": int, "existed": int, "path": str}}``
        """
        from ..sink.local_day import LocalDaySink

        base = root or self.vipdoc_root
        if not base:
            raise ValueError("sync_daily 需要 root（或构造 UnifiedQuoteAPI 时配置 vipdoc_root）")
        sink = LocalDaySink(base, profile=profile)
        out: dict[str, dict[str, Any]] = {}

        def _fetch(sym: str, offset: int, count: int) -> list[Any]:
            return self.tdx.bars(sym, period="day", count=count, start=offset, as_format="dict")

        def _make_fetch(sym: str):
            def _fetch_one(offset: int, count: int) -> list[Any]:
                return _fetch(sym, offset, count)

            return _fetch_one

        for sym in symbols:
            norm = normalize_symbol(sym)
            res = sink.sync(norm, _make_fetch(norm), chunk=chunk, max_windows=max_windows)
            out[sym] = {
                "added": res.added,
                "existed": res.existed,
                "path": res.path,
                "last_date": sink.last_date(norm),
            }
        return out

    # ------------------------------------------------------------------ #
    # 分时 / 成交
    # ------------------------------------------------------------------ #
    def minute(
        self, symbol: str, *, count: int = 0, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """当日分时（``0x0537``；W12：tdx/web 双通路，web 兜底为 HTTP 分时源）。

        .. note:: 语义澄清（N6）
           这里是「**当日 1 分钟分时**」快照（tdx 主站失败自动兜底 HTTP）。
           三入口请勿混用：
           * 需要**分钟 K 线**（历史多日、可选 1/5/15/30/60 周期）→
             :meth:`minute_klines`；
           * 需要**强制 HTTP 当日分时**（不经 TDX 主站）→ :meth:`minute_web`。
        """
        sym = normalize_symbol(symbol)

        def _tdx() -> list[dict[str, Any]]:
            return list(self.tdx.minute_today(sym))

        def _web() -> list[dict[str, Any]]:
            return to_dicts(self.minute_web(sym))

        rows = self._try_routes(route, {"tdx": _tdx, "web": _web})
        return rows[:count] if count > 0 else list(rows)

    def minute_history(
        self, symbol: str, date: int, *, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """历史分时（``0x0533``）。

        W12：web 通路无「历史分时」对应能力（``intraday``/``ticks`` 均为
        当日），仅 tdx；显式传 ``route="web"/"local"`` → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        return list(self.tdx.minute_history(sym, date))

    def trades(
        self, symbol: str, *, start: int = 0, count: int = 0, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """当日成交明细（``0x0FC5``；W12：tdx/web 双通路，web 为腾讯逐笔首页）。

        口径守卫（对齐 W13）：``start`` 偏移仅 tdx 通路支持——显式
        ``route="web"`` 且 ``start`` 非零 → ``ValueError``；auto 序剔除 web
        兜底并告警，绝不静默改变数据窗口。
        """
        sym = normalize_symbol(symbol)
        resolved: Route = route or self.default_route
        web_ok = start == 0
        if resolved == "web" and not web_ok:
            raise ValueError(
                f"trades(route='web') 不支持 start={start!r}：web 逐笔无偏移语义，拒绝改变数据窗口"
            )

        def _tdx() -> list[dict[str, Any]]:
            return list(self.tdx.trade_today(sym, start=start, count=count))

        fns: dict[str, Any] = {"tdx": _tdx}
        if web_ok:

            def _web() -> list[dict[str, Any]]:
                from ..web.facade import WebQuoteSession

                return to_dicts(WebQuoteSession.ticks(sym))

            fns["web"] = _web
        else:
            _LOG.warning(
                "trades: auto 路由剔除 web 兜底（start=%r 仅 tdx 通路支持，拒绝改变数据窗口）",
                start,
            )
        rows = self._try_routes(route, fns)
        return rows[:count] if count > 0 else list(rows)

    # ------------------------------------------------------------------ #
    # 证券元数据
    # ------------------------------------------------------------------ #
    def security_list(
        self, market: str | int = 0, start: int = 0, *, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """证券列表（``market`` 可传 ``"sh"/"sz"/"bj"`` 或编号）。

        路由：

        * ``route="web"`` 或 tdx 命令 ``0x044D`` 已登记 offline（2026-09-06
          实测公开主站停答）时 → **东财 clist 分页兜底**，行
          ``{market, code, name, type, source="eastmoney_clist"}``（``type``
          为 ``"stock"``——clist A 股过滤即全部股票，无 tdx 的类型枚举）。
        * 其余情形走 tdx（``0x044D`` 若被主站恢复即自动回到 tdx 行
          ``{market, code, name, type:int}``）。
        """
        resolved: Route = route or self.default_route
        if resolved == "web":
            return self._security_list_web(market, start=start)
        try:
            from ..client import split_symbol as _split_for_market

            if isinstance(market, str):
                market = _split_for_market(f"{market}000001")[0]
            return list(self.tdx.security_list(market, start=start))
        except CommandOffline:
            _LOG.warning("security_list: tdx 0x044D 已下线（2026-09 实测），降级东财 clist")
            return self._security_list_web(market, start=start)

    def security_list_all(
        self, market: str | int = 0, *, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """全市场代码表导出（分页遍历直至耗尽）。

        tdx ``0x044D`` 已 offline（2026-09-06 实测停答）→ 默认走东财 clist
        全市场分页（``{market, code, name, type, source="eastmoney_clist"}``）；
        主站恢复后走 tdx 原生行。可直接落盘作本地符号库。
        """
        resolved: Route = route or self.default_route
        if resolved == "web":
            return self._security_list_web(market, start=0, all_pages=True)
        try:
            from ..client import split_symbol as _split_for_market

            if isinstance(market, str):
                market = _split_for_market(f"{market}000001")[0]
            return list(self.tdx.export_security_list(market))
        except CommandOffline:
            _LOG.warning(
                "security_list_all: tdx 0x044D 已下线（2026-09 实测），降级东财 clist 全市场分页"
            )
            return self._security_list_web(market, start=0, all_pages=True)

    def _security_list_web(
        self, market: str | int = 0, *, start: int = 0, all_pages: bool = False
    ) -> list[dict[str, Any]]:
        """东财 clist 代码表兜底（对齐 tdx 0x044D 行 schema，字段语义差异见方法文档）。"""
        from ..domain.symbol import split_symbol as _split
        from ..web.fundflow import EastmoneyRankSource

        fs_market = {0: "sz_a", 1: "sh_a", 2: "bse"}
        m = fs_market.get(market, market) if isinstance(market, int) else str(market).lower()
        if m not in ("sz_a", "sh_a", "bse"):
            if isinstance(market, str) and market.isdigit():
                m = fs_market.get(int(market), "all_a")
            else:
                m = _split(f"{market}000001")[0]
                m = {0: "sz_a", 1: "sh_a", 2: "bse"}.get(m, "all_a")
        page = start // 100 + 1 if start else 1
        page_size = 100
        rows: list[dict[str, Any]] = []
        src = EastmoneyRankSource()
        try:
            if all_pages:
                pn = page
                while True:
                    chunk = src.fetch_rows(
                        m,
                        sort="code",
                        limit=page_size,
                        page=pn,
                        ascending=True,
                        extra_fields=["f84", "f85"],
                    )
                    rows.extend(chunk)
                    if len(chunk) < page_size:
                        break
                    pn += 1
                    if pn > 100:  # 防御上限（全市场 ~5.5k 行，100 页远超）
                        break
            else:
                rows = src.fetch_rows(
                    m,
                    sort="code",
                    limit=page_size,
                    page=page,
                    ascending=True,
                    extra_fields=["f84", "f85"],
                )
        finally:
            src.close()
        market_no = {"sz_a": 0, "sh_a": 1, "bse": 2}.get(m, 0)
        return [
            {
                "market": market_no,
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "type": "stock",
                "source": "eastmoney_clist",
            }
            for r in rows
        ]

    def security_count(self, market: str | int = 0, *, route: Route | None = None) -> int:
        """证券总数（``0x044E``）。

        W12：web 通路无对应能力，仅 tdx；显式传 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        from ..client import split_symbol as _split_for_market

        if isinstance(market, str):
            market = _split_for_market(f"{market}000001")[0]
        return int(self.tdx.security_count(market))

    def finance(self, symbol: str, *, route: Route | None = None) -> dict[str, Any]:
        """财务信息（``0x0010``）。

        W12：web 侧 ``profile``/``shareholders`` 等与 0x0010 财务字典字段
        不同源，无对应实现，仅 tdx；显式传 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        return dict(self.tdx.finance_info(sym))

    def capital_changes(
        self, symbol: str, *, route: Route | None = None, use_cache: bool = True
    ) -> list[Any]:
        """除权除息 / 股本变迁（``0x000F``）。

        W12：web 通路无除权除息对应能力，仅 tdx；显式传 ``route="web"/"local"``
        → ``ValueError``。

        N4：默认走 TTL 缓存（:attr:`factor_cache`）——命中跳过网络，过期 /
        损坏自动降级重取；``use_cache=False`` 强制在线（测试 / 实时校验用）。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        if use_cache:
            cached = self.factor_cache.get(sym)
            if cached is not None:
                return cached
        events = list(self.tdx.capital_changes(sym))
        if use_cache:
            self.factor_cache.put(sym, events)
        return events

    def adjusted_bars(
        self,
        symbol: str,
        *,
        method: str = "qfq",
        period: str = "day",
        count: int = 320,
        start: int = 0,
        events: Sequence[Any] | None = None,
        anchor_date: str | None = None,
    ) -> list[Bar]:
        """复权 K 线（F1：除权除息数据链路 → :class:`~tstdx.domain.adjust.AdjustEngine`）。

        把「原始 K 线 + 除权除息事件」合成前复权 / 后复权 / 定点复权：

        * **原始 K 线**：走 ``route="local"``（vipdoc 本地日线，需
          ``vipdoc_root``）；``period != "day"`` 或未配置本地目录时请显式
          传 ``events`` 并用 :meth:`bars` 自行取数后调
          :func:`tstdx.domain.adjust.to_adjusted`。
        * **除权除息事件**：优先 ``events``（:class:`~tstdx.domain.models.CapitalChange`
          或 0x000F 等价 dict 行）；缺省走在线 ``0x000F``（
          :meth:`capital_changes`）。

        ``method``：``qfq``（默认）/ ``hfq`` / ``fixed``（需 ``anchor_date``）/
        ``none``（原样返回）。
        """
        from ..domain.adjust import AdjustEngine
        from ..domain.finance import to_capital_changes
        from ..domain.models import CapitalChange as _CC

        sym = normalize_symbol(symbol)
        bars = self.bars(sym, period=period, count=count, start=start, route="local")
        ev_list = list(events) if events else list(self.capital_changes(sym, route="tdx"))
        # 兼容 dict 行（0x000F 解析行）：统一构造 CapitalChange
        if ev_list and not isinstance(ev_list[0], _CC):
            ev_list = to_capital_changes(ev_list)
        return AdjustEngine().apply(list(bars), ev_list, method, anchor_date=anchor_date)

    # ------------------------------------------------------------------ #
    # 板块 / 竞价 / 量价
    # ------------------------------------------------------------------ #
    def block_quotes(
        self, block_type: int = 0, start: int = 0, *, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """板块行情（命令 ``0x07E5``；block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数）。

        P13-A：``0x07E5`` 已实测 offline（2026-09-06），auto 路由下**降级到
        web 近似能力**——腾讯板块排行 :meth:`WebQuoteSession.board_rank`
        （行业/概念/地区三分组，含领涨股与 5/20 日涨幅）。

        **语义差异（务必阅读）**：

        * ``block_type`` 映射：0（概念）→ ``"concept"``、1（行业）→
          ``"industry"``、2（地区）→ ``"region"``；3（指数）无对应，
          抛 :class:`~tstdx.errors.SourceUnavailable`。
        * 返回字段与协议板块行情不同源：web 侧无 ``start`` 偏移语义
          （``start`` 非零 → 剔除 web 兜底并告警，与 W13 口径守卫一致）；
          每行附 ``"source": "tencent_board_rank"`` 标记来源。
        * 显式 ``route="web"`` 直接走 web 兜底；显式 ``route="tdx"`` 保持
          W12 契约（协议命令未恢复即抛 :class:`~tstdx.errors.CommandOffline`
          或 :class:`~tstdx.errors.AllHostsUnreachable`）；显式
          ``route="local"`` → ``ValueError``（vipdoc 无板块行情本地文件）。
        """
        resolved: Route = route or self.default_route
        if resolved == "local":
            raise ValueError("block_quotes 仅支持 tdx/web/auto 路由（vipdoc 无板块行情本地文件）")
        web_ok = start == 0
        if resolved == "web" and not web_ok:
            raise ValueError(
                f"block_quotes(route='web') 不支持 start={start!r}：web 排行无偏移语义，"
                "拒绝改变数据窗口"
            )

        fns: dict[str, Any] = {
            "tdx": lambda: list(self.tdx.block_quotes(block_type=block_type, start=start))
        }
        if resolved == "web" or (resolved == "auto" and web_ok):
            board = _BLOCK_TYPE_TO_WEB_BOARD.get(block_type)
            if board is None:
                if resolved == "web":
                    raise ValueError(
                        f"block_quotes(block_type={block_type!r}) 无 web 近似映射"
                        "（web 仅覆盖 0/1/2；3=指数无对应）"
                    )
            else:

                def _web_approx() -> list[dict[str, Any]]:
                    from ..web.facade import WebQuoteSession

                    rows = WebQuoteSession.board_rank(board=board, limit=100)
                    for r in rows:
                        r["source"] = "tencent_board_rank"
                    return rows

                fns["web"] = _web_approx
        else:
            _LOG.warning(
                "block_quotes: auto 路由剔除 web 兜底（start=%r 仅 tdx 通路支持，"
                "拒绝改变数据窗口）",
                start,
            )
        return self._try_routes(route, fns)

    def auction(self, symbol: str, *, route: Route | None = None) -> list[dict[str, Any]]:
        """集合竞价过程快照（命令 ``0x056A``）。

        P13-A：``0x056A`` 已实测 offline（2026-09-06），web 侧无对应能力。
        auto 路由下抛 :class:`~tstdx.errors.SourceUnavailable` 并附替代方案
        到 ``context["alternatives"]``。

        **可用替代**：

        * :meth:`hot_rank`（股吧个股人气榜）——市场关注度近似；
        * :meth:`longhu`（龙虎榜）——机构资金关注近似；
        * :meth:`quotes` + :meth:`snapshot`——实时快照与五档（覆盖盘中大部分场景）。

        显式 ``route="tdx"`` 保持 W12 契约（透传原异常，用户显式要求 tdx
        时应看到真实错误）；显式 ``route="web"/"local"`` → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        return _try_tdx_or_unavailable(
            route,
            lambda: list(self.tdx.auction_snapshot(sym)),
            method="auction",
            command="0x056A",
            alternatives=["hot_rank", "longhu", "quotes + snapshot"],
            hint="web 侧无对应能力；如需竞价阶段语义请等待 TDX 主站恢复",
        )

    def volume_price(self, symbol: str, *, route: Route | None = None) -> list[dict[str, Any]]:
        """量价分布 / 筹码分布（命令 ``0x051A``）。

        P13-A：``0x051A`` 已实测 offline（2026-09-06），web 侧亦无对应能力
        （筹码分布是 TDX 端本地计算，无 HTTP 近似）。auto 路由下抛
        :class:`~tstdx.errors.SourceUnavailable` 并附替代方案。

        **可用替代**：

        * :meth:`bars`（K 线）——自算量价指标（MA/VOL MA 等）；
        * :meth:`web.chip`（如未来 MAC 筹码接口打通）——暂不可用。

        显式 ``route="tdx"`` 保持 W12 契约；显式 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        return _try_tdx_or_unavailable(
            route,
            lambda: list(self.tdx.volume_price_dist(sym)),
            method="volume_price",
            command="0x051A",
            alternatives=["bars（自算量价指标）"],
            hint="筹码分布为 TDX 端本地计算，web 侧无对应能力",
        )

    def f10(self, symbol: str, filename: str, *, route: Route | None = None) -> list[Any]:
        """F10 资料下载与文本解析。

        W12：F10 为 tdx 文件服务器能力，web 无对应实现；显式传
        ``route="web"/"local"`` → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        from ..client import F10Client

        return self._with(
            lambda: F10Client(timeout=self.timeout),
            lambda client: client.parse_text(client.download(sym, filename)),
        )

    def f10_catalog(self, symbol: str, *, route: Route | None = None) -> list[Any]:
        """F10 栏目目录（命令 ``0x0001``，family=F10）。

        返回 ``[{title, filename}]``（见 :class:`tstdx.protocol.parsers.f10.F10CatalogParser`）——
        先枚举栏目，再按 ``filename`` 调 :meth:`f10` 下载正文。

        P13-A：``0x0001`` 已实测 offline（2026-09-06），web 侧无对应能力。
        auto 路由下抛 :class:`~tstdx.errors.SourceUnavailable` 并附替代方案
        到 ``context["alternatives"]``。

        **可用替代**：

        * :meth:`dc_query`（东财 datacenter 通用报表直查）——`dividend`
          （分红送配）、`performance`（业绩）、`holder_num`（股东户数）、
          `ipo`（IPO 日历）等；白名单见 :meth:`dc_reports`；
        * :meth:`corporate_action`（权息资料）——TDX 股本变迁文件；
        * :meth:`finance`（财务信息）——TDX 财务基础信息。

        显式 ``route="tdx"`` 保持 W12 契约；显式 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        return _try_tdx_or_unavailable(
            route,
            lambda: self._with(
                lambda: __import__("tstdx.client", fromlist=["F10Client"]).F10Client(
                    timeout=self.timeout
                ),
                lambda client: list(client.catalog(sym)),
            ),
            method="f10_catalog",
            command="0x0001",
            alternatives=[
                "dc_query（dividend/performance/holder_num/ipo）",
                "corporate_action",
                "finance",
            ],
            hint="F10 catalog 命令 offline 且 web 侧无对应能力，请用 dc_query 直查东财报表",
        )

    # ------------------------------------------------------------------ #
    # HTTP Web 独有能力
    # ------------------------------------------------------------------ #
    def all_market(
        self,
        *,
        node: str = "hs_a",
        page_size: int = 80,
        max_pages: int | None = None,
        source: str = "sina",
    ) -> list[Quote]:
        """全市场行情摘要（新浪/腾讯分页接口，仅 web 路由）。

        ``source`` 取 ``"sina"``（默认）/ ``"tencent"``；其余源显式报错。

        .. note:: 能力边界（N7）
           仅覆盖 A 股节点（``hs_a``/``cyb`` 等，见
           :meth:`tstdx.web.facade.WebQuoteSession.all_market`）。**港股/美股整
           市场枚举未实装**——传入 ``node="hk"/"us"`` 显式 ``ValueError``；
           需要港美行情请用 :meth:`hk_quotes` / :meth:`us_quotes`（按代码）
           或 :meth:`minute_klines` / :meth:`klines`（K 线）。
        """
        from ..web.facade import web_session

        return self._with(
            lambda: web_session(source),
            lambda sess: sess.all_market(node=node, page_size=page_size, max_pages=max_pages),
        )

    # -- 百度财经源（B0）------------------------------------------------------ #
    def baidu_kline(
        self, symbol: str, *, period: str = "day", count: int = 320, end_time: int | None = None
    ) -> list[Bar]:
        """百度财经日 / 周 / 月 K 线（A 股，Web 源；MA5/MA10/MA20 挂 ``extra``）。

        ``period`` 取 ``day`` / ``week`` / ``month``；``count`` 超过 250 自动
        分页向前翻页收集（旧→新排列）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.baidu_kline(symbol, period=period, count=count, end_time=end_time),
        )

    def baidu_minute(self, symbol: str) -> list[MinutePoint]:
        """百度财经当日 1 分钟分时（A 股，旧→新）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.baidu_minute(symbol))

    def baidu_ticks(self, symbol: str, *, limit: int = 200) -> list[Tick]:
        """百度财经当日逐笔成交（A 股，默认 200 条）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.baidu_ticks(symbol, limit=limit))

    def baidu_quote(self, symbol: str) -> Quote:
        """百度财经五档快照（A 股，含分时收盘价 / 均价 / 涨跌 / 五档盘口）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.baidu_quote(symbol))

    # -- 东财基金源（P0-1） ------------------------------------------------- #
    def fund_nav_history(
        self, code: str, *, page_size: int = 100, page_index: int = 1
    ) -> list[dict[str, Any]]:
        """东财基金历史净值（旧→新）。

        Returns
        -------
        ``list[dict]``：date / unit_nav / accum_nav / pct_change / bonus_ratio。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_nav_history(code, page_size=page_size, page_index=page_index),
        )

    def fund_estimate(self, code: str) -> dict[str, Any]:
        """东财基金实时估值快照（盘中估算）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_estimate(code))

    def fund_list(self) -> list[dict[str, Any]]:
        """东财全量基金列表（约 1.2 万条）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_list())

    # -- 东财指数成分股（P0-2） --------------------------------------------- #
    def index_constituents(self, index: str) -> list[dict[str, Any]]:
        """东财指数成分股列表（分页拉全量，仅 web 路由）。

        Parameters
        ----------
        index:
            指数代码（``000300`` 沪深300 / ``000016`` 上证50 / ``000905``
            中证500 / ``000688`` 科创50 / ``930050`` 中证A50 / ``000510``
            中证A500 / ``000852`` 中证1000 / ``399330`` 深证100 /
            ``899050`` 北证50 / ``932000`` 中证2000 等），可带市场前缀
            （``sh000300`` / ``sz399330`` / ``bj899050``）。

        Returns
        -------
        ``list[dict]``：code / name / secucode / weight / industry / region /
        price / change_pct / pe / eps / roe / bps / total_shares /
        free_shares / free_cap / type；``weight`` 仅部分指数族提供。
        """
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.index_constituents(index))

    def margin(
        self, symbol: str, *, days: int = 0, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """个股融资融券明细（东财 datacenter，``DATE`` 倒序，仅 web 路由）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519``）；须为两融标的，
            非标的返回空列表。
        days:
            取最近 N 个交易日（``0`` = 全部/按分页）。

        Returns
        -------
        ``list[dict]``：date / code / name / rzye（融资余额，元）/ rzmre /
        rzjme / rqye / rqyl / rzrqye / rzyezb（融资余额占比%）/ close /
        pct_change / total_mv 等；3/5/10 日差分字段在 ``extra``。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.margin(symbol, days=days, page=page, size=size)
        )

    def ex_market_list(self, *, route: Route | None = None) -> list[dict[str, Any]]:
        """扩展市场目录（7727：市场编号 / 名称 / 品种数；期货 / 期权 / 外汇等）。

        P13-A：7727 主站池（4 台候选）2026-09-06 实测全部连接超时，且
        13 台 7709 存活主机均未双开 7727——扩展行情服务疑似整体迁移/下线，
        web 侧亦无对应能力（web 直接取行情，无目录/品种列表接口）。
        auto 路由下抛 :class:`~tstdx.errors.SourceUnavailable` 并附替代方案。

        **可用替代**（按市场直取行情，跳过目录列举）：

        * :meth:`WebQuoteSession.hk_quotes`——港股批量实时；
        * :meth:`WebQuoteSession.us_quotes`——美股批量实时；
        * :meth:`rates`——外汇牌价；
        * 期货/期权数据请走 :meth:`dc_query` 或第三方数据源。

        显式 ``route="tdx"`` 保持 W12 契约（透传原异常，通常是
        :class:`~tstdx.errors.AllHostsUnreachable`）；显式
        ``route="web"/"local"`` → ``ValueError``。
        """
        self._require_tdx_route(route)
        return _try_tdx_or_unavailable(
            route,
            lambda: self._with(
                lambda: __import__("tstdx.client", fromlist=["ExMarketClient"]).ExMarketClient(
                    timeout=self.timeout
                ),
                lambda client: client.ex_market_list(),
            ),
            method="ex_market_list",
            command="0x0100 (7727)",
            alternatives=[
                "hk_quotes（港股）",
                "us_quotes（美股）",
                "rates（外汇）",
                "dc_query（期货期权）",
            ],
            hint="7727 扩展行情服务疑似整体下线，请按市场直取行情跳过目录列举",
        )

    def ex_instruments(
        self, market: int, *, start: int = 0, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """扩展市场品种列表（7727；``ex_market_list()`` 取市场编号后按市场拉取）。

        P13-A：与 :meth:`ex_market_list` 同因——7727 主站池全部超时，
        web 侧无对应能力（无品种目录接口）。auto 路由下抛
        :class:`~tstdx.errors.SourceUnavailable` 并附替代方案。

        **可用替代**：见 :meth:`ex_market_list`（按市场直取行情）。

        显式 ``route="tdx"`` 保持 W12 契约；显式 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        return _try_tdx_or_unavailable(
            route,
            lambda: self._with(
                lambda: __import__("tstdx.client", fromlist=["ExMarketClient"]).ExMarketClient(
                    timeout=self.timeout
                ),
                lambda client: client.ex_instrument_list(market, start=start),
            ),
            method="ex_instruments",
            command="0x0103 (7727)",
            alternatives=[
                "hk_quotes（港股）",
                "us_quotes（美股）",
                "rates（外汇）",
                "dc_query（期货期权）",
            ],
            hint="7727 扩展行情服务疑似整体下线，请按市场直取行情跳过品种列举",
        )

    @staticmethod
    def dc_reports() -> dict[str, str]:
        """可用 datacenter 报表白名单（``{别名: 报表名}``）。

        白名单维护在 :data:`tstdx.web.corporate.VALID_REPORTS`；
        新增报表名前须重新抓包验证（东财会下线旧报表）。
        """
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.dc_reports()

    def dc_query(
        self,
        report: str,
        *,
        symbol: str = "",
        filters: Sequence[str] = (),
        sort_columns: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """通用 datacenter 报表直查（字段**原样透传**，单位以东财报表页为准）。

        适用于白名单内、尚无专属封装方法的报表（如 ``dividend`` 分红送配）。
        ``symbol`` 按报表的个股过滤键自动构造 filter；更多过滤子句经
        ``filters`` 传入。字段语义差异大，本库不做单位翻译/重命名。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.dc_query(
                report,
                symbol=symbol,
                filters=filters,
                sort_columns=sort_columns,
                page=page,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
            ),
        )

    def minute_web(self, symbol: str) -> list[Any]:
        """当日分时（HTTP Web 路由，TDX 不可用时的降级）。

        .. note:: 语义澄清（N6）
           强制走 HTTP 当日分时（腾讯/东财），**不经 TDX 主站**；
           :meth:`minute` 在 tdx 失败时自动兜底到此。如需**分钟 K 线**
           （历史多日）请用 :meth:`minute_klines`。
        """
        sym = normalize_symbol(symbol)
        from ..web.adapters_ext import MinuteSource

        return self._with(MinuteSource, lambda src: src.fetch_minute(sym))

    def minute_klines(self, symbol: str, *, period: str = "5min", count: int = 240) -> list[Bar]:
        """分钟 K 线（HTTP Web 路由：A 股腾讯 mkline；港股/美股东财 push2his）。

        .. note:: 语义澄清（N6）
           返回**跨日分钟 K 线**（:class:`~tstdx.domain.models.Bar` 序列，
           可含历史多日），与「当日分时」不同——当日分时请用 :meth:`minute`
           （0x0537 双路）或 :meth:`minute_web`（强制 HTTP）。
        """
        from ..domain.symbol import parse_symbol

        sym = normalize_symbol(symbol)
        # src 在两分支被赋不同源类型——显式 Any 化统一推断
        if parse_symbol(sym).market in ("hk", "us"):
            from ..web.history import EastmoneyHistoryKlineSource

            return self._with(
                EastmoneyHistoryKlineSource,
                lambda src: src.fetch_bars(sym, period=period, count=count),
            )
        from ..web.adapters_ext import MinuteKlineSource

        return self._with(
            MinuteKlineSource, lambda src: src.fetch_bars(sym, period=period, count=count)
        )

    @staticmethod
    def suggest(key: str, *, limit: int = 10) -> list[dict[str, str]]:
        """证券代码联想搜索（新浪 smartbox，拼音/汉字/代码）。"""
        from ..web.adapters_ext import SuggestSource

        return UnifiedQuoteAPI._with(SuggestSource, lambda src: src.fetch_suggest(key, limit=limit))

    @staticmethod
    def search_symbols(
        pattern: str, *, limit: int = 10, market: str | None = None
    ) -> list[dict[str, str]]:
        """统一证券搜索（名称/拼音/代码 → 市场归属明确的候选，含 display 展示名）。

        与 :meth:`suggest` 同源，补齐 ``display`` 与 ``market`` 过滤语义，
        作为「搜代码 → 行情 / K 线 / 基本面」的统一入口。
        """
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.search_symbols(pattern, limit=limit, market=market)

    @staticmethod
    def wencai(
        query: str, *, page: int = 1, limit: int = 50, cookie: str | None = None
    ) -> list[dict[str, Any]]:
        """i问财自然语言选股（cookie 由调用方注入或环境变量提供）。"""
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.wencai(query, page=page, limit=limit, cookie=cookie)

    @staticmethod
    def index_list() -> list[dict[str, str]]:
        """常用指数目录（离线静态：名称 + 带市场代码）。"""
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.index_list()

    def corporate_action(self, symbol: str, *, route: Route | None = None) -> list[Any]:
        """权息资料（公司行为统一入口；除权除息/配股/送转）。

        与 :meth:`capital_changes` 同通道（TDX 股本变迁 ``0x000F`` GB 股本变迁文件），
        作为「公司行为」语义别名暴露——行业通用命名，便于迁移对接。
        """
        return self.capital_changes(symbol, route=route)

    # ------------------------------------------------------------------ #
    # efinance 对标扩展（基金 / 期货 / 债券 / 股票扩展，仅 web 路由）
    # ------------------------------------------------------------------ #
    # -- 股票扩展 ------------------------------------------------------------- #
    def stock_base_info(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """批量股票基础资料（市盈率 / 市净率 / 行业 / 总市值 / 流通市值）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.stock_base_info(list(codes)))

    def stock_all_performance(self, report_date: str = "") -> list[dict[str, Any]]:
        """全市场定期报告业绩（对标 efinance ``stock.get_all_company_performance``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.stock_all_performance(report_date=report_date)
        )

    def stock_report_dates(self, limit: int = 100) -> list[str]:
        """全市场财报报告期列表（去重，降序）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.stock_report_dates(limit=limit))

    def ipo_review(self, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """IPO 审核状态（对标 efinance ``stock.get_latest_ipo_info``）。

        与 :meth:`ipo_calendar`（申购日历）不同，本方法聚焦审核进度。
        """
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.ipo_review(page=page, size=size))

    # -- 基金扩展（天天基金移动端） ------------------------------------------- #
    def fund_base_info(self, code: str) -> dict[str, Any]:
        """基金基础信息（对标 efinance ``fund.get_base_info`` 单数）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_base_info(code))

    def fund_manager(self, code: str) -> dict[str, Any] | None:
        """基金经理（对标 efinance ``fund.get_fund_manager``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_manager(code))

    def fund_holdings(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金持仓（对标 efinance ``fund.get_invest_position``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_holdings(code, dates=dates))

    def fund_period_change(self, code: str) -> list[dict[str, Any]]:
        """基金阶段涨幅（对标 efinance ``fund.get_period_change``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_period_change(code))

    def fund_asset_allocation(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金资产配置（股票/债券/现金占比，对标 efinance ``fund.get_types_percentage``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_asset_allocation(code, dates=dates)
        )

    def fund_industry_distribution(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金行业分布（对标 efinance ``fund.get_industry_distribution``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_industry_distribution(code, dates=dates)
        )

    def fund_public_dates(self, code: str) -> list[str]:
        """基金公开持仓日期列表（对标 efinance ``fund.get_public_dates``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.fund_public_dates(code))

    # -- 期货（东财 push2 降级） ---------------------------------------------- #
    def futures_base_info(self) -> list[dict[str, Any]]:
        """全市场期货基础信息（对标 efinance ``futures.get_futures_base_info``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.futures_base_info())

    def futures_realtime(self, quote_id: str) -> dict[str, Any]:
        """期货实时快照（对标 efinance ``futures.get_realtime_quotes`` 单只）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.futures_realtime(quote_id))

    def futures_kline(
        self,
        quote_id: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "",
    ) -> list[Any]:
        """期货历史 K 线（对标 efinance ``futures.get_quote_history``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.futures_kline(quote_id, period=period, count=count, adjust=adjust),
        )

    def futures_trades(self, quote_id: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """期货当日成交明细（对标 efinance ``futures.get_deal_detail``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.futures_trades(quote_id, max_count=max_count)
        )

    # -- 债券（东财 push2 降级） ---------------------------------------------- #
    def bond_realtime(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """债券实时行情（对标 efinance ``bond.get_realtime_quotes``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.bond_realtime(list(codes)))

    def bond_base_info(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """债券基础信息（对标 efinance ``bond.get_base_info``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.bond_base_info(list(codes)))

    def bond_kline(
        self,
        code: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "",
    ) -> list[Any]:
        """债券历史 K 线（对标 efinance ``bond.get_quote_history``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.bond_kline(code, period=period, count=count, adjust=adjust),
        )

    def bond_history_bill(self, code: str, *, count: int = 10) -> list[dict[str, Any]]:
        """债券历史资金流（对标 efinance ``bond.get_history_bill``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.bond_history_bill(code, count=count))

    def bond_today_bill(self, code: str) -> dict[str, Any] | None:
        """债券当日资金流（对标 efinance ``bond.get_today_bill``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.bond_today_bill(code))

    def bond_trades(self, code: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """债券当日成交明细（对标 efinance ``bond.get_deal_detail``）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.bond_trades(code, max_count=max_count)
        )

    # -- astock-data-toolkit 对标（基本面衍生） -------------------------------- #
    def dividend_history(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """分红送转历史（对标 astock-data-toolkit ``dividend_history``）。

        东财 ``RPT_SHAREBONUS_DET``（已验证可用）。比例字段为「每 10 股」口径：
        ``bonus_shares_per_10``（送股）/ ``transfer_shares_per_10``（转增）/
        ``cash_dividend_per_10``（派息，元）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.dividend_history(symbol, page=page, size=size),
        )

    def stock_valuation(self, symbol: str) -> list[dict[str, Any]]:
        """个股估值快照（对标 astock-data-toolkit ``valuation_daily``，东财聚合版）。

        返回按报告期降序的 ``pe_ttm / pb / ps_ttm / pcf_ttm / total_share /
        total_mv``；取首项即「最新估值」。报表名 ``RPT_VALUEASSESS_DET`` 为
        best-effort（若返回 code=9501 需重新抓包校准）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.stock_valuation(symbol)
        )

    def holder_changes(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """股东 / 董监高增减持（对标 astock-data-toolkit ``holder_changes``）。

        东财聚合接口 ``RPT_CAPITAL_PARTICIPATION_DET``；``change_shares`` 正=增持、
        负=减持。报表名 best-effort（若返回 code=9501 需重新抓包校准）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.holder_changes(symbol, page=page, size=size),
        )

    def financial_abstract(self, symbol: str) -> list[dict[str, Any]]:
        """财务主要指标摘要（对标 astock-data-toolkit「财务摘要 28 指标」）。

        返回 ``eps / roe / bps / revenue / net_profit / 同比 / 毛利率 /
        资产负债率``，按报告期降序。报表名 ``RPT_F10_FINANCE_MAIN`` 为
        best-effort（若返回 code=9501 需重新抓包校准）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.financial_abstract(symbol)
        )

    def announcements(
        self, symbols: Sequence[str], *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """上市公司公告列表（对标 astock-data-toolkit 公告信息库）。

        复用东财 ``np-anotice-stock``；``symbols`` 可传多只。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.announcements(list(symbols), page=page, size=size),
        )

    # -- 三大财务报表（东财 F10 报表族） -------------------------------------- #
    def balance_sheet(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """资产负债表明细（东财 F10 ``RPT_F10_FINANCE_GBALANCE``，按报告期降序）。

        应收账款 / 长期应收款坏账风险、有息负债结构、商誉占净资产比等
        排雷分析入口。与 :meth:`financial_abstract`（摘要口径）互补——
        本方法给**报表级全字段明细**。

        ``raw=True`` 时每条附原始行（键 ``raw``）；报表名 best-effort，
        若返回 code=9501 需重新抓包校准。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.balance_sheet(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            ),
        )

    def income_sheet(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """利润表明细（东财 F10 ``RPT_F10_FINANCE_GINCOME``，按报告期降序）。

        研发费用占收入比、三费结构、净利率与少数股东损益占比等
        盈利质量分析入口。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.income_sheet(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            ),
        )

    def cash_flow(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """现金流量表明细（东财 F10 ``RPT_F10_FINANCE_GCASHFLOW``，按报告期降序）。

        经营现金流与净利润匹配度、自由现金流、投融资净额分析入口。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.cash_flow(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            ),
        )

    def fin_report(
        self,
        symbol: str,
        report: str = "balance_sheet",
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """通用 F10 报表直查（字段**原样透传**，单位以东财报表页为准）。

        ``report`` 可传别名（``balance_sheet`` / ``income_sheet`` / ``cash_flow``）
        或原始报表名。与 :meth:`dc_query`（datacenter-web 报表族）区别：
        本方法走东财 F10 独立后端，个股过滤键为 ``SECUCODE``（``600519.SH``）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fin_report(
                symbol,
                report,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
            ),
        )

    # -- 治理与卖方一致预期（东财 datacenter-web 报表族） --------------------- #
    def executive_holds(self, symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """董监高持股变动明细（按变动日期降序，内部人减持 / 套现排查）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.executive_holds(symbol, page=page, size=size),
        )

    def shareholder_changes(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """股东增减持明细（大股东 / 机构股东；``change_shares`` 正=增持、负=减持）。

        与 :meth:`holder_changes`（董监高增减持聚合口径）互补。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.shareholder_changes(symbol, page=page, size=size),
        )

    def org_profile(self, symbol: str) -> dict[str, Any] | None:
        """公司概况快照（法定代表人 / 董事长 / 主营 / 办公地址 / 员工数）。

        护城河与治理分析的结构化锚点。报表名 best-effort，
        若返回 code=9501 需重新抓包校准。
        """
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.org_profile(symbol))

    def org_profiles(
        self, symbols: Sequence[str], *, size: int = 50
    ) -> list[dict[str, Any]]:
        """公司概况批量（按代码去重，保留服务端返回顺序）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.org_profiles(list(symbols), size=size),
        )

    def rating_forecast(
        self,
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        sort_columns: str = "PUBLISH_DATE",
    ) -> list[dict[str, Any]]:
        """券商评级与目标价（默认按发布日期降序；``symbol`` 空 = 全市场最新）。

        「估值合理性」与「市场情绪」两维度的量化入口：目标价、EPS/PE 预测、
        覆盖机构数。``sort_columns`` 可用 ``RATING_ORG_NUM``（覆盖机构数最多）/
        ``TARGET_PRICE``（目标价最高）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.rating_forecast(
                symbol, page=page, size=size, sort_columns=sort_columns
            ),
        )

    def rating_consensus(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> dict[str, Any] | None:
        """一致预期聚合快照（目标价 / EPS / PE 均值 + 评级分布，本地计算）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.rating_consensus(symbol, page=page, size=size),
        )

    # -- ESG 评级 / 筹码分布（P1 扩展） --------------------------------------- #
    def esg_rating(self, symbol: str) -> dict[str, Any] | None:
        """个股 ESG 评级详情（新浪 13 家机构聚合：MSCI / 标普 / 华证 / 商道融绿等）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.esg_rating(symbol))

    def esg_history(self, symbol: str) -> dict[str, Any] | None:
        """个股 ESG 评级历史（季度变动，按机构分组）。"""
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.esg_history(symbol))

    def esg_ratings_all(
        self,
        source: str = "msci",
        *,
        market: str = "",
        rating: str = "",
        sort_column: str = "esg_rating",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """全市场 ESG 评级列表（支持 MSCI / 华证两种源）。

        ``source="msci"`` 返回 MSCI 评级（5200+ 只，含港股）；
        ``source="hz"`` 返回华证评级（6300+ 只，含 A 股）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.esg_ratings_all(
                source,
                market=market,
                rating=rating,
                sort_column=sort_column,
                sort_order=sort_order,
            ),
        )

    def chip_distribution(self, symbol: str, *, days: int = 5) -> dict[str, Any] | None:
        """个股筹码分布分析（资金流驱动，收集/发散判定）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.chip_distribution(symbol, days=days),
        )

    def chip_distributions(
        self, symbols: Sequence[str], *, days: int = 5, max_count: int = 20
    ) -> list[dict[str, Any]]:
        """批量筹码分布分析（按 accumulation_ratio 降序排列）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.chip_distributions(
                list(symbols), days=days, max_count=max_count
            ),
        )

    # -- 行业指数 / 概念指数 / 宏观经济 / 可转债（P2 扩展） ----------------- #
    def industry_index(
        self,
        *,
        report_date: str = "",
        board_code: str = "",
        size: int = 20,
        page: int = 1,
        sort_columns: str = "CHANGE_RATE",
    ) -> list[dict[str, Any]]:
        """行业指数指标（板块/概念，含涨跌幅/多周期涨跌/排名）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.industry_index(
                report_date=report_date,
                board_code=board_code,
                size=size,
                page=page,
                sort_columns=sort_columns,
            ),
        )

    def concept_index(
        self,
        *,
        index_code: str = "",
        size: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """概念指数成分（股票代码 → 概念映射）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.concept_index(
                index_code=index_code, size=size, page=page
            ),
        )

    def macro_cpi(self, *, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """CPI 宏观数据（全国/城镇/农村，含同比/环比/累计）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.macro_cpi(size=size, page=page),
        )

    def macro_ppi(self, *, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """PPI 宏观数据（出厂价同比/环比/累计）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.macro_ppi(size=size, page=page),
        )

    def macro_gdp(self, *, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """GDP 宏观数据（GDP 总量 / 三产占比 / 同比增速）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.macro_gdp(size=size, page=page),
        )

    def convertible_bonds(
        self, *, size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """可转债列表（基本信息 / 到期日 / 转股价 / 评级）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.convertible_bonds(size=size, page=page),
        )

    # -- 基金排行 / 快照 / 画像（天天基金移动端扩展） ------------------------- #
    def fund_rank(
        self,
        *,
        fund_type: int = 0,
        sort_column: str = "SYL_1N",
        sort: str = "desc",
        page: int = 1,
        size: int = 20,
        company_id: str = "",
        topic: str = "",
        risk_level: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        """基金排行榜。返回 ``{"total","page","size","rows"}``。

        ``sort_column`` 取 ``SYL_1N`` 近1年 / ``SYL_M`` 近1月 / ``SYL_Q``
        近3月 / ``SYL_Z`` 成立至今 / ``RDZF`` 日涨幅 / ``DWJZ`` 最新净值；
        ``fund_type`` 取 ``0`` 全部 / ``25`` 股票 / ``27`` 混合 / ``35`` 货币。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_rank(
                fund_type=fund_type,
                sort_column=sort_column,
                sort=sort,
                page=page,
                size=size,
                company_id=company_id,
                topic=topic,
                risk_level=risk_level,
                **extra,
            ),
        )

    def fund_snapshot(self, codes: Sequence[str] | str) -> list[dict[str, Any]]:
        """批量基金实时快照（净值 + 盘中估值），一次请求多只。

        ``fund_estimate`` 依赖的 ``fundgz`` 接口已下线，本方法是官方替代
        路径；``est_nav`` / ``est_pct`` / ``est_time`` 即估算净值 /
        估算涨跌% / 估算时间。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_snapshot(list(codes))
        )

    def fund_nav_history_mob(
        self, code: str, *, page: int = 1, size: int = 49
    ) -> list[dict[str, Any]]:
        """移动端历史净值（字段比 :meth:`fund_nav_history` 更全）。

        额外提供 ``nav_type`` / ``rate`` / ``cum_return``（累计收益率）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_nav_history_mob(code, page=page, size=size),
        )

    def fund_detail(self, code: str) -> dict[str, Any]:
        """基金详情（风险等级 / 业绩基准 / 投资策略 / 各类费用）。

        选基尽调核心字段：``risk_level`` / ``benchmark`` /
        ``invest_strategy`` / ``management_exp`` / ``trust_exp`` /
        ``sales_exp``。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_detail(code)
        )

    def fund_rating(
        self, code: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """基金历史评级（天天基金 / 招商 / 上证 / 嘉实等机构）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_rating(code, page=page, size=size),
        )

    def fund_yield_curve(
        self, code: str, *, index_code: str = "000300"
    ) -> list[dict[str, Any]]:
        """累计收益走势（基金 vs 指数 vs 同类），超额收益分析基础。

        ``index_code`` 可选 ``000300`` 沪深300 / ``000001`` 上证 /
        ``399001`` 深成 / ``399006`` 创业板 / ``000905`` 中证500。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_yield_curve(code, index_code=index_code),
        )

    def fund_rank_trend(self, code: str, *, range_: str = "n") -> list[dict[str, Any]]:
        """同类排名走势（每日同类排名与总数）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_rank_trend(code, range_=range_),
        )

    # -- 基金经理（移动端 JSON，替代 HTML 解析） ----------------------------- #
    def fund_manager_list(self, code: str) -> list[dict[str, Any]]:
        """基金经理列表（现任 + 离任）。

        稳定 JSON 版，替代 :meth:`fund_manager`（``fundf10`` HTML
        best-effort 正则解析，页面改版即静默失效）。用 ``is_in_office``
        区分现任 / 离任。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_manager_list(code)
        )

    def fund_manager_profile(self, mgrid: str) -> dict[str, Any]:
        """基金经理档案（简历 / 投资理念 / 任职基金 / 获奖）。

        ``mgrid`` 来自 :meth:`fund_manager_list` 的 ``mgrid`` 字段。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_manager_profile(mgrid)
        )

    def fund_manager_yield(
        self, mgrid: str, *, range_: str = "y"
    ) -> list[dict[str, Any]]:
        """基金经理业绩走势（任职收益 vs 同类 vs 指数）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_manager_yield(mgrid, range_=range_),
        )

    def fund_manager_eval(self, mgrid: str) -> dict[str, Any]:
        """基金经理业绩评价（夏普 / 最大回撤 / 胜率 / 波动率 / 超额）。

        ``sharp_1y`` / ``max_ret_1y`` / ``win_pct_1y`` / ``stddev_1y``
        可直接构成量化经理打分卡。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_manager_eval(mgrid)
        )

    def fund_manager_style(self, mgrid: str) -> dict[str, Any]:
        """基金经理持仓风格画像（重仓股 / 风格标签 / 子风格分布）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_manager_style(mgrid)
        )

    # -- 基金公司 / 搜索 --------------------------------------------------- #
    def fund_companies(self) -> list[dict[str, Any]]:
        """全部基金公司列表（约 160 家，一次拉全）。

        ``company_id`` 是 :meth:`fund_company_archives` /
        :meth:`fund_company_funds` / :meth:`fund_rank` 的过滤入参。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_companies()
        )

    def fund_company_archives(self, company_id: str) -> dict[str, Any]:
        """基金公司概况（成立时间 / 资产总规模 / 注册地 / 官网 / 人数）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.fund_company_archives(company_id)
        )

    def fund_company_funds(
        self,
        company_id: str,
        *,
        fund_type: str = "all",
        page: int = 1,
        size: int = 50,
        sort_field: str = "DWJZ",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        """公司旗下基金列表（含各类区间收益，便于横向比较）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_company_funds(
                company_id,
                fund_type=fund_type,
                page=page,
                size=size,
                sort_field=sort_field,
                sort_dir=sort_dir,
            ),
        )

    def fund_company_scale(
        self, company_id: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """公司旗下基金总规模变动（份额 / 净值资产，按报告期）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_company_scale(company_id, page=page, size=size),
        )

    def fund_company_base_info(self, company_id: str) -> dict[str, Any]:
        """公司画像（旗下基金分类统计 + 主题热度）。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_company_base_info(company_id),
        )

    def fund_search(
        self, key: str, *, order_type: int = 2, page: int = 1, size: int = 10
    ) -> dict[str, Any]:
        """按名称 / 代码模糊搜索基金。返回 ``{"total","page","size","rows"}``。"""
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_search(
                key, order_type=order_type, page=page, size=size
            ),
        )

    # -- 资讯 / 研报 / 调研（niuniu 审计缺口补全） ----------------------------- #
    def news_financial(self, *, page: int = 1, size: int = 30) -> list[dict[str, Any]]:
        """财经快讯头条（对标 niuniu ``/api/news/financial`` 新浪财经头条）。

        返回 ``[{"id","title","content","summary","time","url","labels"}, ...]``，
        按时间倒序。后端为东财 ``newsapi.eastmoney.com`` 快讯接口。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.news_financial(page=page, size=size),
        )

    def research_reports(
        self,
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        begin: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """个股研报（对标 niuniu ``/api/news/research/{code}`` 同花顺研报）。

        ``symbol`` 为空取全市场最新研报。返回评级 / 机构 / 分析师 /
        本年末与下一年末 EPS·PE 预测。后端为东财 ``reportapi.eastmoney.com``。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.research_reports(
                symbol, page=page, size=size, begin=begin, end=end
            ),
        )

    def research_visits(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """机构调研记录（对标 niuniu ``/api/news/research-visits/{code}`` 巨潮调研）。

        返回 ``[{"code","name","date","org","type","summary","content"}, ...]``，
        按调研日期倒序。报表名 ``RPT_ORG_SURVEY_DET`` 为 best-effort。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.research_visits(symbol, page=page, size=size),
        )

    # -- 股东户数 / 十大流通股东（接线提升） ---------------------------------- #
    def free_holders(self, symbol: str, *, size: int = 10) -> list[dict[str, Any]]:
        """十大流通股东（``shareholders`` 会话方法的门面提升）。

        返回 ``[{"holder_name","holder_rank","hold_num","hold_ratio","change",
        "holder_type","shares_type","report_date"}, ...]``。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.shareholders(symbol, size=size),
        )

    def holder_num(self, symbol: str, *, size: int = 10) -> list[dict[str, Any]]:
        """股东户数变动历史。

        返回 ``[{"holder_num","prev_holder_num","change","change_ratio",
        "end_date","avg_market_cap","avg_hold_num","notice_date"}, ...]``。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession, lambda sess: sess.holder_num(symbol, size=size)
        )

    # -- efinance 剩余项：批量 / 全量枚举 ------------------------------------ #
    def fund_base_info_multi(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """批量基金基础信息（对标 efinance ``get_base_info_muliti``）。

        逐只取基础信息，单只失败不阻断批量。
        """
        from ..web.facade import WebQuoteSession

        return self._with(
            WebQuoteSession,
            lambda sess: sess.fund_base_info_multi(list(codes)),
        )

    def bond_all_base_info(self) -> list[dict[str, Any]]:
        """全市场债券基础信息（对标 efinance ``bond.get_all_base_info``）。

        通过 push2 全量列表枚举可转债（沪 128 / 深 80）。
        """
        from ..web.facade import WebQuoteSession

        return self._with(WebQuoteSession, lambda sess: sess.bond_all_base_info())

    # -- 统一响应形态 --------------------------------------------------------- #
    def query(self, method: str, *args: Any, **kwargs: Any) -> ApiResponse:
        """以统一响应形态调用本门面的任意方法。

        形态对齐业界通行的 ``success / error / data / extra`` 四要素：
        成功（含合法空结果）``success=True``；任何异常转为
        ``success=False``（保留 tstdx 错误码与上下文）。适用于需要
        「永不抛异常」边界的场景（HTTP 网关 / 脚本批处理 / UI 展示）。

        Parameters
        ----------
        method:
            本类方法名，如 ``"quotes"`` / ``"bars"`` / ``"search_symbols"``。
        *args, **kwargs:
            透传给目标方法。

        Returns
        -------
        :class:`~tstdx.facade.response.ApiResponse`
        """
        from .response import from_result

        fn = getattr(self, str(method), None)
        if fn is None or not callable(fn):
            return from_result(
                TdxError(f"未知方法: {method!r}", context={"api": "UnifiedQuoteAPI"})
            )
        try:
            return from_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 —— 统一边界兜底
            return from_result(exc)

    # -- 板块 ---------------------------------------------------------------- #
    @staticmethod
    def industry_boards() -> list[dict[str, Any]]:
        """行业板块列表（新浪，含领涨股）。"""
        from ..web.boards import SinaIndustryBoardSource

        return UnifiedQuoteAPI._with(SinaIndustryBoardSource, lambda src: src.fetch_boards())

    @staticmethod
    def board_list(board: str = "concept") -> list[dict[str, Any]]:
        """新浪板块列表（concept 概念/region 地域/industry 新版行业，含领涨股）。"""
        from ..web.boards import SinaBoardListSource

        return UnifiedQuoteAPI._with(SinaBoardListSource, lambda src: src.fetch_boards(board))

    @staticmethod
    def board_members(
        node: str, *, page_size: int = 100, max_pages: int | None = None
    ) -> list[Quote]:
        """板块成分行情（新浪 node 分页）。"""
        from ..web.boards import SinaBoardMemberSource

        return UnifiedQuoteAPI._with(
            SinaBoardMemberSource,
            lambda src: src.fetch_members(node, page_size=page_size, max_pages=max_pages),
        )

    @staticmethod
    def board_rank(
        board: str = "industry", *, limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块排行（腾讯，industry/concept/region，含领涨股）。"""
        from ..web.boards import TencentBoardRankSource

        return UnifiedQuoteAPI._with(
            TencentBoardRankSource, lambda src: src.fetch_boards(board, limit=limit, page=page)
        )

    @staticmethod
    def em_boards(
        board: str = "industry", *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块列表（东财，industry/concept/region）。"""
        from ..web.boards import EastmoneyBoardSource

        return UnifiedQuoteAPI._with(
            EastmoneyBoardSource, lambda src: src.fetch_boards(board, limit=limit, page=page)
        )

    @staticmethod
    def em_board_members(
        board_code: str, *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块成分（东财，board_code 形如 BK0475）。"""
        from ..web.boards import EastmoneyBoardSource

        return UnifiedQuoteAPI._with(
            EastmoneyBoardSource, lambda src: src.fetch_members(board_code, limit=limit, page=page)
        )

    @staticmethod
    def stock_boards(symbol: str) -> list[dict[str, Any]]:
        """个股所属板块（东财 slist，行业/概念/地域全量）。"""
        from ..web.boards import EastmoneyBoardSource

        return UnifiedQuoteAPI._with(
            EastmoneyBoardSource, lambda src: src.fetch_stock_boards(symbol)
        )

    @staticmethod
    def ipo_calendar(
        *, apply_date: str = "", page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """IPO 申购日历（东财 datacenter-web，申购日期降序）。

        ``apply_date``（``YYYY-MM-DD``）非空即「今日申购」视图。
        未定价/未上市字段为 ``None``。
        """
        from ..web.corporate import EastmoneyIpoSource

        return UnifiedQuoteAPI._with(
            EastmoneyIpoSource,
            lambda src: src.fetch_ipo(apply_date=apply_date, page=page, size=size),
        )

    @staticmethod
    def big_order_flow(symbol: str) -> dict[str, Any] | None:
        """个股大单流向（五档资金分档明细；单只语义别名）。"""
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.big_order_flow(symbol)

    @staticmethod
    def stock_changes(
        types: Sequence[int] = (), *, page: int = 1, size: int = 50
    ) -> list[dict[str, Any]]:
        """盘中异动池（16 类异动；交易时段实时，非交易时段为空列表）。"""
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.stock_changes(types, page=page, size=size)

    @staticmethod
    def hot_rank(*, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """股吧个股人气榜（rank/symbol；行情请以 symbol 回查 quotes）。"""
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.hot_rank(page=page, size=size)

    @staticmethod
    def sector_flow(
        board: str = "industry", *, sort: str = "main_net", limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块资金流排行（东财 push2 clist；金额单位元，仅 web 路由）。

        ``board``: ``industry`` 行业（默认）/ ``concept`` 概念 / ``region``
        地域；``sort``: ``main_net``（默认）/ ``main_ratio`` / ``change_pct`` /
        ``amount`` 等。资金流排序自动附带主力/超大单/大单/中单/小单字段。
        """
        from ..web.facade import WebQuoteSession

        return WebQuoteSession.sector_flow(board, sort=sort, limit=limit, page=page)

    # -- 历史行情（web 双源） ------------------------------------------------- #
    @staticmethod
    def history(
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "qfq",
        source: str = "sina",
    ) -> list[Bar]:
        """历史 K 线（新浪/东财双源；东财含成交额与复权选项）。

        复权口径：新浪只提供原始价——``adjust != ""`` 时自动路由到东财源
        （深审 M9：旧实现静默把复权请求偷换成不复权数据）。
        """
        from ..web.history import EastmoneyHistoryKlineSource, SinaHistoryKlineSource

        if source == "eastmoney" or adjust not in ("", None):
            return UnifiedQuoteAPI._with(
                EastmoneyHistoryKlineSource,
                lambda src: src.fetch_bars(symbol, period=period, count=count, adjust=adjust),
            )
        return UnifiedQuoteAPI._with(
            SinaHistoryKlineSource,
            lambda src: src.fetch_bars(symbol, period=period, count=count, adjust=adjust),
        )

    def rates(self) -> list[dict[str, Any]]:
        """中国银行外汇牌价（仅 web 路由）。"""
        from ..web.facade import web_session

        return self._with(lambda: web_session("boc"), lambda sess: sess.rates())

    # ------------------------------------------------------------------ #
    # 扩展市场 / 商品期权
    # ------------------------------------------------------------------ #
    def ex_bars(
        self, symbol: str, *, period: str = "day", count: int = 320, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """扩展市场 K 线（港股 / 美股 / 期货 / 外汇）。

        W12：7727 扩展市场为 tdx 协议能力，web 无对应实现；显式传
        ``route="web"/"local"`` → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        from ..client import ExMarketClient

        return self._with(
            lambda: ExMarketClient(timeout=self.timeout),
            lambda client: client.ex_bars(sym, period=period, count=count, as_format="dict"),
        )

    def ex_quotes(self, symbol: str, *, route: Route | None = None) -> dict[str, Any]:
        """扩展市场实时行情。

        W12：web 无对应实现，仅 tdx；显式传 ``route="web"/"local"`` →
        ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        from ..client import ExMarketClient

        return self._with(
            lambda: ExMarketClient(timeout=self.timeout),
            lambda client: client.ex_quote(sym, as_format="dict"),
        )

    def goods_bars(
        self, symbol: str, *, period: str = "day", count: int = 320, route: Route | None = None
    ) -> list[dict[str, Any]]:
        """商品 / 期权 K 线。

        W12：web 无对应实现，仅 tdx（7727）；显式传 ``route="web"/"local"``
        → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        from ..client import GoodsClient

        return self._with(
            lambda: GoodsClient(timeout=self.timeout),
            lambda client: client.goods_bars(sym, period=period, count=count, as_format="dict"),
        )

    def goods_quotes(self, symbol: str, *, route: Route | None = None) -> dict[str, Any]:
        """商品 / 期权实时行情。

        W12：web 无对应实现，仅 tdx（7727）；显式传 ``route="web"/"local"``
        → ``ValueError``。
        """
        self._require_tdx_route(route)
        sym = normalize_symbol(symbol)
        from ..client import GoodsClient

        return self._with(
            lambda: GoodsClient(timeout=self.timeout),
            lambda client: client.goods_quote(sym, as_format="dict"),
        )
