# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G46 — 执行器握着的 ``hosts`` 与 ``config`` 必须抵达每一个 ``TdxClient`` 家族的低层客户端。

第 31 轮第 1 遍实测到的断链形状：:class:`tstdx.runtime.executor.DirectProviderExecutor`
的构造处收 ``hosts`` 与 ``config``，``_tdx_client()`` 把两者都交出去（``self.hosts`` 作位置参
+ :func:`~tstdx.transport.pool.pool_settings_from_config` 的六键）；而
``f10_client`` / ``ex_client`` / ``goods_client`` / ``mac_client`` 四个分支构造的是同一个类的
**子类**，却只传 ``timeout=hop``。于是调用方钉住的主站列表、``[hosts] slots_per_host``、
``[core] max_retries``、``[core] heartbeat_interval``、``[rate_limit]``、``[security] use_tls``
在 F10 / 商品 / 扩展行情 / MAC 这四条路上当场蒸发，而同一个键在行情主链路上是生效的——
同一个 ``Client`` 实例、同一份配置，两条路的口径不同。主站钉不住还有安全含义：
显式 ``hosts`` 是本仓库唯一的"只碰我指定的站"手段。

修复后的口径：家族构造只有一处 :meth:`~tstdx.runtime.executor.DirectProviderExecutor._pool_client`，
``_tdx_client()`` 是它在行情族上的调用点，四条分支全部改走它。

第 27 轮的 ``test_pool_knob_reachability.py`` 量的是 ``Config → ConnectionPool`` 那一格翻译
（它现在仍然成立），**没量执行器有几个构造点、每个构造点有没有用那份翻译**——又是"量接缝
两侧而不量接缝"（V19 §10 教训 3 同族）。

判据不看分支写法也不看注释，只看五件事：

1. 把一份真实 ``Config`` 与一个显式 ``hosts`` 交给执行器，驱动绑定表里每一个"实现类是
   ``TdxClient`` 子类"的格子，**在构造处把那个类截住**，检查它实际收到了什么；
2. 家族类在执行器里**只许被 :meth:`~tstdx.runtime.executor.DirectProviderExecutor._pool_client`
   一处构造**——本轮这条断链的形状就是"分支各自 new 一个"，所以除了量读数，还要量构造点数量；
3. 每一格用完必须**恰好显式 ``close()`` 一次**，正常路径与实现抛错路径各量一遍——
   收得对不对不看分支写法，看动作序列（第 31 轮 31-B 那一格 ``tdx_client`` 分支用 ``with``，
   ``__enter__`` 落在 try 之外，构造出来的池和心跳线程在异常路径上没人收尾）。
4. 交出一个可关闭客户端的位置**只许待在唯一那道 :meth:`_client_session` 保护区里**（31-C3）。
   31-B4 那次只收了迁移分支三条，核心链路还留着九处 ``with self._tdx_client(...) as client:``——
   同一份文件对同一个风险给两种答案。这条量的是形状本身：``with`` 直接包住构造出口即红，
   无论块体怎么写；保护区自己另有一判，``yield`` 在 try 里、``close()`` 在 finally 里且只一次。
5. 第 4 件事的射程是**整个 ``tstdx/``**，不是只有执行器（31-C4）。执行器的 13 处收进
   :meth:`_client_session` 之后再按族扫全包，同一形状在 CLI 面还剩 6 处：``probe`` /
   ``blocks`` / ``list`` / ``quotes-snapshot`` 四处 ``with TdxClient(**conn)``、``goods`` /
   ``f10`` 两处 ``with get_client(...)``。一把只读 ``executor.py`` 的尺子看不见换了一张面的
   同一件事，所以判据按"构造出口 / 保护区"的名字在全包 AST 上走，而不是按文件名单。

变异台账（第 31 轮合成一本：``Temp/mut31d_all.py`` 在候选树上逐条落锚、只跑该条 assigned 的
判据、按字节回滚并复核 sha256。本轮读数 **CASES=19 BAD=0**，基线 = 本尺子 104 格全绿）：
- **M1** ``_pool_client()`` 的主站位置参换成 ``None`` → 本尺子 **19 红 / 85 绿**，第 27 轮的
  ``test_pool_knob_reachability.py`` 4 格全绿；
