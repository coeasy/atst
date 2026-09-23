"""G15/G16：「周期 → 上游参数」的每张表都必须真的服务它键上写的那个周期。

第 18 轮把周期词表收成一处声明（:mod:`tstdx.domain.period`），量的是**公开面之间**
的分叉。第 19 轮把同一把尺子伸到执行侧：全仓 AST 现扫「以周期拼写为键」的字面量表，
19 处命中，逐张核对后抓到两类东西——

**① 一格会把请求换成另一个周期（G15，本轮已修）**
:class:`tstdx.web.history.SinaHistoryKlineSource` 的 ``SCALES`` 里有 ``"1min": 5``：
新浪这个端点最细就是 5 分钟，于是"1 分钟"的写法会被拿去请求 5 分钟线还照常返回——
帧合法、内容是另一个周期，与 G3 同族。它的类 docstring 甚至明写 ``1min(=5min)``。
Provider 注册表恰好**没有**给 ``sina/history_kline`` 声明 ``1min``，所以走内核的调用方
吃不到这一格；但 ``tstdx.web.SinaHistoryKlineSource`` 是导出符号，直接用源的人吃得到。
处置：删行，让"这一面不服务"成为显式报错（第 18 轮 G13 那条裁决的同一句式）。

**② 两处还在手抄别名（G16，本轮已修）**
``_KLINE_KTYPES``（百度）与 ``_MKLINE_PERIODS``（腾讯 mkline）把自己的别名键各抄了
6 个和 5 个。百度那份抄错了：它写着 ``"1m": 3``，而域内词表里 ``1m`` 是 **1 分钟**——
即"1 分钟"在百度这一面会被解成**月线**。两张表现在只收规范拼写，别名由公开面经
:func:`~tstdx.domain.period.normalize_bar_period` 一次解掉（会话层 ``baidu_kline`` /
``WebQuoteSession.history`` 各加这一手）。

其余 15 张表核对通过：``Nmin`` 键的请求参数就是 N 分钟（``5`` / ``m5`` 两种编码），
非分钟档（day/week/month/season/year）是各上游自己的枚举，逐格登记在下面第 4 条判据的
豁免表里——豁免要说清是谁、为什么，且失效就当众红。
"""

from __future__ import annotations

import ast
import re
from typing import Any

import pytest

from tests.support.field_readers import REPO_ROOT
from tstdx.client.core import _CANONICAL_TO_CATEGORY
from tstdx.domain.period import CANONICAL_PERIODS, PERIOD_ALIASES, normalize_bar_period
from tstdx.protocol.parsers._std7709_common import KlineCategory
from tstdx.providers import PROVIDERS
from tstdx.reader.formats import resolve_vipdoc_path
from tstdx.web._session_market import _KLINE_SERVABLE, KLINES_PERIOD_ALIASES
from tstdx.web.adapters import KlineSource
from tstdx.web.adapters_baidu import _KLINE_KTYPES
from tstdx.web.adapters_ext import _MKLINE_PERIODS
from tstdx.web.history import EastmoneyHistoryKlineSource, SinaHistoryKlineSource

pytestmark = pytest.mark.unit

#: 上游有、域内词表没有的两档：新浪独有的 2 小时/20 小时粒度。它们**故意**不进
#: :data:`~tstdx.domain.period.CANONICAL_PERIODS`——tdx 协议没有对应 category，
#: 进了规范集合就要伪造一个协议号（不猜协议字节）。
PROVIDER_SCOPED = frozenset({"120min", "1200min"})

PERIODISH = set(CANONICAL_PERIODS) | set(PERIOD_ALIASES) | PROVIDER_SCOPED
_MINUTE_KEY = re.compile(r"^(\d+)min$")

