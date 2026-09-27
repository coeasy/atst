"""CLI → 内核的入参接缝（G30，第 24 轮）。

射程：文档里的每一条可执行 CLI 示例，凡落点是 ``内核·typed`` / ``内核·rows`` 的，都在**真实
调用链**上跑一遍——真实 ``Client``、真实 ``QueryPlanner.compile``（``normalized`` →
``_select_channel`` → ``validate_call`` 全部按生产口径执行），唯一被换掉的是内核自己声明的
注入缝 :class:`~atst.runtime.kernel.KernelExecutor`（它的 docstring 就写着 "Injection seam
for tests"）。因此 ``Client.__getattr__`` 那条"除了 provider/channel/currentness，其余关键字
参数一律塞进 options 袋"的转发路，被逐格量到了它自己的终点。落点是 ``元信息`` 的那两条
（``version`` / ``capabilities``）也在射程里，判据反着量：它们按构造碰不到内核，所以一格的
内核调用痕迹都不许留下。

为什么必须有这道门禁（G30 的三处实测，第 23 轮）：``__getattr__`` 返回 :class:`Any`，所以
``api.all_market(source="sina")`` 这类**写错的键名**在 ``mypy`` 里完全不可见，只有真跑一次
才炸；而当时单元测试里的手抄假对象 ``FakeApi.all_market(source=...)`` 跟着 CLI 一起写错，
于是离线全绿、装好的包里是红的。手抄假对象的毛病不是"抄错了"，是**它永远不会不同意**。
本门禁不再抄任何假签名：入参合不合，由内核自己的绑定决定。

与兄弟判据的分工：``test_cli_reference_table`` 管命令名 / 旗标 / 落点三格与 argparse 现值
一致（旗标名对不对），``test_dispatch_targets`` 管 capability → 实现体的绑定表对不对，
本文件管**这两者之间那段没人管的路上，实参能不能落到实现体的签名上**。落点是
``直连传输层`` 的六支命令不在这里——它们经 :func:`atst.client.get_client` 的
``@overload`` 回到具体类型，参数名与类型由 ``mypy``（``check_untyped_defs = true``）钉住，
本文件用 :func:`test_the_transport_leaves_are_the_ones_mypy_can_see` 把这条分工本身量出来，
而不是默认它成立。
"""

from __future__ import annotations

import contextlib
import inspect
import io
import re
import socket
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

from tests.architecture.test_cli_reference_table import (  # noqa: E402
    _group_action,
    _subparsers,
    runtime_leaves,
)
from tests.architecture.test_doc_code_consistency import _cli_examples  # noqa: E402
from atst.client.api import Client  # noqa: E402
from atst.query import QueryPlan, QuerySpec  # noqa: E402
from atst.result import Provenance, QueryResult, ResultMeta  # noqa: E402
from atst.runtime.kernel import UnifiedRuntime  # noqa: E402

#: 落点里经过内核的两类：``Client`` / ``_ClientRows`` 构造点由 ``test_cli_reference_table``
#: 的 ``_landing`` 现读处理器源码判定，这里只消费那份词汇表，不另抄一份名单。
KERNEL_LANDINGS = ("内核·typed", "内核·rows")

#: 按构造碰不到内核的那一类：判据二拿它量**反向**口径（一格内核痕迹都不许留下）。
_META = "元信息"

#: 本轮实测的去重 capability 数下界（清单由本判据现跑现算，读数记在 §33 台账）。
#: 只会随命令面变多而升高；掉下来说明有示例不再真的走到内核（被跳过、被吞异常、或被改成
#: 绕道），而不是"测试还是绿的"。
CAPABILITY_FLOOR = 19


class _Tripped(Exception):
    """socket tripwire：门禁一旦真去连套接字，就是这里。"""


def _no_socket(*args: Any, **kwargs: Any) -> Any:
    raise _Tripped("CLI 接缝门禁碰到了套接字：内核路径必须在注入缝处停下")


class _Recorder:
    """:class:`KernelExecutor` 的替身：只留痕，返回按计划身份构造的空结果。"""

    def __init__(self) -> None:
        self.plans: list[dict[str, Any]] = []
        self.rejected: list[str] = []

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        options = plan.spec.options
        self.plans.append(
            {
                "capability": plan.spec.capability,
                "provider": plan.provider,
                "channel": plan.channel,
                "args": options.get("args"),
                "kwargs": options.get("kwargs"),
            }
        )
        return QueryResult(
            data=[],
            meta=ResultMeta.from_plan(plan, Provenance.direct(plan), ()),
        )

    def close(self) -> None:
        return None