- **M2** ``f10_client`` 分支绕开构造处自己 ``F10Client(timeout=hop)`` → **5 红 / 99 绿**，红名
  逐格可点：该族两格读数（``test_a_pinned_host_list_reaches_every_family_client[f10×2]``）+
  同族两格旋钮（``test_every_family_client_gets_the_translated_pool_settings[f10×2]``）+
  一格形状（``test_no_family_client_is_constructed_outside_the_pool_home``）；
- **M3** ``ex/goods/mac`` 分支同样绕开构造处 → **15 红 / 89 绿**；
- **M4** 构造处把翻译结果收缩成只剩 ``timeout`` → **19 红 / 85 绿**，翻译层尺子仍 4 绿（与 M1
  同形：两把尺子射程不重叠）；
- **M5**（对照条）翻译层 :func:`~tstdx.transport.pool.pool_settings_from_config` 删一个键 →
  **本尺子 104 全绿**，红的是翻译层那把尺子（2 红）：本尺子量的是"执行器有没有把翻译结果
  交出去"，不重复量翻译本身——两把尺子的功劳不许记串；
- **M6** 家族集合派生改成永远为空（本尺子自身失明）→ 2 红 / 7 绿 / 5 跳过：分母闭合格红，
  其余格子因分母为空而无事可做。"尺子自己瞎了"必须单独一条才看得见；
- **M7**（31-B）``tdx_client`` 分支回到 ``with self._tdx_client(hop) as client`` → **22 红 /
  82 绿**：该族 10 格 ``released_exactly_once`` + 10 格 ``raising_still_releases`` + 形状格 +
  全包射程格一起红（``enter``/``exit`` 顶掉了 ``close``，一次就把四把尺子全撞翻）；
- **M15**（31-C3）核心链路 7 处里的任意一处回到 ``with self._tdx_client(self._hop_timeout(plan))
  as client:`` → 只红两格（``test_every_client_handoff_sits_behind_the_guarded_session`` 与
  ``test_no_family_client_is_handed_to_a_with_anywhere_in_the_package``，实测报出行号
  ``runtime/executor.py:630``），其余 102 格仍绿：这一条只量形状，不重复量读数。
- **M16**（31-C4）CLI ``blocks`` 分支回到 ``with get_client("stock", **_transport_kwargs(args))``
  → 只红全包判据 ``test_no_family_client_is_handed_to_a_with_anywhere_in_the_package``（实测报出
  ``cli/runtime_commands.py:751``），其余 103 格仍绿：换面即换尺子，这条量的是射程。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import tstdx.client as client_face
from tstdx.catalog.capability import MIGRATED_BINDINGS, implementation_for
from tstdx.client import TdxClient
from tstdx.config.schema import Config, CoreConfig, HostsConfig, RateLimitConfig, SecurityConfig
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.runtime.executor import DirectProviderExecutor
from tstdx.transport.pool import pool_settings_from_config

EXECUTOR_SOURCE = (
    Path(__file__).resolve().parents[2] / "tstdx" / "runtime" / "executor.py"
).read_text(encoding="utf-8")

#: 第 5 件事的射程：整个运行期包，不只执行器。
_PKG_ROOT = Path(__file__).resolve().parents[2] / "tstdx"

#: 调用方钉住的主站：执行器必须原样交到构造处，不能替换成内置候选池。
PINNED_HOSTS: tuple[tuple[str, int], ...] = (("PINNED-HOST", 7709),)

#: 每个旋钮都取与自身默认值**不同**的数字，否则"没传"与"传了默认值"在这把尺子下同形。
PROBE_CONFIG = Config(
    core=CoreConfig(timeout=4.0, max_retries=1, heartbeat_interval=17),
    hosts=HostsConfig(slots_per_host=2),
    rate_limit=RateLimitConfig(continuous=33),
    security=SecurityConfig(use_tls=True),
)

#: 驱动一格所需的**最小合法入参**，按实现签名的必填形参派生（不是手抄格子名单）。
#: 这里的值没有任何判据读它——它唯一的作用是让 ``validate_call`` 放行，好让执行器真的
#: 走到构造那一行。新增必填形参没登记在这里就是判据失明，因此 _payload_for 会硬失败。
_REQUIRED_ARGS: dict[str, Any] = {
    "symbol": "000001",
    "symbols": ["000001"],
    "date": "20260701",
    "filename": "000001.txt",
}

