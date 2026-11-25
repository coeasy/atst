# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical query contracts: :class:`QuerySpec`, currentness and the budget.

P2-1：本模块只装「契约」——请求形状、入参校验、当期性口径与执行预算。规划
（channel 选择、auto 解析、plan 编译）在 :mod:`atst.query.planning`，数据身份在
:mod:`atst.query.fingerprint`；三者由 :mod:`atst.query` 包出口统一对外。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Any, Final

from ..domain.period import normalize_bar_period
from ..domain.symbol import normalize_symbol, parse_symbol
from ..errors import ReadTimeout, SymbolError, ValidationError
from ..providers import PROVIDERS, resolve_capability_provider

__all__ = [
    "CurrentnessMode",
    "ExecutionBudget",
    "QuerySpec",
    "EXECUTED_OPTIONS",
    "REJECTED_OPTIONS",
    "SPEC_INT_FIELDS",
]


#: 直连执行面上没有对象可作用的策略键。设置它们必须当场失败，而不是被静默收下——
#: 一个"看起来生效"的开关比没有开关更糟（``max_age`` 就是静默收下然后无人消费）。
REJECTED_OPTIONS: dict[str, str] = {
    "allow_stale": (
        "数据始终来自绑定的 Provider，过期容忍没有可作用的对象，新鲜度口径请用 currentness"
    ),
    "allow_partial": (
        "quotes BatchResult 始终逐 symbol 记录三态，partial 是结果事实而非可放行的策略"
    ),
}
#: ``options`` 袋里唯一被直连执行面读取的键（``runtime/executor.py`` 的
#: ``options.get("args")`` / ``options.get("kwargs")`` / ``options.get("market")`` /
#: ``options.get("strict")`` / ``options.get("index")``）。
#: 袋里的键从此只有两种下场：在这个名单里被执行面读取，或者在 ``normalized()`` 当场被拒。
#: 名单与真实读取点的一致性由架构门禁 ``test_option_bag_keys_are_executed_or_rejected``
#: 对 AST 扫描结果求差把守——在这里加一个执行面不读的键，门禁即红。
EXECUTED_OPTIONS: frozenset[str] = frozenset({"args", "kwargs", "market", "strict", "index"})


class CurrentnessMode(str, Enum):
    """Caller intent for how current a result must be."""

    AUTO = "auto"
    LIVE = "live"
    HISTORICAL = "historical"
    BUSINESS = "business"


@dataclass(slots=True)
class ExecutionBudget:
    """The wall-clock ceiling of **one** ``execute()``.

    A ``policy`` fan-out re-compiles per symbol and therefore re-starts the clock;
    attempt counting is not here on purpose - retries belong to the transport pool
    (``[core] max_retries``) and to the fallback policy, not to this object.
    """

    deadline_ns: int

    @classmethod
    def from_deadline_ms(cls, deadline_ms: int) -> ExecutionBudget:
        if deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0",
                context={"deadline_ms": deadline_ms},
            )
        return cls(deadline_ns=time.monotonic_ns() + int(deadline_ms * 1_000_000))

    def remaining_s(self) -> float:
        return max(0.0, (self.deadline_ns - time.monotonic_ns()) / 1_000_000_000)

    def ensure_remaining(self, phase: str) -> None:
        if time.monotonic_ns() >= self.deadline_ns:
            raise ReadTimeout(
                "查询总 deadline 已耗尽",
                context={
                    "phase": phase,
                    "deadline_scope": "query",
                    #: 这是一条**决策**而不是建议：预算已经用光的失败不许再被对外
                    #: 宣告成"可以重试同一个 Provider"（`error_envelope` 读的就是它）。
                    "retry_same_provider": False,
                },
            )


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


