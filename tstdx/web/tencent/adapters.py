# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""腾讯系 HTTP Web 行情适配器（实时行情 / K 线 / 分钟线 / 分时 / 外部市场）。

从 ``tstdx.web.adapters`` 和 ``tstdx.web.adapters_ext`` 拆分归组而来，
继承链全部在本 provider 内闭合：

- :class:`TencentSource` → :class:`TencentExternalSource` →
  :class:`HkSource` / :class:`UsSource`
- :class:`KlineSource` / :class:`MinuteKlineSource` / :class:`MinuteSource`
  均直接继承 :class:`~tstdx.web.base.BaseWebSource`。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

from ...diagnostics import WarningCode, record_warning
from ...domain.models import Bar, Level, MinutePoint, Quote
from ...errors import ReadTimeout, SourceDeprecated, WebSourceError
from .._paginate import (
    decode_kline_payload,
    fetch_bars_paged,
    rows_to_bars,
    warn_amount_all_zero,
)
from ..base import (
    BaseWebSource,
    to_tencent_symbol,
)
from ..base import (
    num_f as _f,
)
from ..base import (
    num_i as _i,
)
from ..limits import TENCENT_KLINE_MAX
from ..sources import HK, KLINE, MINUTE, MINUTE_KLINE, TENCENT, US

logger = logging.getLogger(__name__)

__all__ = [
    "TencentSource",
    "TencentExternalSource",
    "HkSource",
    "UsSource",
    "KlineSource",
    "MinuteKlineSource",
    "MinuteSource",
    "DEFAULT_MAX_PAGES",
]

#: ``fetch_all`` 未显式给 ``max_pages`` 时的硬上限（P2 #1）：
#: 分页终止不再完全依赖服务端空页，防止失控翻页。
DEFAULT_MAX_PAGES = 100

# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #


def _levels(
    fields: Sequence[str], start: int, count: int = 5, vol_first: bool = True
) -> list[Level]:
    """从交错数组解析盘口档位。

    Parameters
    ----------
    vol_first:
        True 表示排列为 ``量,价,量,价...``（新浪）；False 表示 ``价,量``（东财）。
    """
    out: list[Level] = []
    for i in range(count):
        a = start + i * 2
        b = a + 1
        if b >= len(fields):
            break
        va, vb = fields[a], fields[b]
        price, vol = (_f(vb), _i(va)) if vol_first else (_f(va), _i(vb))
        out.append(Level(price=price, volume=vol))
    return out