#: 执行器的 socket 超时上界：`_hop_timeout` 取「配置值与剩余预算」之小，这里必须是它，
#: 不是 ``PROBE_CONFIG.core.timeout``。
EXECUTOR_TIMEOUT = 2.0

PROBE_SETTINGS = pool_settings_from_config(PROBE_CONFIG)


def _settings_reading(got: dict[str, Any]) -> dict[str, Any]:
    """把一份构造参数里的六个传输旋钮读成可比较的值。

    ``rate_limiter`` 每次都新建实例，按身份比必然假红；它携带的配置才是这一格要量的东西，
    所以读它的速率表。
    """

    reading = {key: value for key, value in got.items() if key != "rate_limiter"}
    limiter = got.get("rate_limiter")
    reading["rate_limiter"] = None if limiter is None else limiter._rates
    return reading


class _AnyAttr(type):
    """类级属性对任何名字都给一个空实现：``implementation_for`` 的 ``getattr(cls, method)`` 先过闸。"""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def _any(*args: Any, **kwargs: Any) -> list[Any]:
            return []

        _any.__qualname__ = f"{cls.__name__}.{name}"
        return _any


class _Recorder:
    """只记录构造参数与释放动作；绝不发包，也不需要发包。"""

    captured: dict[str, Any] = {}
    #: 一次驱动的动作序列：``enter``/``exit`` 来自上下文协议，``close`` 来自显式释放。
    events: list[str] = []
    #: 让实现方法抛错，量"异常路径上有没有人收尾"。
    boom: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _Recorder.captured = {"args": args, "kwargs": kwargs}

    def __enter__(self) -> _Recorder:
        _Recorder.events.append("enter")
        return self

    def __exit__(self, *exc: Any) -> bool:
        _Recorder.events.append("exit")
        return False

    def close(self) -> None:
        _Recorder.events.append("close")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> list[Any]:
            _Recorder.events.append(f"call:{name}")
            if _Recorder.boom:
                raise _Boom(f"{name} 实现抛错")
            return []

        return _call


class _Boom(RuntimeError):
    """实现方法在 ``boom`` 模式下抛出的错：只用来走异常路径。"""


def _recorder(name: str) -> type:
    return _AnyAttr(name, (_Recorder,), {})


def _executor_family_classes() -> dict[str, type]:
    """执行器从 ``tstdx.client`` 引进来、且是 ``TdxClient`` 子类的那些类。

    这是**派生**的家族集合，不是抄的名单：新增一个家族低层客户端，它自己会进射程。
    """

    imported: set[str] = set()
    for node in ast.walk(ast.parse(EXECUTOR_SOURCE)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level
            and (node.module or "").endswith("client")
        ):
            imported.update(alias.name for alias in node.names)
    found = {}
    for name in sorted(imported):
        obj = getattr(client_face, name, None)
        if isinstance(obj, type) and issubclass(obj, TdxClient):
            found[name] = obj
    return found


def _family_cells() -> list[tuple[str, str, str, str]]:
    """绑定表里每一个"实现落在 ``TdxClient`` 家族类上"的执行格。"""

    families = _executor_family_classes()
    cells: list[tuple[str, str, str, str]] = []
    for binding in MIGRATED_BINDINGS:
        impl = implementation_for(binding.provider, binding.channel, binding.capability)
        if impl is None:
            continue  # web_adapter / composed：没有一份可绑的签名，也就不构造家族客户端
        owner = getattr(impl, "__qualname__", "").split(".")[0]
        if owner in families:
            cells.append((binding.provider, binding.channel, binding.capability, owner))
    return cells


_CELLS = _family_cells()


def _payload_for(provider: str, channel: str, capability: str) -> tuple[Any, ...]:
    impl = implementation_for(provider, channel, capability)
    assert impl is not None
    #: [1:] 去掉 ``self``：``implementation_for`` 取的是未绑定函数，签名第一位是宿主形参。
    params = list(inspect.signature(impl).parameters.values())[1:]
    args: list[Any] = []
    unknown: list[str] = []
    for param in params:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        required = param.default is inspect.Parameter.empty
        if param.kind is param.KEYWORD_ONLY:
            #: 关键字形参一律留在缺省上——它们与传输旋钮无关，必填的那类要另开一格债。
            if required:
                unknown.append(param.name)
            continue
        if not required:
            continue
        try:
            args.append(_REQUIRED_ARGS[param.name])
        except KeyError:
            unknown.append(param.name)
    assert not unknown, (
        f"{provider}/{channel}/{capability} 的必填形参 {unknown} 没有登记读数，"
        "这一格就驱动不到构造处 —— 判据自身失明"
    )
    return tuple(args)