#: 本轮现扫出来的账：哪些地方还以周期拼写为键/成员。三种角色决定它受哪条尺子量——
#: ``vocabulary`` 是词表本身（只有域内那一处许可），``param`` 是"周期 → 上游参数"的
#: 执行表（受分钟尺与编码账两把尺子），``declared`` 是注册表/会话面声明的服务档
#: （受下面第 3 条尺子问执行体），``protocol`` 是周期 → tdx 协议号，``irrelevant``
#: 是扫描的形状误报（在此登记理由，否则下次改动没人知道它为什么在账上）。
#: 新增一张表、或某张表份数变了，都要先在账上写理由。
LEDGER: dict[tuple[str, str], tuple[str, str, int]] = {
    ("tstdx/domain/period.py", "CANONICAL_PERIODS"): ("vocabulary", "唯一的规范词表", 1),
    ("tstdx/domain/period.py", "PERIOD_ALIASES"): ("vocabulary", "唯一的别名词表", 1),
    ("tstdx/client/core.py", "_CANONICAL_TO_CATEGORY"): ("protocol", "规范拼写 → tdx 协议号", 1),
    ("tstdx/providers/__init__.py", "periods"): ("declared", "各 channel 声明的服务档", 7),
    ("tstdx/web/_session_market.py", "_KLINE_SERVABLE"): ("declared", "ifzq 面服务集", 1),
    ("tstdx/web/adapters.py", "PERIODS"): ("param", "腾讯 fqkline 参数", 1),
    ("tstdx/web/adapters_baidu.py", "_KLINE_KTYPES"): ("param", "百度 ktype 参数", 1),
    ("tstdx/web/adapters_ext.py", "_MKLINE_PERIODS"): ("param", "腾讯 mkline 参数", 1),
    ("tstdx/web/efinance_deriv.py", "table"): ("param", "东财 klt 参数", 1),
    ("tstdx/web/fundflow.py", "klt"): ("param", "东财 klt 参数", 1),
    ("tstdx/web/history.py", "SCALES"): ("param", "新浪 scale 参数", 1),
    ("tstdx/web/history.py", "KLTS"): ("param", "东财 klt 参数", 1),
    ("tstdx/protocol/generic.py", "(字面量)"): ("irrelevant", "datetime32 字段名，与周期无关", 1),
}

ROLES = ("vocabulary", "protocol", "declared", "param", "irrelevant")


def _assignment_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """这张字面量表挂在谁名下：顶层/类内 ``NAME = ...``、``periods=(...)`` 关键字。"""

    owner = parents.get(node)
    while owner is not None:
        if isinstance(owner, ast.Assign):
            for target in owner.targets:
                if isinstance(target, ast.Name):
                    return target.id
        elif isinstance(owner, ast.AnnAssign) and isinstance(owner.target, ast.Name):
            return owner.target.id
        elif isinstance(owner, ast.keyword) and owner.arg:
            return owner.arg
        owner = parents.get(owner)
    return "(字面量)"