# --------------------------------------------------------------------------- #
# 腾讯：实时行情
# --------------------------------------------------------------------------- #
class TencentSource(BaseWebSource):
    """腾讯财经实时行情（含港股、期货）。"""

    BASE = "https://qt.gtimg.cn/q="

    @property
    def source_name(self) -> str:
        return TENCENT

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(to_tencent_symbol(s) for s in symbols)
        return f"{self.BASE}{codes}"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        # 美股代码含大写（如 usAAPL），故代码段允许大写
        pattern = re.compile(r'v_([a-zA-Z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split("~")
            if len(f) < 30:
                raise WebSourceError(
                    f"腾讯响应字段不足: {len(f)} < 30",
                    context={"code": raw_code, "source": TENCENT},
                )
            extra: dict[str, Any] = {"name": f[1]}
            if len(f) > 30:
                extra["time"] = f[30]
            if len(f) > 32:
                extra["pct_change"] = _f(f[32])
            if len(f) > 45:
                # 深审 M30：腾讯接口市值单位为**亿**——统一换算为元（与东财
                # f116/f117 同口径），下游跨源聚合/比较不再跳变数量级。
                extra["float_market_cap"] = _f(f[44]) * 1e8
                extra["total_market_cap"] = _f(f[45]) * 1e8
            if len(f) > 46:
                extra["pb"] = _f(f[46])
            if len(f) > 48:
                extra["limit_up"] = _f(f[47])
                extra["limit_down"] = _f(f[48])
            if len(f) > 49:
                extra["volume_ratio"] = _f(f[49])
            if len(f) > 38:
                extra["turnover_rate"] = _f(f[38])
            if len(f) > 39:
                extra["pe"] = _f(f[39])

            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[4]),
                open=_f(f[5]),
                volume=_i(f[6]),
                high=_f(f[33]) if len(f) > 33 else _f(f[3]),
                low=_f(f[34]) if len(f) > 34 else _f(f[3]),
                amount=_f(f[37]) if len(f) > 37 else 0.0,
                bid=_levels(f, 9, 5, vol_first=True),
                ask=_levels(f, 19, 5, vol_first=True),
                extra=extra,
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "腾讯返回空数据（接口可能已改版）",
                context={"source": TENCENT, "sample": text[:200]},
            )
        return quotes

    # -- 全市场（getBoardRankList 枚举代码 + qt.gtimg.cn 批量行情） ------------ #
    #: 节点 → 排行接口 board_code（对齐新浪 ``fetch_all`` 的 node 语义）
    RANK_BOARDS: dict[str, str] = {
        "hs_a": "aStock",  # 沪深 A 股
        "cyb": "cyb",  # 创业板
    }
    #: 排行接口单页条数上限（服务端上限 200）
    RANK_PAGE_MAX = 200
    #: qt.gtimg.cn 单请求批量条数（实测 120 路全量返回，保守取 100）
    TENCENT_BATCH = 100
    #: 腾讯排行接口（新版 getBoardRankList，offset/count 分页 JSON）
    RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"

    def fetch_all(
        self,
        node: str = "hs_a",
        page_size: int = 200,
        max_pages: int | None = None,
    ) -> list[Quote]:
        """分页拉取腾讯全市场行情摘要（U5）。

        全程单源（腾讯，非兜底），两步走：
        1. ``getBoardRankList`` 排行接口按 ``offset/count`` 枚举证券代码；
        2. 按 :attr:`TENCENT_BATCH` 切批走 ``qt.gtimg.cn`` 批量行情接口，
           复用 :meth:`fetch` 的限流 / 重试 / 归一化，返回完整 Quote。

        与新浪 ``fetch_all`` 的 ``node`` 语义对齐：``hs_a`` 沪深 A 股
        （默认）/ ``cyb`` 创业板；其余节点显式报错并给出可选值。
        """
        effective_max_pages = DEFAULT_MAX_PAGES if max_pages is None else max(1, int(max_pages))
        board = self.RANK_BOARDS.get(str(node).lower())
        if board is None:
            raise WebSourceError(
                f"腾讯全市场节点不支持 {node!r}；可选: {sorted(self.RANK_BOARDS)}",
                context={"source": TENCENT, "node": node},
            )
        codes = self._fetch_codes(board, page_size=page_size, max_pages=effective_max_pages)
        if not codes:
            raise SourceDeprecated(
                "腾讯全市场返回空代码表", context={"source": TENCENT, "node": node}
            )

        out: list[Quote] = []
        failed_codes: list[str] = []
        for i in range(0, len(codes), self.TENCENT_BATCH):
            batch = codes[i : i + self.TENCENT_BATCH]
            try:
                out.extend(self.fetch(batch))
            except (WebSourceError, ReadTimeout):
                # A4：单批失败先记录，最后统一退避重试一次；全部失败才判不可用
                failed_codes.extend(batch)
        if failed_codes:
            time.sleep(0.5)  # 单次退避，避免瞬断/限流连锁
            retried_failed: list[str] = []
            for i in range(0, len(failed_codes), self.TENCENT_BATCH):
                retry_batch = failed_codes[i : i + self.TENCENT_BATCH]
                try:
                    out.extend(self.fetch(retry_batch))
                except (WebSourceError, ReadTimeout):
                    retried_failed.extend(retry_batch)
            if retried_failed:
                # A4：不静默丢数据 —— 结束时一次性 UserWarning，列出失败样本
                record_warning(
                    WarningCode.WEB_TENCENT_BATCH_FAILED,
                    "腾讯全市场 fetch_all 单批重试后仍失败 "
                    f"{len(retried_failed)}/{len(codes)} 只代码"
                    f"（样本 {retried_failed[:5]}），本次结果可能不完整",
                    stacklevel=2,
                )
        if not out:
            raise SourceDeprecated(
                "腾讯全市场拉取全部失败",
                context={"source": TENCENT, "node": node, "codes": len(codes)},
            )
        return out

    def _fetch_codes(
        self, board: str, *, page_size: int = 200, max_pages: int = DEFAULT_MAX_PAGES
    ) -> list[str]:
        """按排行接口 offset 分页枚举证券代码（去重保序，单页失败即停）。"""
        page_size = max(1, min(int(page_size), self.RANK_PAGE_MAX))
        codes: list[str] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(max_pages):
            url = (
                f"{self.RANK_URL}?board_code={board}&sort_type=price&direct=down"
                f"&offset={offset}&count={page_size}"
            )
            try:
                text = self._request_text(url, encoding="utf-8")
                rows = self._parse_rank_codes(text)
            except (WebSourceError, ReadTimeout) as exc:
                # 单页失败（瞬断/限速/结构变更）→ 停止枚举，返回已收代码（同新浪 fetch_all
                # 语义）。但"返回已收代码"不能是无声的：调用方拿到的是**被截断的**代码表，
                # 少了这一格信号就与 ``docs/errors.md`` §一之四 的口径相悖——新浪缺页时发
                # ``WEB_SINA_PAGES_MISSING``，而腾讯过去只在**批量行情**阶段发告警，
                # 枚举阶段截断一声不吭（第 29 轮）。
                record_warning(
                    WarningCode.WEB_TENCENT_PAGES_MISSING,
                    f"腾讯全市场代码枚举在 offset={offset} 处分页失败"
                    f"（board={board}，已收 {len(codes)} 条，原因 {exc}）："
                    "代码表被截断，本次全市场不完整",
                    stacklevel=2,
                )
                break
            for c in rows:
                if c not in seen:
                    seen.add(c)
                    codes.append(c)
            if len(rows) < page_size:
                break  # 末页（不足一页）→ 终止
            offset += page_size
        return codes

    @staticmethod
    def _parse_rank_codes(text: str) -> list[str]:
        """解析 getBoardRankList JSON，抽出 ``code`` 列（非法体 → SourceDeprecated）。"""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "腾讯排行接口返回非 JSON",
                context={"source": TENCENT, "sample": text[:200]},
                cause=exc,
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("rank_list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise SourceDeprecated(
                "腾讯排行接口返回结构异常（缺 rank_list）",
                context={"source": TENCENT, "sample": text[:160]},
            )
        return [str(r.get("code", "")) for r in rows if isinstance(r, dict) and r.get("code")]