class _SeamedRuntime(UnifiedRuntime):
    """真实编译链，只把内核在执行体边界上的拒绝原话收一份痕。"""

    def __init__(self, recorder: _Recorder, **kwargs: Any) -> None:
        self.recorder = recorder
        super().__init__(executor=recorder, **kwargs)

    def execute(self, spec: QuerySpec) -> QueryResult[Any]:
        try:
            return UnifiedRuntime.execute(self, spec)
        except Exception as exc:  # noqa: BLE001 - 拒绝原因要能被断言
            self.recorder.rejected.append(f"{spec.capability}: {type(exc).__name__}: {exc}")
            raise


class _SeamedClient(Client):
    """真实 ``Client`` 的子类：真实 runtime，只把执行体换成留痕器。

    必须是**类**而不是工厂函数——``cmd_capabilities`` 走 ``Client.capabilities()`` 这一
    类方法入口，替换名字时把它一起换掉就成了 ``AttributeError``（第 24 轮普查第一次跑
    本门禁时踩到，属于仪器自身的错）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args
        recorder = _ACTIVE[0]
        kwargs.pop("runtime", None)
        Client.__init__(self, runtime=_SeamedRuntime(recorder, **kwargs))


#: 当前一次运行的留痕器：``_ClientRows`` 在自己函数体里 import ``Client``，构造点拿不到
#: 参数，只能由这条线程内活跃的引用交给 ``_SeamedClient``。
_ACTIVE: list[_Recorder] = []


def _leaf_of(tokens: list[str]) -> str | None:
    """按 parser 自己的口径还原叶子命令键（旗标插在中间也认得）。"""
    from atst.cli.parser import build_parser

    try:
        args = build_parser().parse_args(tokens)
    except SystemExit:
        return None
    top = getattr(args, "command", None)
    choices = _subparsers()
    if top not in choices:
        return None
    group = _group_action(choices[top])
    if group is None:
        return str(top)
    return f"{top} {getattr(args, group.dest, None)}"


def kernel_examples() -> list[tuple[str, str, list[str], str, str]]:
    """文档示例里落点经过内核 / 元信息的那些行。

    ``(叶子命令, 来源文档, tokens, 处理器名, 落点)``。两类一起收：内核那一类要**留下**调用
    痕迹，元信息那一类（``version``、``capabilities``）按构造碰不到内核，就要**不许留下**
    痕迹——判据二两头都量，所以这张表不是"能跑的跑一下"，是一格一格有判据的。
    """
    specs = runtime_leaves()
    rows: list[tuple[str, str, list[str], str, str]] = []
    for rel, _raw, tokens in _cli_examples():
        if tokens is None:
            continue
        leaf = _leaf_of(tokens)
        spec = specs.get(leaf)  # type: ignore[arg-type]
        if spec is not None and (spec["landing"] in KERNEL_LANDINGS or spec["landing"] == _META):
            rows.append((str(leaf), rel, tokens, str(spec["handler"]), str(spec["landing"])))
    return rows


def run_through_seam(monkeypatch: pytest.MonkeyPatch, tokens: list[str]) -> dict[str, Any]:
    """在真实调用链上跑一行示例，返回留痕与异常。"""
    import atst.cli.runtime_commands as rc

    recorder = _Recorder()

    monkeypatch.setattr(rc, "Client", _SeamedClient)
    monkeypatch.setattr("atst.client.api.Client", _SeamedClient)
    monkeypatch.setattr(rc, "_ClientRows", _rows_with_seam())
    sleeps: list[float] = []
    monkeypatch.setattr(rc.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(socket, "socket", _no_socket)

    from atst.cli.parser import build_parser

    out, err = io.StringIO(), io.StringIO()
    rc_code: Any = None
    tb = ""
    _ACTIVE.append(recorder)
    try:
        args = build_parser().parse_args(tokens)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc_code = args.func(args)
    except BaseException:  # noqa: BLE001 - traceback 是断言对象，不是本函数的失败
        import traceback

        tb = "".join(traceback.format_exc(limit=6))
    finally:
        assert _ACTIVE.pop() is recorder
    return {
        "argv": tokens,
        "rc": rc_code,
        "plans": list(recorder.plans),
        "rejected": list(recorder.rejected),
        "traceback": tb,
        "slept": sleeps,
    }


def _rows_with_seam() -> type:
    """``_ClientRows`` 在自己内部 import ``Client``，所以给它同一个缝。"""
    import atst.cli.runtime_commands as rc

    class Rows(rc._ClientRows):  # type: ignore[misc, valid-type,no-untyped-def]
        def __init__(self, *, timeout: float | None = None, hosts: Any = None) -> None:
            self._client = _SeamedClient(hosts=hosts, timeout=timeout)

    return Rows


# ---------------------------------------------------------------------------
# 判据一：没有任何一行文档示例在内核接缝上断掉
# ---------------------------------------------------------------------------


def test_no_documented_cli_line_breaks_on_the_kernel_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = kernel_examples()
    assert rows, "文档里找不到任何经过内核的 CLI 示例，本门禁失效"
    broken: list[str] = []
    for leaf, rel, tokens, _handler, _landing in rows:
        result = run_through_seam(monkeypatch, tokens)
        if result["traceback"]:
            broken.append(
                f"{leaf} (`{' '.join(tokens)}`, {rel}) → traceback\n{result['traceback']}"
            )
        for message in result["rejected"]:
            broken.append(f"{leaf} (`{' '.join(tokens)}`, {rel}) → 内核拒绝: {message}")
    assert not broken, "CLI 递交给内核的实参在真实绑定处断了：\n" + "\n".join(broken)


# ---------------------------------------------------------------------------
# 判据二：尺子自己不失明——读数下界与 socket 隔离
# ---------------------------------------------------------------------------


def test_the_seam_actually_carried_this_many_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """尺子自己不失明：内核那一类逐行要留下痕迹，元信息那一类逐行要**不**留下痕迹。

    这里不抄读数下界当分母。真正的分母是**本门禁自己的射程**：文档里每一条落点经过内核的
    示例，跑完必须至少留下一次内核调用记录——少一行示例分母跟着少一，某行明明还在文档里却
    一次调用都不再产生（被跳过、被吞异常、被改成绕道），这里当场点名是哪一行。

    反方向同一条判据管：落点为 ``元信息`` 的那些行（``version``、``capabilities``）按构造
    就不该走内核，留下任何一次 ``runtime.execute`` 痕迹都说明处理器与文档表那一格分叉了。
    这条反向读数与 ``test_cli_reference_table`` 的 ``_landing`` 是一对：那边改成"只借类名
    也算内核"，这边就会有一行变成空跑；那边把 ``capabilities`` 判回内核，这边它就成了空跑——
    两头都关不上，就不存在"把处理器换成一句 print 也算连通"的第三种躲法。
    """
    rows = kernel_examples()
    silent: list[str] = []
    leaked: list[str] = []
    capabilities: set[str] = set()
    for leaf, rel, tokens, handler, landing in rows:
        result = run_through_seam(monkeypatch, tokens)
        capabilities.update(str(item["capability"]) for item in result["plans"])
        if landing == _META:
            if result["plans"]:
                leaked.append(
                    f"{leaf} (`{' '.join(tokens)}`, 落点 {landing}) "
                    f"留下内核痕迹 {sorted(str(p['capability']) for p in result['plans'])}"
                )
            continue
        if not result["plans"]:
            silent.append(
                f"{leaf} (`{' '.join(tokens)}`, {rel}, 处理器 {handler}) 一次内核调用都没走到"
            )
    assert not silent, "这些示例在内核接缝上变成了空跑：\n" + "\n".join(silent)
    assert not leaked, "这些元信息命令竟然走到了内核：\n" + "\n".join(leaked)
    assert len(capabilities) >= CAPABILITY_FLOOR, (
        f"经接缝送达的 capability 只有 {sorted(capabilities)}，少于下界 {CAPABILITY_FLOOR} 个"
    )


def test_the_battery_never_touches_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入缝之上的路径必须真的不联网：碰到 socket 就是假绿。

    ``run_through_seam`` 会把异常收进 traceback / rejected，所以这里**读**那两格里有没有
    ``_Tripped`` 的原话，而不是等它抛出——等抛出等于把判据变成一条永远走不到的 except。
    """
    tripped: list[str] = []
    for leaf, _rel, tokens, _handler, _landing in kernel_examples():
        result = run_through_seam(monkeypatch, tokens)
        for message in [*result["rejected"], result["traceback"]]:
            if "_Tripped" in message:
                tripped.append(f"{leaf} (`{' '.join(tokens)}`): {message}")
    assert not tripped, "CLI 内核接缝门禁里出现了真实套接字调用：\n" + "\n".join(tripped)