def _require_int(value: Any, name: str) -> int:
    """绑定处的整数后闸（第 25 轮 G34）。

    四个入口在这之前的规整口径各不相同，坏值最后都落到 :meth:`QuerySpec.normalized`
    的 ``self.count < 0`` 一类比较上：库面因此抛出裸 :class:`TypeError`，而它被每一张
    服务面一致地翻译成 **E9000 内部错误**（HTTP 500）——调用方少写了一个引号，
    服务器却把故障登记在自己名下。这里只补一条口径：绑定处收到的必须是
    :class:`int`，否则当场 :class:`ValidationError`（E1010 / 422 / -32602）。

    这里**不**做字符串转换：三张服务面已在各自的入口用
    :func:`atst.server.wire_fields.as_request_int` 完成传输形状的规整，库面调用方
    则应当直接传整数。绑定处替人猜一次，就多一处"看起来生效"的口径分歧。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{name} 必须是整数：这里收到 {value!r}（{type(value).__name__}）。"
            "绑定处不把它猜成另一个数，也不让裸 TypeError 冒充服务器故障",
            context={
                "phase": "wire_validation",
                "face": "binder",
                "field": name,
                "received": repr(value),
            },
        )
    return value


def _canonical_options(options: Mapping[str, Any] | None) -> str:
    try:
        return json.dumps(
            dict(options or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "query options 必须是可 JSON 序列化的确定性值",
            context={"options_type": type(options).__name__},
        ) from exc


def _normalized_date_range(start_date: str, end_date: str, *, cap: str) -> tuple[str, str]:
    """日期区间的唯一校验口：形状、闭区间次序，以及"只有 K 线才有日期区间"这件事。

    为什么不接受 ``2024-01`` 或 ``20240101`` 这类写法：区间要在字符串上比较（ISO 日期的
    字典序与时间序一致），形状放宽一格就得在比较处再放宽一次，届时
    ``"2024-1-1" < "2024-10-01"`` 会静默成立。宁可当场把格式说清楚。

    ``start_date``/``end_date`` 只对**按时间成序列**的能力有意义；给 ``quotes`` 传它们是
    调用方写错了地方，收下也不会改变任何行为——按同一条 fail-closed 口径拒绝。
    """
    if not start_date and not end_date:
        return "", ""
    if cap not in _RANGE_CAPABILITIES:
        raise ValidationError(
            f"{cap} 不支持日期区间：只有 K 线类能力按时间成序列，"
            "实时快照要的是 currentness 不是日期",
            context={
                "capability": cap,
                "start_date": start_date,
                "end_date": end_date,
                "range_capabilities": sorted(_RANGE_CAPABILITIES),
            },
        )
    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if not value:
            continue
        if not _is_iso_day(value):
            raise ValidationError(
                f"{name}={value!r} 不是 YYYY-MM-DD：日期区间按 ISO 字符串比较，"
                "别的写法无法保证排序正确",
                context={"field": name, "received": value},
            )
    if start_date and end_date and start_date > end_date:
        raise ValidationError(
            "start_date 晚于 end_date：区间是闭区间，次序反了只会拿到空结果",
            context={"start_date": start_date, "end_date": end_date},
        )
    return start_date, end_date


#: 哪些能力上日期区间是有意义的（"按时间成序列"这一条判据）。
_RANGE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"bars", "adjusted_bars", "history", "minute_klines", "klines"}
)


def _is_iso_day(value: str) -> bool:
    """``YYYY-MM-DD``，且必须是个真实存在的日期（``2024-02-31`` 不算）。"""
    from datetime import date

    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    head, mid, tail = value[:4], value[5:7], value[8:10]
    if not (head.isdigit() and mid.isdigit() and tail.isdigit()):
        return False
    try:
        date(int(head), int(mid), int(tail))
    except ValueError:
        return False
    return True


def _parse_currentness(value: str | CurrentnessMode) -> CurrentnessMode:
    if isinstance(value, CurrentnessMode):
        return value
    normalized = str(value or CurrentnessMode.AUTO.value).strip().lower()
    try:
        return CurrentnessMode(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"未知 currentness {value!r}",
            context={"currentness": value, "allowed": [item.value for item in CurrentnessMode]},
        ) from exc


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """Immutable semantic request before Provider execution."""

    capability: str
    symbols: tuple[str, ...] = ()
    provider: str | None = None
    channel: str | None = None
    period: str = ""
    count: int = 0
    start: int = 0
    adjustment: str = ""
    #: 日期区间（``YYYY-MM-DD``，闭区间）。协议侧只有"从最新往回数 ``count`` 根"这一种
    #: 游标，所以区间是在取回的那一页上做过滤——取回的页盖不住区间起点时会记
    #: :data:`~atst.diagnostics.WarningCode.BARS_RANGE_UNCOVERED`（``strict=True`` 即失败），
    #: 而不是假装区间就是全部历史。
    start_date: str = ""
    end_date: str = ""
    currentness: str = CurrentnessMode.AUTO.value
    deadline_ms: int = 5000
    schema_version: int = 1
    options_json: str = "{}"

    @classmethod
    def build(
        cls,
        capability: str,
        *,
        symbols: str | Sequence[str] = (),
        provider: str | None = None,
        channel: str | None = None,
        period: str = "",
        count: int = 0,
        start: int = 0,
        adjustment: str = "",
        adjust: str = "",
        start_date: str = "",
        end_date: str = "",
        allow_stale: bool = False,
        currentness: str | CurrentnessMode = CurrentnessMode.AUTO,
        deadline_ms: int = 5000,
        schema_version: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> QuerySpec:
        if isinstance(symbols, str):
            symbol_tuple: tuple[Any, ...] = (symbols,)
        else:
            try:
                symbol_tuple = tuple(symbols)
            except TypeError as exc:
                #: 裸 ``TypeError`` 会被四张面一致地报成 E9000/HTTP 500——调用方写错的键被说成
                #: 服务器故障。这里也不替调用方把整数翻成字符串：``000001`` 写成整数就是 ``1``，
                #: 猜一次就是一条安静错码的查询。
                raise ValidationError(
                    "symbols 必须是代码字符串或代码字符串的序列：整数不是合法写法，"
                    "前导零会在到达这里之前就已经丢了",
                    context={
                        "phase": "wire_validation",
                        "capability": capability,
                        "symbols_type": type(symbols).__name__,
                    },
                ) from exc
        current = _parse_currentness(currentness)
        # ``adjust`` 是 ``adjustment`` 的别名（与 derived.adjusted_bars 的 method/adjustment
        # 别名同义）：canonical 的 ``adjustment`` 非空时胜出，否则用 ``adjust`` 填充。
        # 两者给了不同值且都非空时以 canonical 为准，不报错——避免出现第三种拼法后
        # 又多出一套"两个都给就失败"的调用方负担。
        effective_adjustment = adjustment or adjust
        # 构造期只保留"折叠进 options 袋"这一条通路：袋里的键要么被执行面消费，
        # 要么在 ``REJECTED_OPTIONS`` 里当场拒绝，不存在第三种"收下但没人读"。
        merged_options: dict[str, Any] = dict(options or {})
        if allow_stale:
            merged_options["allow_stale"] = True
        return cls(
            capability=capability,
            symbols=symbol_tuple,
            provider=provider,
            channel=channel,
            period=period,
            count=count,
            start=start,
            adjustment=effective_adjustment,
            start_date=_norm_text(start_date),
            end_date=_norm_text(end_date),
            currentness=current.value,
            deadline_ms=deadline_ms,
            schema_version=schema_version,
            options_json=_canonical_options(merged_options),
        )

    @property
    def options(self) -> dict[str, Any]:
        # R1：不做进程级解析复用——旧实现把解析结果按 JSON 串驻留进程内存
        # （含 wencai cookie 类凭证），且浅拷贝与调用方共享嵌套结构；
        # json.loads 本身足够便宜，按零缓存口径少一个例外。对外返回新 dict，
        # 调用方照旧可以自由改写。
        value = json.loads(self.options_json or "{}")
        return value if isinstance(value, dict) else {}

    def normalized(self, *, default_provider: str | None = None) -> QuerySpec:
        # P2-1：planning 侧的 canonical channel 判据与市场路由候选经包内函数级
        # 导入使用（spec 不在模块级依赖 planning；planning 模块级依赖 spec）。
        from .planning import (
            _NON_TDX_BAR_PROVIDERS,
            _NON_TDX_QUOTE_PROVIDERS,
            _canonical_unified_channel,
            _reject_core_channel_mismatch,
        )

        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")

        # A-share/quote/bar capabilities use the canonical security-symbol
        # engine.  Derivative identifiers are provider-native contracts (for
        # example ``IF2509`` and Eastmoney option quote ids) and must remain
        # opaque; forcing them through the A-share parser rejects valid
        # canonical capabilities before Provider execution.
        opaque_symbol_capabilities = frozenset(
            {
                "bond_kline",
                "futures_kline",
                "options_snapshot",
                "options_kline",
            }
        )
        if cap in opaque_symbol_capabilities:
            symbols = tuple(str(item).strip() for item in self.symbols)
            if any(not item for item in symbols):
                raise ValidationError(
                    f"{cap} 的 symbol 不能为空",
                    context={"capability": cap},
                )
        else:
            symbols = tuple(normalize_symbol(item) for item in self.symbols)
        # 市场感知路由（quotes / bars 同一口径）：TDX 二进制只认 SZ/SH 的
        # symbol-based 请求；BJ 未真机验证，HK/US 根本没有 tdx_market。此前
        # quotes 把 HK/US 翻译成 hk_quotes/us_quotes 再取能力目录缺省 Provider，
        # 真机上落到 tencent/derived 的 catalog（live=False）通道，直接 E1010
        # 「所选 Channel 不是 live channel」——2026-11-24 本地真机实测。
        # 现与 bars 一样**保留 canonical capability**，只把缺省 Provider 换成
        # operational 的实时 Web 源（tencent 的 quote 通道 live=True，真机
        # 同时覆盖 A 股 / BJ / HK / US 以及混合代码）。显式 provider 时本分支
        # 完全不介入，钉死 tdx 的非沪深请求仍在客户端层 fail-closed；结果的
        # meta.provider 会显示真实出数源，不是静默换源。
        _market_routed = False
        if cap in {"quotes", "bars"} and self.provider is None and symbols:
            try:
                routed_markets = {parse_symbol(s).market for s in symbols}
            except SymbolError:
                # R-7：parse_symbol 只抛 SymbolError；不吞与之无关的程序错误。
                routed_markets = set()
            if routed_markets and not routed_markets <= {"sh", "sz"}:
                _market_routed = True
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(f"{cap} 至少需要一个 symbol", context={"capability": cap})
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError("bars 统一 QuerySpec 一次只接受一个 symbol")
        for rejected, reason in REJECTED_OPTIONS.items():
            if self.options.get(rejected):
                raise ValidationError(
                    f"直连执行面不接受 {rejected}：{reason}",
                    context={"capability": cap, rejected: True},
                )
        if not self.options.keys() <= EXECUTED_OPTIONS:
            raise ValidationError(
                "options 里有直连执行面不读取的键（设置它们不会改变任何行为，"
                "因此当场拒绝而不是静默收下）",
                context={
                    "capability": cap,
                    "unknown_options": sorted(set(self.options) - set(EXECUTED_OPTIONS)),
                    "executed_options": sorted(EXECUTED_OPTIONS),
                },
            )
        for field_name in SPEC_INT_FIELDS:
            _require_int(getattr(self, field_name), field_name)
        if self.count < 0:
            raise ValidationError("count 不能为负数", context={"count": self.count})
        if cap == "bars" and self.count <= 0:
            raise ValidationError("bars.count 必须大于 0", context={"count": self.count})
        if self.start < 0:
            raise ValidationError("start 不能为负数", context={"start": self.start})
        start_date, end_date = _normalized_date_range(self.start_date, self.end_date, cap=cap)
        if self.deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0", context={"deadline_ms": self.deadline_ms}
            )
        if self.schema_version <= 0:
            raise ValidationError("schema_version 必须大于 0")

        currentness = _parse_currentness(self.currentness)
        if _market_routed:
            # 非沪深标的：从 operational 的 Web Provider 里按偏好挑一个 live 源。
            # tdx 在册但对 BJ 未验证、对 HK/US 抛 E4040；eastmoney 的 push2
            # 在本地真机被 WAF 断连（2026-11-24 实测三节点全挂），故 quotes
            # 以 tencent 的 live quote 通道优先；bars 仍以 eastmoney 的 kline
            # 优先（其 secid 覆盖 BJ/HK/US，且支持复权）。
            preferred = _NON_TDX_BAR_PROVIDERS if cap == "bars" else _NON_TDX_QUOTE_PROVIDERS
            available = {p for p in PROVIDERS.available_providers(cap) if p in preferred}
            chosen = next((p for p in preferred if p in available), None)
            fb_default = chosen or (default_provider or PROVIDERS.default_provider)
        else:
            fb_default = default_provider or PROVIDERS.default_provider
        selected = resolve_capability_provider(
            cap,
            self.provider,
            default=fb_default,
        )
        channel = _norm_text(self.channel) or None
        period = normalize_bar_period(self.period) if cap == "bars" else _norm_text(self.period)
        # v13 SSOT：统一 quotes/bars 只能走 Provider 的 canonical channel。该检查必须先于
        # 注册表存在性校验，否则 ``provider=tdx, channel=vipdoc`` 会先报“未知 channel”，
        # 掩盖真正的“canonical channel 不匹配”语义。
        _reject_core_channel_mismatch(
            selected,
            cap,
            channel,
            _canonical_unified_channel(selected, cap, period),
        )
        PROVIDERS.require(selected, cap, channel=channel)

        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=selected,
            channel=channel,
            period=period,
            adjustment=_norm_text(self.adjustment),
            start_date=start_date,
            end_date=end_date,
            currentness=currentness.value,
            options_json=_canonical_options(self.options),
        )


#: :meth:`QuerySpec.normalized` 之前必须已经是真整数的字段：**从 dataclass 自己的 ``int``
#: 注解现扫**，不另抄名单——抄来的名单会在下一个 int 形参上漏掉自己（第 25 轮 G34）。
SPEC_INT_FIELDS: Final[tuple[str, ...]] = tuple(
    spec_field.name for spec_field in dataclass_fields(QuerySpec) if spec_field.type == "int"
)
