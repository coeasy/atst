"""跨解释器版本的风险判据（支持矩阵 = CPython 3.10–3.14）。

有两类写法只在**较新的 CPython** 上出问题，而本地解释器与旧版本全绿——它们在
``make gates`` 里以"环境特有"的面目出现，最难归因：

1. ``argparse`` 的 ``help`` / ``description`` / ``epilog`` / ``usage`` 里出现**裸 ``%``**。
   这些文本会被 ``%``-格式化（``%(default)s`` 之类占位是合法用法）。旧版本只在
   **渲染** help 时才报错，CPython 3.14 在 ``add_argument`` 阶段就急切校验，抛
   ``ValueError: badly formed help string``——3.14 上整个工具/CLI 直接不可用。
2. ``asyncio.start_server`` 的**同步回调**。3.13 起回调一返回就关闭已接受的连接
   （3.12 不关），把"接受但沉默"的服务端替身悄悄变成"接受后立刻断开"，判据随之
   退化成在测"对端已断"。

判据都按 AST 走，并且直接复用运行时语义（``literal % {}``）而不是正则。
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_DIRS = ("atst", "tests", "scripts")
_FORMAT_KEYWORDS = ("help", "description", "epilog", "usage")
_SERVER_FACTORIES = ("start_server", "start_unix_server")
_ARG_FACTORIES = ("add_argument", "ArgumentParser", "add_parser")


def _sources() -> list[Path]:
    return [
        path
        for base in _SCANNED_DIRS
        for path in sorted((_ROOT / base).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _string_pieces(node: ast.AST) -> list[tuple[int, str]]:
    """展开 f-string 的静态片段，返回 ``(行号, 文本)``。

    相邻字面量由解析器合成一个 ``Constant``，所以隐式拼接不必单独处理。
    """
    return [
        (child.lineno, child.value)
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _is_badly_formed(text: str) -> bool:
    """argparse 渲染时用的就是这条 ``%`` 语义：裸 ``%`` 抛 ``ValueError``。"""
    try:
        text % {}
    except ValueError:
        return True
    except (KeyError, IndexError, TypeError):
        return False  # ``%(default)s`` 之类合法占位
    return False


def test_the_percent_judge_matches_runtime_semantics() -> None:
    """判据自身的变异自测：裸 ``%`` 判红，转义与合法占位判绿。"""
    assert _is_badly_formed("覆盖率 < 100% 时返回非零退出码") is True
    assert _is_badly_formed("覆盖率 < 100%% 时返回非零退出码") is False
    assert _is_badly_formed("%(default)s 为默认值") is False
    assert _is_badly_formed("无百分号") is False


def test_argparse_text_never_carries_a_bare_percent() -> None:
    """CPython 3.14 在 ``add_argument`` 阶段就会抛 ``badly formed help string``。"""
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in _ARG_FACTORIES:
                continue
            for keyword in call.keywords:
                if keyword.arg not in _FORMAT_KEYWORDS:
                    continue
                for lineno, text in _string_pieces(keyword.value):
                    if _is_badly_formed(text):
                        offenders.append(f"{path.relative_to(_ROOT)}:{lineno}: {text!r}")

    assert offenders == [], (
        "argparse 文本里的裸 % 在 CPython 3.14 上是硬错误（badly formed help string），"
        "请把 % 写成 %%：" + "; ".join(offenders)
    )


def _non_coroutine_reason(callback: ast.expr, plain_defs: set[str]) -> str:
    """回调是不是**必然不是协程函数**的那种实参？返回理由，否则返回空串。"""
    if isinstance(callback, ast.Lambda):
        return "lambda 不可能是协程函数"
    if isinstance(callback, ast.Name) and callback.id in plain_defs:
        return f"{callback.id} 是同模块的普通 def，不是 async def"
    return ""


def test_the_server_callback_judge_flags_lambdas_and_plain_defs() -> None:
    """判据自身的变异自测：命中 lambda / 同模块普通 def，放过未知名字。"""
    plain = {"serve", "handle"}

    def expr(source: str) -> ast.expr:
        return ast.parse(source, mode="eval").body

    assert _non_coroutine_reason(expr("lambda r, w: None"), plain) != ""
    assert _non_coroutine_reason(expr("serve"), plain) != ""
    assert _non_coroutine_reason(expr("handle"), plain) != ""
    assert _non_coroutine_reason(expr("keep_alive"), plain) == ""


def test_asyncio_server_callbacks_are_coroutine_functions() -> None:
    """CPython 3.13 起 ``start_server`` 的同步回调会在返回时关闭连接。"""
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        plain_defs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr not in _SERVER_FACTORIES:
                continue
            if not call.args:
                continue
            reason = _non_coroutine_reason(call.args[0], plain_defs)
            if reason:
                offenders.append(f"{path.relative_to(_ROOT)}:{call.lineno}: {reason}")

    assert offenders == [], (
        "asyncio 服务端的同步回调在 CPython 3.13+ 上会于返回时关闭连接，让"
        "「接受但沉默」的替身退化成「接受即断开」；改用 async def 回调或阻塞 "
        "socket 服务端：" + "; ".join(offenders)
    )