# ---------------------------------------------------------------------------
# 判据三：种下的坏关键字必须被同一条路抓住（含第 23 轮那次回归）
# ---------------------------------------------------------------------------


def test_a_planted_wrong_keyword_is_caught_by_the_real_reconciler() -> None:
    """三种形状各自都要红，否则判据一只是"恰好没坏"的观测。"""
    from atst.catalog.capability import default_provider_for
    from atst.query import QueryPlanner

    planner = QueryPlanner()

    def reject(capability: str, args: list[Any], kwargs: dict[str, Any]) -> str:
        spec = QuerySpec.build(
            capability,
            provider=default_provider_for(capability),
            options={"args": args, "kwargs": kwargs},
        )
        try:
            planner.compile(spec)
        except Exception as exc:  # noqa: BLE001 - 要的就是异常原话
            return f"{type(exc).__name__}: {exc}"
        return ""

    caught = {
        #: 第 23 轮装好包那遍的真实回归：``--source`` 被当成 capability 入参递下去。
        "all_market(source=...)": reject("all_market", [], {"source": "sina"}),
        #: 少一个必填位置参数。
        "adjusted_bars()": reject("adjusted_bars", [], {}),
        #: 拼错的键名（改一个字母）。
        "hot_rank(pge=...)": reject("hot_rank", [], {"pge": 1}),
    }
    missed = [name for name, message in caught.items() if not message]
    assert not missed, f"内核绑定对这些坏入参放了行：{missed}"