# --------------------------------------------------------------------------- #
# 港股 / 美股（腾讯 qt.gtimg.cn 外部市场布局）
# --------------------------------------------------------------------------- #
class TencentExternalSource(TencentSource):
    """腾讯外部市场（港股 / 美股）基类。

    腾讯对港股返回 ``100`` 前缀、美股返回 ``200`` 前缀的字段布局，与 A 股
    的 93 字段布局**核心 OHLCV 索引一致**（[3]价 / [4]昨收 / [5]开 /
    [31]涨跌 / [32]涨跌% / [33]高 / [34]低 / [37]额），但**不携带五档盘口**，
    且 [9..18] / [19..28] 不是买卖盘。A 股版 :class:`TencentSource` 直接把
    这些位置当 bid/ask 解析会得到垃圾值，故外部市场用本类独立解析。

    成交量单位为「股」（与全局契约一致，无需 ×100）；价格 / 成交额以原生
    币种计（港股 HKD、美股 USD），通过 ``extra["currency"]`` 透出，库不做
    汇率换算。
    """

    #: 子类覆盖：原生币种
    CURRENCY = "CNY"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        # 美股代码含大写（如 usAAPL），故代码段允许大写
        pattern = re.compile(r'v_([a-zA-Z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split("~")
            if len(f) < 38:
                raise WebSourceError(
                    f"腾讯外部市场响应字段不足: {len(f)} < 38",
                    context={"code": raw_code, "source": self.source_name},
                )
            extra: dict[str, Any] = {"name": f[1], "currency": self.CURRENCY}
            if len(f) > 30:
                extra["time"] = f[30]
            if len(f) > 32:
                extra["pct_change"] = _f(f[32])
            if len(f) > 38:
                extra["turnover_rate"] = _f(f[38])
            if len(f) > 39:
                extra["pe"] = _f(f[39])
            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[4]),
                open=_f(f[5]),
                volume=_i(f[6]),
                high=_f(f[33]) if len(f) > 33 else _f(f[3]),
                low=_f(f[34]) if len(f) > 34 else _f(f[3]),
                amount=_f(f[37]) if len(f) > 37 else 0.0,
                bid=[],
                ask=[],
                extra=extra,
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "腾讯外部市场返回空数据（接口可能已改版）",
                context={"source": self.source_name, "sample": text[:200]},
            )
        return quotes