def _drive(
    provider: str, channel: str, capability: str, class_name: str, *, boom: bool = False
) -> dict[str, Any]:
    """让执行器按自己的分支构造那一个低层客户端，并把构造参数截下来。"""

    _Recorder.captured = {}
    _Recorder.events = []
    _Recorder.boom = boom
    plan = QueryPlanner().compile(
        QuerySpec.build(
            capability,
            provider=provider,
            channel=channel,
            options={"args": list(_payload_for(provider, channel, capability)), "kwargs": {}},
        )
    )
    executor = DirectProviderExecutor(
        timeout=EXECUTOR_TIMEOUT, hosts=PINNED_HOSTS, config=PROBE_CONFIG
    )
    try:
        with patch(f"{client_face.__name__}.{class_name}", _recorder(class_name)):
            executor._migrated_capability(plan)
    except _Boom:
        #: boom 模式下实现抛错是设定动作，不是判据失败。
        pass
    finally:
        _Recorder.boom = False
    return _Recorder.captured


def _drive_events(cell: tuple[str, str, str, str], *, boom: bool = False) -> list[str]:
    """驱动一格，返回低层客户端上发生的动作序列（构造参数不关心）。"""

    provider, channel, capability, class_name = cell
    _drive(provider, channel, capability, class_name, boom=boom)
    return list(_Recorder.events)


@pytest.mark.parametrize(
    "cell",
    _CELLS,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _CELLS],
)
def test_a_pinned_host_list_reaches_every_family_client(cell: tuple[str, str, str, str]) -> None:
    provider, channel, capability, class_name = cell
    got = _drive(provider, channel, capability, class_name)
    positional = got["args"]
    hosts = positional[0] if positional else got["kwargs"].get("hosts")
    assert hosts == PINNED_HOSTS, (
        f"{class_name}（{provider}/{channel}/{capability}）没有收到调用方钉住的主站列表："
        f"收到 {hosts!r} —— 同一个执行器手里的 self.hosts 在这一格被丢掉了"
    )


@pytest.mark.parametrize(
    "cell",
    _CELLS,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _CELLS],
)
def test_every_family_client_gets_the_translated_pool_settings(
    cell: tuple[str, str, str, str],
) -> None:
    provider, channel, capability, class_name = cell
    got = _drive(provider, channel, capability, class_name)
    missing = [key for key in PROBE_SETTINGS if key not in got["kwargs"]]
    assert not missing, (
        f"{class_name}（{provider}/{channel}/{capability}）的构造处没有把配置翻译成传输层参数，"
        f"缺 {missing} —— 这些键在这一条路上不存在"
    )
    actual = _settings_reading(got["kwargs"])
    expected = _settings_reading(PROBE_SETTINGS)
    wrong = {
        key: (actual[key], expected[key])
        for key in PROBE_SETTINGS
        if key != "timeout" and actual[key] != expected[key]
    }
    assert not wrong, f"{class_name}（{capability}）的传输旋钮取值与配置不一致：{wrong}"


@pytest.mark.parametrize(
    "cell",
    _CELLS,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _CELLS],
)
def test_the_hop_timeout_still_wins_over_the_configured_one(
    cell: tuple[str, str, str, str],
) -> None:
    """``timeout`` 是本轮唯一**该**覆盖配置值的键：一逻辑请求一份预算读数。"""

    provider, channel, capability, class_name = cell
    got = _drive(provider, channel, capability, class_name)
    assert got["kwargs"].get("timeout") == pytest.approx(EXECUTOR_TIMEOUT), (
        f"{class_name}（{capability}）的 socket 超时不是执行器算出来的那一跳："
        f"收到 {got['kwargs'].get('timeout')!r}，应为 {EXECUTOR_TIMEOUT}"
    )