def _literal_period_tables() -> list[tuple[str, str, dict[str, Any]]]:
    """扫 ``tstdx/`` 全部字面量 dict/tuple/list/set，取出"以周期拼写为键/成员"的表。

    派生表（``{**a, **b}``、推导式）没有字面量周期键，自然不落进这张账——这正是
    第 18 轮之后我们要的形状：只有手抄件需要被登记。
    """

    found: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted((REPO_ROOT / "tstdx").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            pairs: list[tuple[str, Any]] = []
            if isinstance(node, ast.Dict):
                pairs = [
                    (k.value, v)
                    for k, v in zip(node.keys, node.values, strict=False)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
            elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                pairs = [
                    (e.value, None)
                    for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
            periodish = [key for key, _ in pairs if key in PERIODISH]
            if len(periodish) < 2 or len(periodish) < len(pairs) / 2:
                continue
            table = {
                key: (value.value if isinstance(value, ast.Constant) else "<非字面量>")
                for key, value in pairs
                if key in PERIODISH
            }
            found.append((relative, _assignment_name(node, parents), table))
    return found


def test_the_scan_sees_the_tables_it_claims() -> None:
    """防盲：扫描一无所获，或账上任何一张表都还在原地，才允许往下判。"""

    scanned = _literal_period_tables()
    assert len(scanned) >= 15, f"周期表扫描只剩 {len(scanned)} 处命中：尺子瞎了"
    present = {(relative, name) for relative, name, _ in scanned}
    for relative, name in LEDGER:
        assert (relative, name) in present, f"{relative}::{name} 不再被扫到：口径变了"


def test_no_period_table_appears_off_the_ledger() -> None:
    """新长出一张以周期为键的表必须先在账上写理由——G12/G13 的教训都是"没人登记的第二手词表"。"""

    scanned = _literal_period_tables()
    counted: dict[tuple[str, str], int] = {}
    for relative, name, _table in scanned:
        counted[(relative, name)] = counted.get((relative, name), 0) + 1
    ledger_counts = {key: entry[2] for key, entry in LEDGER.items()}
    assert all(entry[0] in ROLES for entry in LEDGER.values()), "账上有未定义的角色"
    assert counted == ledger_counts, (
        "周期表账本与现扫不一致（新增/消失/份数变化都要写理由）："
        f"现扫多出的 = {sorted(set(counted) - set(ledger_counts))}，"
        f"账上有而现扫没有 = {sorted(set(ledger_counts) - set(counted))}，"
        f"份数变了 = {sorted(k for k in set(counted) & set(ledger_counts) if counted[k] != ledger_counts[k])}"
    )


# --------------------------------------------------------------------------- #
# 尺子一：分钟档的请求参数必须就是它键上写的分钟数
# --------------------------------------------------------------------------- #
#: 非分钟档（各上游自己的枚举编码）逐格登记；键不在这里又非 ``Nmin`` 形状 = 新形状，红。
NON_MINUTE_ENCODINGS: dict[tuple[str, str], dict[str, int | str]] = {
    ("tstdx/web/history.py", "SCALES"): {"day": 240},
    ("tstdx/web/history.py", "KLTS"): {"day": 101},
    ("tstdx/web/adapters.py", "PERIODS"): {
        "day": "day",
        "week": "week",
        "month": "month",
    },
    ("tstdx/web/adapters_baidu.py", "_KLINE_KTYPES"): {"day": 1, "week": 2, "month": 3},
    ("tstdx/web/adapters_ext.py", "_MKLINE_PERIODS"): {},
    ("tstdx/web/efinance_deriv.py", "table"): {"day": 101, "week": 102, "month": 103},
    ("tstdx/web/fundflow.py", "klt"): {"day": 101, "week": 102, "month": 103},
}


def _minute_rule_violations(table: dict[str, Any]) -> list[tuple[str, Any]]:
    """``Nmin`` 键必须请求 N 分钟：值写成 ``N`` / ``mN`` / ``Nmin`` 之外就是换周期。"""

    bad = []
    for key, value in table.items():
        match = _MINUTE_KEY.match(str(key))
        if match is None or not isinstance(value, (int, float, str)):
            continue
        if value == "<非字面量>":
            continue  # 枚举/常量值由它自己的运行时判据量（见协议号那条）
        minutes = int(match.group(1))
        allowed = {str(minutes), f"m{minutes}", f"{minutes}min"}
        if str(value).strip().lower() not in allowed:
            bad.append((key, value))
    return bad


def _role_of(relative: str, name: str) -> str:
    entry = LEDGER.get((relative, name))
    return entry[0] if entry else "unregistered"


def test_every_minute_spelling_requests_its_own_minutes() -> None:
    """G15 的尺子：每张「周期 → 上游参数」表过一遍，``1min`` 换 5 分钟线这类格子当场红。"""

    offenders = {}
    checked = 0
    for relative, name, table in _literal_period_tables():
        if _role_of(relative, name) != "param":
            continue
        checked += 1
        bad = _minute_rule_violations(table)
        if bad:
            offenders[f"{relative}::{name}"] = bad
    assert checked >= 6, f"只量到 {checked} 张参数表：本判据失去对象"
    assert not offenders, f"这些分钟档请求的不是它自己：{offenders}"


def test_the_minute_ruler_itself_sees_the_old_sina_row() -> None:
    """正控：本轮删掉的那格 ``"1min": 5`` 重新出现时，上面的尺子必须抓得住。"""

    planted = {"1min": 5, "5min": 5, "15min": 15, "day": 240}
    assert _minute_rule_violations(planted) == [("1min", 5)]
    assert _minute_rule_violations({"5min": 5, "15min": "m15", "day": 240}) == []


def test_non_minute_rows_are_the_registered_upstream_encodings() -> None:
    """day/week/month 这些不是分钟数，编码是各上游自己的枚举——逐格登记，新形状要红。"""

    param_tables = [
        (relative, name, table)
        for relative, name, table in _literal_period_tables()
        if _role_of(relative, name) == "param"
    ]
    assert param_tables, "一张参数表都没扫到：本判据失去对象"
    for relative, name, table in param_tables:
        assert (relative, name) in NON_MINUTE_ENCODINGS, (
            f"{relative}::{name} 是新的「周期 → 上游参数」表，非分钟档还没逐格登记"
        )
        registered = NON_MINUTE_ENCODINGS[(relative, name)]
        for key, value in table.items():
            if _MINUTE_KEY.match(str(key)):
                continue
            assert key in registered, f"{relative}::{name} 的非分钟档 {key!r} 没逐格登记"
            assert value == registered[key], (
                f"{relative}::{name} 的 {key!r} 编码变了：账上 {registered[key]!r} → 现值 {value!r}"
            )


def _protocol_row_mismatches(table: dict[str, int]) -> dict[str, tuple[int, str]]:
    """反查 ``KlineCategory.NAMES``：这一行协议号真正取到的周期，与键上写的是否同一档。"""

    wrong = {}
    for spelling, category in table.items():
        answered = normalize_bar_period(KlineCategory.NAMES[category])
        if answered != spelling:
            wrong[spelling] = (category, KlineCategory.NAMES[category])
    return wrong


def test_the_protocol_category_rows_point_at_the_period_they_name() -> None:
    """tdx 协议号是运行时枚举，AST 看不见值——这里用 ``KlineCategory.NAMES`` 反查兑现。

    第 17 轮量到的 ``quarter → 10`` 就是这一类：``NAMES[10]`` 其实是 ``season``。
    一行指向别的周期的协议表，比没有表更危险。
    """

    assert _CANONICAL_TO_CATEGORY, "协议表空了：本判据失去对象"
    wrong = _protocol_row_mismatches(_CANONICAL_TO_CATEGORY)
    assert not wrong, f"这些协议行请求的不是它键上写的周期：{wrong}"


def test_the_protocol_ruler_sees_the_old_quarter_row() -> None:
    """正控：``quarter`` 挂着季线协议号（10）时，上面那条尺子必须认出来。"""

    assert _protocol_row_mismatches({"quarter": 10}) == {"quarter": (10, "season")}
    assert _protocol_row_mismatches({"season": 10}) == {}


# --------------------------------------------------------------------------- #
# 尺子二：执行侧不许手抄别名
# --------------------------------------------------------------------------- #
def test_only_the_domain_vocabulary_hands_copies_period_aliases() -> None:
    """G16：别名→参数的手抄件是第二个词表；百度那份还把 ``1m``（1 分钟）抄成了月线。"""

    offenders = {}
    for relative, name, table in _literal_period_tables():
        if relative == "tstdx/domain/period.py":
            continue
        aliases = sorted(k for k in table if k in set(PERIOD_ALIASES))
        if aliases:
            offenders[f"{relative}::{name}"] = aliases
    assert not offenders, f"执行侧又手抄了周期别名（公开面负责规范它）：{offenders}"


def test_the_alias_ruler_is_not_blind() -> None:
    """防盲：这张尺子必须真的看得见别名——域内词表自己有别名键，且两处旧抄件已空。"""

    scanned = {(relative, name): table for relative, name, table in _literal_period_tables()}
    assert any(
        k in set(PERIOD_ALIASES) for k in scanned[("tstdx/domain/period.py", "PERIOD_ALIASES")]
    ), "域内别名表扫不出别名：本判据失去对象"
    assert set(_KLINE_KTYPES) == {"day", "week", "month"}
    assert set(_MKLINE_PERIODS) == {p for p in CANONICAL_PERIODS if p.endswith("min")}


# --------------------------------------------------------------------------- #
# 尺子三：注册表声明的档，必须真是执行体服务得起的档
# --------------------------------------------------------------------------- #
#: 每个声明了 periods 的 bars channel → 怎么问执行体"这一档你服务吗"。
#: 问法必须是真代码（函数或它读的那张表），不是注册表自己那份声明。
SERVED_BY: dict[tuple[str, str], str] = {
    ("tdx", "quotation"): "协议 category 表",
    ("local_vipdoc", "vipdoc"): "resolve_vipdoc_path 的分支",
    ("tencent", "kline"): "KlineSource.build_url",
    ("tencent", "minute_kline"): "_mkline_period",
    ("sina", "history_kline"): "SinaHistoryKlineSource.SCALES",
    ("eastmoney", "kline"): "EastmoneyHistoryKlineSource.KLTS",
    ("baidu", "kline"): "_ktype",
}


def _served(provider: str, channel: str, period: str) -> bool:
    if (provider, channel) == ("tdx", "quotation"):
        return period in _CANONICAL_TO_CATEGORY
    if (provider, channel) == ("local_vipdoc", "vipdoc"):
        try:
            resolve_vipdoc_path("vipdoc", "sh600519", period)
        except ValueError:
            return False
        return True
    if (provider, channel) == ("tencent", "kline"):
        try:
            KlineSource.build_url(object.__new__(KlineSource), ["sh600519"], period=period)
        except ValueError:
            return False
        return True
    if (provider, channel) == ("tencent", "minute_kline"):
        from tstdx.web.adapters_ext import _mkline_period

        return period in _MKLINE_PERIODS and _mkline_period(period) is not None
    if (provider, channel) == ("baidu", "kline"):
        from tstdx.web.adapters_baidu import _ktype

        return period in _KLINE_KTYPES and _ktype(period) is not None
    table: dict[str, Any] = (
        SinaHistoryKlineSource.SCALES
        if (provider, channel) == ("sina", "history_kline")
        else EastmoneyHistoryKlineSource.KLTS
    )
    return period in table


def _declared_channels() -> set[tuple[str, str]]:
    out = set()
    for provider_id in PROVIDERS.ids():
        for channel in PROVIDERS.get(provider_id).channels:
            if "bars" in channel.capabilities and channel.periods:
                out.add((provider_id, channel.id))
    return out


def test_every_declaring_channel_has_a_way_to_ask_the_executor() -> None:
    """防盲 + 防漏：声明了档位的 channel 必须都在 SERVED_BY 里，且问法不空。"""

    declared = _declared_channels()
    assert declared, "没有任何 channel 声明 periods：本族判据失去对象"
    assert declared == set(SERVED_BY), (
        f"声明档位却没人核对的 channel：{sorted(declared ^ set(SERVED_BY))}"
    )


def test_registry_periods_are_actually_served_by_the_bound_executor() -> None:
    """注册表不许超报：它说服务的每一档，执行体真得接得住。"""

    declared = _declared_channels()
    overclaims = []
    for provider_id, channel_id in sorted(declared):
        periods = PROVIDERS.get(provider_id).channel(channel_id).periods
        for period in sorted(periods):
            if not _served(provider_id, channel_id, period):
                overclaims.append((provider_id, channel_id, period))
    assert declared, "没有任何 channel 声明 periods：本判据失去对象"
    assert not overclaims, f"注册表声明了执行体不服务的档：{overclaims}"


def test_the_sina_history_face_says_no_instead_of_renaming_a_period() -> None:
    """G15 的行为账：``1min`` 在新浪面显式报错，而不是拿 5 分钟线冒充。"""

    assert "1min" not in SinaHistoryKlineSource.SCALES
    assert "1min" not in PROVIDERS.get("sina").channel("history_kline").periods
    assert "1m" not in _KLINE_KTYPES, "百度又用 1m 表示月线了"
    assert _KLINE_KTYPES.keys() <= set(CANONICAL_PERIODS)


def test_provider_scoped_spellings_stay_out_of_the_canonical_table() -> None:
    """``120min``/``1200min`` 只有新浪有：它们是 provider 档，不是域内规范档。"""

    assert set(SinaHistoryKlineSource.SCALES) >= PROVIDER_SCOPED
    assert set(PROVIDERS.get("sina").channel("history_kline").periods) >= PROVIDER_SCOPED
    assert not (PROVIDER_SCOPED & set(CANONICAL_PERIODS)), (
        "provider 专属档进了规范集合：tdx 协议号就得伪造，回到不猜字节之前"
    )


def test_the_web_session_accepts_aliases_only_through_the_domain_table() -> None:
    """会话层的接受集 = 域内词表 ∩ 本面服务集；``m5`` 这类写法由派生提供，不是本地抄。"""

    expected = {
        **{a: c for a, c in PERIOD_ALIASES.items() if c in set(_KLINE_SERVABLE)},
        **{c: c for c in _KLINE_SERVABLE},
    }
    assert expected == KLINES_PERIOD_ALIASES
    assert not (set(KLINES_PERIOD_ALIASES) & set(CANONICAL_PERIODS) - set(_KLINE_SERVABLE))