class HkSource(TencentExternalSource):
    """港股实时行情（腾讯协议，代码前缀 ``hk``）。

    .. note::
        旧实现直接复用 A 股解析，会把港股字段 [9..18] 误当五档买卖盘，
        得到垃圾盘口。本实现改用外部市场布局，已修正。
    """

    CURRENCY = "HKD"

    @property
    def source_name(self) -> str:
        return HK

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(s if s.lower().startswith("hk") else f"hk{s}" for s in symbols)
        return f"{self.BASE}{codes}"


class UsSource(TencentExternalSource):
    """美股实时行情（腾讯协议，代码前缀 ``us``）。

    例：``usAAPL`` / ``us00700``（港股代码在美股二次上市）。
    """

    CURRENCY = "USD"

    @property
    def source_name(self) -> str:
        return US

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(s if s.lower().startswith("us") else f"us{s}" for s in symbols)
        return f"{self.BASE}{codes}"


# --------------------------------------------------------------------------- #
# 日 K 线
# --------------------------------------------------------------------------- #
class KlineSource(BaseWebSource):
    """日 K 线（腾讯 ifzq 接口）。"""

    BASE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
    encoding = "utf-8"  # 腾讯 ifzq JSON 为 UTF-8
    PERIODS = {
        "day": "day",
        "week": "week",
        "month": "month",
        "1min": "m1",
        "5min": "m5",
        "15min": "m15",
        "30min": "m30",
        "60min": "m60",
    }

    @property
    def source_name(self) -> str:
        return KLINE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        period_key = kwargs.get("period", "day")
        if period_key not in self.PERIODS:
            # P1 #13: 未知周期显式报错，不再静默回退 day（错误数据比报错更危险）
            raise ValueError(f"未知 K 线周期 {period_key!r}；可选: {sorted(self.PERIODS)}")
        period = self.PERIODS[period_key]
        count = int(kwargs.get("count", 320))
        # v5 PG4：实测超限静默截断（2000→640）甚至整体空返回（4000→null），
        # 钳到实测安全上限并告警——静默截断比明示降级更危险。
        # C9：单次请求仍钳制；需要更长历史时由 fetch_bars 按日期分段拉全量
        if count > TENCENT_KLINE_MAX:
            logger.warning(
                "腾讯 K 线 count=%d 超接口实测上限 %d，已钳制（fetch_bars 会自动分段取全量）",
                count,
                TENCENT_KLINE_MAX,
            )
            count = TENCENT_KLINE_MAX
        fq = kwargs.get("adjust", "qfq")
        end = str(kwargs.get("end") or "")  # C9：分段请求右边界（YYYY-MM-DD，可为空）
        param = f"{symbol},{period},,{end},{count},{fq}"
        return f"{self.BASE}?param={param}"

    def fetch_bars(
        self, symbol: str, *, period: str = "day", count: int = 320, adjust: str = "qfq"
    ) -> list[Bar]:
        """获取 K 线（返回 :class:`Bar`，非 :class:`Quote`）。

        ``symbol`` 可为裸代码（``600519``）或带前缀（``sh600519``）。

        C9（v5 PG8 落地）：``count`` 超过 :data:`TENCENT_KLINE_MAX` 时
        **自动按日期分段**请求并拼接去重，调用方拿到完整 ``count`` 根，
        不再被钳制截断。
        """
        symbol = to_tencent_symbol(symbol)  # 响应 data 键为腾讯格式，需先归一化

        def _fetch_page(seg: int, end: str | None) -> list[Bar]:
            url = self.build_url([symbol], period=period, count=seg, adjust=adjust, end=end)
            text = self._request_text(url, encoding="utf-8")
            return self.parse_bars(text, symbol)

        # v9 Q4-1：分页向前翻页逻辑上收至 _paginate.fetch_bars_paged（与 mkline 共用）
        return fetch_bars_paged(
            symbol=symbol,
            count=count,
            max_per_req=TENCENT_KLINE_MAX,
            fetch_page=_fetch_page,
            paging=True,
            empty_error="腾讯 K 线分段拉取全部失败",
            source=KLINE,
            log_label="腾讯 K 线",
        )

    def parse_bars(self, text: str, symbol: str) -> list[Bar]:
        """解析腾讯 fqkline 响应为 ``list[Bar]``。

        .. note::
            腾讯日 K（``day``）响应**不含成交额字段**，此时
            :class:`~tstdx.domain.models.Bar`.``amount`` 为 ``0.0``
            （不可得即置 0，不捏造）；分线（如 ``m5``）含 amount。
        """
        payload = decode_kline_payload(text, msg="K 线返回非 JSON", source=KLINE)
        data = (payload.get("data") or {}).get(symbol) or {}
        rows = data.get("qfqday") or data.get("day") or data.get("qfq") or []
        if not rows:
            # 有些响应把数组放在 data 下的一层
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], list):
                    rows = v
                    break
        # v9 Q4-1：行 → Bar 组装与按市场缩放上收至 _paginate（与 mkline 共用）
        bars = rows_to_bars(rows, symbol=symbol, with_amount=True)
        # A5：腾讯日 K / 分钟 K 接口对部分周期不返回 amount 字段，
        # 若整批 amount 恒为 0（区别于个别缺失），一次性 UserWarning，非逐行
        warn_amount_all_zero(bars, symbol)
        return bars

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        bars = self.parse_bars(text, to_tencent_symbol(symbols[0]))
        if not bars:
            return []
        last = bars[-1]
        return [
            Quote(
                code=to_tencent_symbol(symbols[0]),
                price=last.close,
                open=last.open,
                high=last.high,
                low=last.low,
                volume=last.volume,
                amount=last.amount,
            )
        ]