@pytest.mark.parametrize(
    "cell",
    _CELLS,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _CELLS],
)
def test_every_family_client_is_released_exactly_once(cell: tuple[str, str, str, str]) -> None:
    """释放口径①：正常路径上家族客户端**恰好**被显式 ``close()`` 一次。

    握着 ``hosts`` 与 ``config`` 构造出来的对象自带连接池和心跳线程，漏掉 close 就是漏掉
    一条线程（G41 判据 5 数的是武装点，数不到这种"用完根本没人收"的格子）。写成 ``with``
    会先留下 ``enter``/``exit`` 两个动作，所以这一格同时钉住释放**次数**与释放**形状**：
    构造 → 调用 → close，中间不经过上下文协议。
    """

    events = _drive_events(cell)
    calls = [name for name in events if name.startswith("call:")]
    assert calls, f"这一格根本没驱动到实现方法：{events}"
    assert events == calls + ["close"], (
        f"{cell[3]}（{cell[2]}）的释放形状不是'调用后恰好一次 close'：{events} —— "
        "多出 enter/exit 说明分支回到了 with，重复 close 或没有 close 说明收尾口径分了家"
    )


@pytest.mark.parametrize(
    "cell",
    _CELLS,
    ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in _CELLS],
)
def test_a_raising_implementation_still_releases_the_client(
    cell: tuple[str, str, str, str],
) -> None:
    """释放口径②：实现抛错时同样恰好一次 ``close()``——``finally`` 才给得起这个保证。

    这一格就是修复本身的动机：``with self._tdx_client(hop) as client`` 把构造与
    ``__enter__`` 都留在 try 之外，而 :meth:`_pool_client` 交回来的对象**已经**带着池和心跳
    线程；``__enter__`` 一抛就没有任何人 close 它。异常路径上有没有收尾，只有让实现真抛一次
    才量得到。
    """

    events = _drive_events(cell, boom=True)
    assert events.count("close") == 1, f"实现抛错后没人收尾（或收了两次）：{events}"
    assert "enter" not in events and "exit" not in events, f"异常路径走了上下文协议：{events}"


def test_the_family_denominator_is_closed() -> None:
    """射程闭合：执行器里每一个家族类都必须有格子被量到。"""

    families = set(_executor_family_classes())
    assert families, "一个家族低层客户端都没扫到，判据自身失明"
    covered = {cell[3] for cell in _CELLS}
    assert covered == families, (
        f"执行器构造了 {sorted(families - covered)}，但绑定表里没有一格被驱动到它——"
        "这一族的传输口径没人量过"
    )


def test_no_family_client_is_constructed_outside_the_pool_home() -> None:
    """形状闭合：家族类在执行器里只许被 ``_pool_client`` 一处构造。

    本轮这条断链的产生方式就是"每个分支自己 new 一个"，所以量完读数还要量构造点。
    名字口径：家族类本身、以及"绑定了家族类（或家族类字典）的局部名字"，都不得成为
    被调用者——否则 ``cls = {...}[backend]; cls(timeout=hop)`` 这种换皮写法能绕过纯名字判据。
    """

    tree = ast.parse(EXECUTOR_SOURCE)
    families = set(_executor_family_classes())
    #: 形如 ``x = FamilyName`` / ``x = FamilyName`` 作为字典值出现 的局部名字。
    aliased: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        if names & families or (isinstance(value, ast.Dict) and names & families):
            aliased.add(target.id)
    direct = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in (families | aliased)
    ]
    assert aliased, "扫不到任何家族别名，说明这一判据的口径已经和项目对不上"
    assert not direct, (
        f"执行器直接在 {direct} 上 new 了低层客户端 —— 主站与传输旋钮的口径又分了家，"
        "改走 DirectProviderExecutor._pool_client"
    )


#: 家族客户端的两个构造出口：从这两处交出去的对象自带连接池/心跳线程。
_CLIENT_HOMES = frozenset({"_tdx_client", "_pool_client"})

#: 全执行器唯一那道受保护使用区。
_GUARD = "_client_session"

#: 保护区与两个构造出口自身的实现体不算"使用点"。
_HOME_FUNCS = frozenset({_GUARD, *_CLIENT_HOMES})