# ---------------------------------------------------------------------------
# 判据四：分母——每支经过内核的叶子都得有一行示例真的走到内核
# ---------------------------------------------------------------------------


def test_every_kernel_landing_leaf_is_driven_by_a_documented_line() -> None:
    """新增一支内核命令而没有示例，这里当场红（G32 的"被点名 ≠ 被跑过"残余）。"""
    driven = {leaf for leaf, _rel, _tokens, _handler, _landing in kernel_examples()}
    kernel_leaves = {
        leaf for leaf, spec in runtime_leaves().items() if spec["landing"] in KERNEL_LANDINGS
    }
    missing = sorted(kernel_leaves - driven)
    assert not missing, "这些内核命令在文档里没有可执行示例：" + "、".join(missing)


# ---------------------------------------------------------------------------
# 判据五：分工本身要被量出来
# ---------------------------------------------------------------------------


def test_the_transport_leaves_are_the_ones_mypy_can_see() -> None:
    """两个前提都要被量出来，不许当常识。

    ①本门禁存在的理由：一支 ``内核·rows`` 的能力在 :class:`Client` 上**没有**声明方法，
       它经 ``__getattr__`` 以 :class:`Any` 转发，所以 ``mypy`` 看不见键名——第 23 轮那次
       ``source=`` 正是这样在全绿的离线套件里装进包的。哪天有人把它们写成显式方法，这个
       前提变了，本判据先红，再决定这段射程要不要搬进类型层。
    ②``直连传输层`` 那六支交给 ``mypy`` 的理由：:func:`atst.client.get_client` 的每个
       ``kind`` 都有 ``@overload`` 回到具体类（不是 ``Any``），参数名与类型因此可静态检查。
       重载名单与注册表必须逐 ``kind`` 配对，多一个 ``kind`` 少一条重载都会在这里红。
    """
    landings = {leaf: spec["landing"] for leaf, spec in runtime_leaves().items()}
    rows_leaves = sorted(leaf for leaf in landings if landings[leaf] == "内核·rows")
    assert rows_leaves, "没有 内核·rows 的叶子，前提①无从量起"
    declared = [
        leaf for leaf in rows_leaves if hasattr(Client, leaf.replace("-", "_").replace(" ", "_"))
    ]
    assert not declared, (
        "这些 rows 叶子在 Client 上已有声明方法，__getattr__ 盲区前提已变，"
        f"本门禁的射程要重新判定：{declared}"
    )

    from atst.client import factory

    source = inspect.getsource(factory)
    registry_kinds = set(factory._CLIENT_REGISTRY)  # type: ignore[attr-defined]
    overload_kinds = {match for match in re.findall(r'kind:\s*Literal\["([^"]+)"\]', source)}
    assert overload_kinds == registry_kinds, (
        f"get_client 的重载面与注册表不再逐 kind 配对："
        f"只注册未重载={sorted(registry_kinds - overload_kinds)}，"
        f"只重载未注册={sorted(overload_kinds - registry_kinds)}"
    )
    #: 每一条 ``@overload`` 都要回到具体类名，回到 ``Any`` 就等于把六支命令重新推回盲区。
    #: 实现体自己那条 `-> Any` 不算重载，把它从读数里剔出去——它是签名声明，不是类型承诺。
    returns = re.findall(r"^def get_client\([^)]*\)\s*->\s*(\w+):", source, re.M)
    concrete = [r for r in returns if r != "Any"]
    assert len(concrete) == len(registry_kinds), (
        f"get_client 只有 {len(concrete)} 条重载标了具体返回类型（返回类型读数 {returns}），"
        f"注册表却有 {len(registry_kinds)} 个 kind"
    )