# --------------------------------------------------------------------------- #
# 分钟 K 线（腾讯）
# --------------------------------------------------------------------------- #

#: 规范周期拼写 → 腾讯 mkline 周期参数。本表只收 :data:`tstdx.domain.period
#: .CANONICAL_PERIODS` 的分钟档，且每一行都真的服务它键上写的那个周期
#: （``Nmin`` → ``mN``）。旧表另抄了 ``m1``/``m5``…五个别名键——别名归域内那份
#: 唯一词表管，公开面先规范再下发（第 19 轮，与 G13 同一条裁决）。
_MKLINE_PERIODS = {
    "1min": "m1",
    "5min": "m5",
    "15min": "m15",
    "30min": "m30",
    "60min": "m60",
}


def _mkline_period(period: str) -> str:
    """规范周期 → 腾讯 mkline 参数；未知值显式报错（P1 #13，不静默回退 m5）。"""
    try:
        return _MKLINE_PERIODS[period]
    except KeyError:
        raise ValueError(
            f"腾讯分钟 K 线不服务周期 {period!r}（这一面只有 {sorted(_MKLINE_PERIODS)}）"
        ) from None


class MinuteKlineSource(BaseWebSource):
    """分钟 K 线（腾讯 ifzq mkline 接口，**仅 A 股**）。

    .. note::
        该接口仅覆盖 A 股（``sh/sz/bj``）：港股 / 美股传入后腾讯返回
        ``code=-1`` 空数据，直接调用本源会抛出明确错误。港股 / 美股
        分钟 K 线请走 :class:`~tstdx.web.eastmoney.adapters.EastmoneyHistoryKlineSource`
        （东财 push2his，``hk`` secid=116.x、``us`` secid=105/106/107 探测）；
        :meth:`~tstdx.web.session.WebQuoteSession.klines` 已按市场自动路由。

        成交量单位：A 股腾讯返回「手」需 ``×100`` 到股。

        .. warning::
            腾讯 mkline 响应**不含成交额字段**，故 :class:`~tstdx.domain.models.Bar`
            的 ``amount`` 恒为 ``0.0``（合约语义：不可得即置 0，绝不捏造）。
    """

    BASE = "https://ifzq.gtimg.cn/appstock/app/kline/"
    encoding = "utf-8"  # 腾讯 ifzq JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return MINUTE_KLINE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        period = _mkline_period(kwargs.get("period", "5min"))
        count = int(kwargs.get("count", 240))
        # v5 PG4：与 fqkline 同源实测口径——超限静默截断/空返回，钳制+告警
        if count > TENCENT_KLINE_MAX:
            logger.warning(
                "腾讯分钟 K 线 count=%d 超接口实测上限 %d，已钳制",
                count,
                TENCENT_KLINE_MAX,
            )
            count = TENCENT_KLINE_MAX
        return f"{self.BASE}mkline?param={symbol},{period},,,{count}"

    def fetch_bars(
        self,
        symbol: str,
        *,
        period: str = "5min",
        count: int = 240,
    ) -> list[Bar]:
        """获取分钟 K 线。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` / ``sh600519`` / ``hk00700`` / ``usAAPL`` 均可，
            但 hk/us 分钟 K 线腾讯不支持，将抛出明确错误）。
        period:
            ``1min`` / ``5min`` / ``15min`` / ``30min`` / ``60min``。
        count:
            返回根数（默认 240 = 一个交易日 5 分钟线）。
        """
        symbol = to_tencent_symbol(symbol)

        def _fetch_page(seg: int, end: str | None) -> list[Bar]:
            del seg, end  # mkline 不支持 end 翻页，超限由 build_url 钳制并告警
            url = self.build_url([symbol], period=period, count=count)
            text = self._request_text(url, encoding="utf-8")
            return self.parse_bars(text, symbol, period=period)

        # v9 Q4-1：与 fqkline 共用的分页拉取器（paging=False：单请求语义逐字保留）
        return fetch_bars_paged(
            symbol=symbol,
            count=count,
            max_per_req=TENCENT_KLINE_MAX,
            fetch_page=_fetch_page,
            paging=False,
            empty_error=None,
            source=MINUTE_KLINE,
            log_label="腾讯分钟 K 线",
        )

    def parse_bars(self, text: str, symbol: str, *, period: str = "5min") -> list[Bar]:
        payload = decode_kline_payload(text, msg="分钟 K 线返回非 JSON", source=MINUTE_KLINE)
        code = payload.get("code")
        if code != 0:
            # 腾讯 mkline 对港股/美股返回 code=-1 空数据：分钟 K 线该源不支持
            if symbol[:2].lower() in ("hk", "us"):
                raise WebSourceError(
                    f"腾讯 mkline 不提供港股/美股分钟 K 线（symbol={symbol} 返回 "
                    f"code={code}）。可改用 sess.minute('{symbol}') 取当日分时，"
                    f"或 sess.klines('{symbol}', period='day') 取日 K。",
                    context={"source": MINUTE_KLINE, "symbol": symbol},
                )
            raise SourceDeprecated(
                f"分钟 K 线返回错误码 {code}",
                context={"source": MINUTE_KLINE},
            )
        data = payload.get("data") or {}
        node = data.get(symbol) or {}
        key = _mkline_period(period)
        rows = node.get(key) or node.get("mkline") or []
        if not rows:
            # 布局兜底：部分响应把行情数组直接放在 data 层
            rows = data.get(key) or data.get("mkline") or []
        if not rows and symbol[:2].lower() in ("hk", "us"):
            raise WebSourceError(
                f"腾讯 mkline 未返回港股/美股分钟 K 线数据（symbol={symbol}）。"
                f"可改用 sess.minute('{symbol}') 取当日分时。",
                context={"source": MINUTE_KLINE, "symbol": symbol},
            )
        # 成交量单位按市场区分：A 股腾讯返回「手」需 ×100 到股；
        # 港股/美股（若未来放开）直接为「股」不再缩放。
        # v9 Q4-1：行 → Bar 组装与按市场缩放上收至 _paginate（与 fqkline 共用；
        # with_amount=False：mkline 响应不含成交额字段，amount 恒 0.0）
        return rows_to_bars(rows, symbol=symbol, with_amount=False)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Bar]:
        return self.parse_bars(
            text, to_tencent_symbol(symbols[0]), period=kwargs.get("period", "5min")
        )