#: 不import 自 ``tstdx.client``、因此不在派生家族集合里，但同样按跳构造并需收尾的那一个。
_SESSION_CLASSES = frozenset({"WebQuoteSession"})


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _client_handoffs(tree: ast.AST) -> tuple[list[ast.AST], list[ast.AST]]:
    """把执行器里每一次"交出一个可关闭客户端"分成进过保护区的与没进的。"""
    parents = _parent_map(tree)

    def enclosing_func(node: ast.AST) -> str | None:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current.name
            current = parents.get(current)
        return None

    def guarded(node: ast.AST) -> bool:
        current = parents.get(node)
        while current is not None:
            if (
                isinstance(current, ast.Call)
                and isinstance(current.func, ast.Attribute)
                and current.func.attr == _GUARD
            ):
                return True
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return False
            current = parents.get(current)
        return False

    names = set(_executor_family_classes()) | _SESSION_CLASSES
    protected: list[ast.AST] = []
    bare: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = (isinstance(node.func, ast.Attribute) and node.func.attr in _CLIENT_HOMES) or (
            isinstance(node.func, ast.Name) and node.func.id in names
        )
        if not called or enclosing_func(node) in _HOME_FUNCS:
            continue
        (protected if guarded(node) else bare).append(node)
    return protected, bare


def test_every_client_handoff_sits_behind_the_guarded_session() -> None:
    """第 4 件事：客户端的交出点只许待在 ``_client_session`` 保护区里。

    31-B4 那次只收了迁移分支三条，核心链路还留着九处 ``with self._tdx_client(...) as
    client:``——同一种"构造已完成、收尾还没接管"的形状在同一份文件里给两种答案。这条尺子
    量的正是那个形状：``with`` 直接包构造出口就是红，不需要数分支怎么写。
    """

    protected, bare = _client_handoffs(ast.parse(EXECUTOR_SOURCE))
    assert protected, "扫不到任何进保护区的交出点：这一判据自身失明"
    assert not bare, (
        f"{len(bare)} 处客户端交出没进保护区（行号 {sorted(n.lineno for n in bare)}）——"
        f"改走 with self.{_GUARD}(self._tdx_client(...)) as client:"
    )


def test_the_guard_releases_in_a_finally() -> None:
    """保护区自身：``yield`` 在 try 里，``close()`` 在 finally 里，且只关一次。"""

    fn = next(
        node
        for node in ast.walk(ast.parse(EXECUTOR_SOURCE))
        if isinstance(node, ast.FunctionDef) and node.name == _GUARD
    )
    trials = [node for node in fn.body if isinstance(node, ast.Try)]
    assert len(trials) == 1, f"保护区里有 {len(trials)} 个 try，形状已不是'交出→finally 释放'"
    trial = trials[0]
    yields = [y for stmt in trial.body for y in ast.walk(stmt) if isinstance(y, ast.Yield)]
    assert yields, "yield 不在 try 里"
    assert trial.finalbody, "保护区没有 finally —— 异常路径上就没人收尾"
    closes = [
        call
        for stmt in trial.finalbody
        for call in ast.walk(stmt)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "close"
    ]
    assert len(closes) == 1, f"收尾动作数成 {len(closes)}，与'恰好一次'不是同一件事"


def test_planted_unguarded_handoff_is_caught() -> None:
    """正控：把修复前那一形状（``with self._tdx_client(...)``）喂给尺子，它必须报红。"""

    before = (
        "class E:\n"
        "    def _tdx_quotes(self):\n"
        "        with self._tdx_client(1.0) as c:\n"
        "            return c.quotes(['000001'])\n"
    )
    protected, bare = _client_handoffs(ast.parse(before))
    assert protected == [] and len(bare) == 1, "尺子看不见 with 形状，判据 4 就是空的"

    after = before.replace(
        "with self._tdx_client(1.0)", "with self._client_session(self._tdx_client(1.0))"
    )
    protected, bare = _client_handoffs(ast.parse(after))
    assert len(protected) == 1 and bare == []


#: 包内交出家族客户端的两道保护区：执行器那处（``_client_session``）与 CLI 那处。
_PACKAGE_GUARDS = frozenset({_GUARD, "family_client"})

#: 实现体里出现构造出口不算越界：两个构造出口、两道保护区、公开工厂。
_PACKAGE_HOMES = _PACKAGE_GUARDS | _CLIENT_HOMES | frozenset({"get_client"})


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _family_outlets() -> frozenset[str]:
    """包内每一个"交出一个已建池、需收尾的客户端"的名字：家族类 + 两个构造出口 + 工厂。"""
    return frozenset(
        set(_executor_family_classes()) | _SESSION_CLASSES | _CLIENT_HOMES | {"get_client"}
    )