# --------------------------------------------------------------------------- #
# 当日分时（腾讯）
# --------------------------------------------------------------------------- #
class MinuteSource(BaseWebSource):
    """当日分时（腾讯 minute/query 接口）。

    返回 ``list[MinutePoint]``；接口返回的量/额为**累计值**，
    解析时逐点差分还原为分钟增量。
    """

    BASE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    encoding = "utf-8"  # 腾讯 minute/query JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return MINUTE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        return f"{self.BASE}?code={symbol}"

    def fetch_minute(self, symbol: str) -> list[MinutePoint]:
        """获取当日 1 分钟分时序列。"""
        symbol = to_tencent_symbol(symbol)
        url = self.build_url([symbol])
        text = self._request_text(url, encoding="utf-8")
        return self.parse_minute(text, symbol)

    def parse_minute(self, text: str, symbol: str) -> list[MinutePoint]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "分时返回非 JSON",
                context={"source": MINUTE, "sample": text[:200]},
                cause=exc,
            ) from exc
        if payload.get("code") != 0:
            raise SourceDeprecated(
                f"分时返回错误码 {payload.get('code')}",
                context={"source": MINUTE},
            )
        node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
        rows = node.get("data") or []
        date = str(node.get("date", ""))
        out: list[MinutePoint] = []
        prev_vol = 0.0
        prev_amt = 0.0
        for r in rows:
            parts = str(r).split()
            # W6: 守卫与消费宽度对齐——本行消费 parts[0..3]（累计额在第 4 列），
            # 旧守卫 ``< 3`` 会让恰好 3 列的行在 parts[3] 处 IndexError
            if len(parts) < 4:
                continue
            hhmm = parts[0]
            price = _f(parts[1])
            cum_vol = _f(parts[2]) * 100.0  # 手 → 股
            cum_amt = _f(parts[3])  # 已是元
            out.append(
                MinutePoint(
                    time=f"{date} {hhmm[:2]}:{hhmm[2:4]}" if date else hhmm,
                    price=price,
                    volume=int(max(cum_vol - prev_vol, 0)),
                    avg_price=0.0,
                    amount=max(cum_amt - prev_amt, 0.0),
                )
            )
            prev_vol, prev_amt = cum_vol, cum_amt
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[MinutePoint]:
        return self.parse_minute(text, to_tencent_symbol(symbols[0]))