def _with_wrapped_family_handoffs(source: str) -> list[int]:
    """扫一份源码，报出 ``with`` 直接包住家族构造出口、又没进保护区的行号。"""
    tree = ast.parse(source)
    parents = _parent_map(tree)
    outlets = _family_outlets()

    def enclosing_func(node: ast.AST) -> str | None:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current.name
            current = parents.get(current)
        return None

    def in_guard(node: ast.AST) -> bool:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.Call) and _called_name(current) in _PACKAGE_GUARDS:
                return True
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return False
            current = parents.get(current)
        return False

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With | ast.AsyncWith):
            continue
        for item in node.items:
            expr = item.context_expr
            if not isinstance(expr, ast.Call) or _called_name(expr) not in outlets:
                continue
            if enclosing_func(node) in _PACKAGE_HOMES or in_guard(expr):
                continue
            offenders.append(node.lineno)
    return offenders


def test_no_family_client_is_handed_to_a_with_anywhere_in_the_package() -> None:
    """第 5 件事：这条口径的射程是整个包，不是只有执行器（31-C4）。

    31-C3 把执行器的 13 处收进 ``_client_session`` 之后，按族再扫一遍全包，露出同一形状
    在 CLI 面还剩 6 处（``probe`` / ``blocks`` / ``list`` / ``quotes-snapshot`` 四处
    ``with TdxClient(**conn)``、``goods`` / ``f10`` 两处 ``with get_client(...)``）：构造
    已经完成、连接池与心跳线程已起跑，``__enter__``（即 ``open()``）却落在 try 之外。
    一把只扫 ``executor.py`` 的尺子看不见换了一张面的同一件事。
    """

    offenders: list[str] = []
    for path in sorted(_PKG_ROOT.rglob("*.py")):
        for line in _with_wrapped_family_handoffs(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(_PKG_ROOT).as_posix()}:{line}")
    assert offenders == [], (
        f"{len(offenders)} 处家族客户端被 ``with`` 直接接管（{offenders}）——"
        "改走 with self._client_session(...) / with family_client(...)"
    )


def test_planted_cli_handoff_is_caught() -> None:
    """正控：CLI 修复前那一形状（``with get_client(...)``）喂给尺子必须报红，改后必须绿。"""

    before = (
        "def _cmd_f10(args):\n"
        "    with get_client('f10', hosts=None, timeout=1.0) as c:\n"
        "        return c.catalog('000001')\n"
    )
    assert _with_wrapped_family_handoffs(before) == [2], "尺子看不见 CLI 面那一格"

    after = before.replace("with get_client(", "with family_client(")
    assert _with_wrapped_family_handoffs(after) == []


def test_planted_blindness_is_caught() -> None:
    """正控：本轮修复前那一格实际收到的就是这个形状，尺子必须认得出来。"""

    bare = {"args": (), "kwargs": {"timeout": EXECUTOR_TIMEOUT}}
    full = {"args": (PINNED_HOSTS,), "kwargs": {**PROBE_SETTINGS, "timeout": EXECUTOR_TIMEOUT}}

    def carries(got: dict[str, Any]) -> bool:
        hosts = got["args"][0] if got["args"] else got["kwargs"].get("hosts")
        return hosts == PINNED_HOSTS and all(key in got["kwargs"] for key in PROBE_SETTINGS)

    assert not carries(bare), "判据把'只传了 timeout'也当成合格 —— 它看不见本轮这条断链"
    assert carries(full)


def test_planted_release_blindness_is_caught() -> None:
    """正控：把 ``with`` 那一形状喂给释放尺子，它必须认不出来是"合格"。

    释放两格量的是动作序列，所以这里比对动作序列即可——不需要真的把执行器改回 ``with``
    才能知道尺子看得见什么。
    """

    def releases_once(events: list[str]) -> bool:
        calls = [name for name in events if name.startswith("call:")]
        return bool(calls) and events == calls + ["close"]

    assert not releases_once(["enter", "call:quotes", "exit"]), (
        "把上下文协议当成合格释放 —— 这一尺子就看不见 with 形状下 __enter__ 抛错没人收尾"
    )
    assert not releases_once(["call:quotes"]), "漏掉释放动作也算合格，异常路径那一格就白设了"
    assert not releases_once(["call:quotes", "close", "close"]), "收了两次也算合格？"
    assert releases_once(["call:download", "call:parse_text", "close"])
