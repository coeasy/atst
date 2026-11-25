# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""量纲系数单源化守卫（P1-8「量纲系数单源化仍延后」的收口）。

数据契约：**price = 元、volume = 股、amount = 元**。换算系数只允许写在
:mod:`atst.sources._scale` 一处，其余模块一律 import 常量。本文件做三件事：

1. 钉住 ``_scale`` 各常量的值——改动等于行为变更，必须显式确认；
2. 扫描 ``atst/sources/**/*.py``（``_scale.py`` 除外），出现裸缩放字面量即失败；
3. 断言每个已注册 normalizer 的系数只能由具名常量组成，新源无法自造系数。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from atst.sources import _scale
from atst.sources.normalize import _NORMALIZER_REGISTRY
from atst.sources.sources import KNOWN_SOURCES

_SOURCES_DIR = Path(__file__).resolve().parents[2] / "atst" / "sources"
_SCALE_MODULE = _SOURCES_DIR / "_scale.py"


# --------------------------------------------------------------------------- #
# 1. 常量值锚点
# --------------------------------------------------------------------------- #
def test_scale_constant_values() -> None:
    """系数值即行为；任何改动都必须伴随显式的行为评审。"""
    assert _scale.SHARES == 1.0
    assert _scale.YUAN == 1.0
    assert _scale.HAND_TO_SHARE == 100.0
    assert _scale.WAN_TO_YUAN == 10_000.0
    assert _scale.FEN_INT_TO_YUAN == 1.0 / 100.0
    assert _scale.MILLI_INT_TO_YUAN == 1.0 / 1000.0
    # 派生除数：用于「除以 1000」的既有调用点，必须精确等于 1000.0
    assert _scale.MILLI_INT_DIVISOR == 1000.0
    assert _scale.YI_TO_YUAN == 100_000_000.0
    assert _scale.CENT_INT_DIVISOR == 100.0
    assert _scale.DECIMAL_TO_PERCENT == 100.0


# --------------------------------------------------------------------------- #
# 2. 源码扫描守卫
# --------------------------------------------------------------------------- #
#: 只认「被乘/被除的缩放字面量」这些形状。不扫裸 ``100`` / ``10000``，
#: 以免把 ``page_size=100``、``tm // 10000`` 时间戳、``max_pages=100`` 误判。
_SCALE_TOKEN = re.compile(
    r"""
    \*\s*100(?:\.0)?(?![\d_.])                 # ×100：手→股 / 小数→百分数
  | \*\s*10_000(?:\.0)?(?![\d_.])              # ×10000：万元→元
  | \*\s*10000(?:\.0)?(?![\d_.])
  | (?<![/])/\s*100(?:\.0)?(?![\d_.])          # ÷100：×100 整数→十进制
  | (?<![/])/\s*1000(?:\.0)?(?![\d_.])         # ÷1000：×1000 整数价→元
  | (?<![\d_.])1\s*/\s*100(?:\.0)?(?![\d_.])   # 1/100：×100 整数价→元
  | (?<![\d_.])1\s*/\s*1000(?:\.0)?(?![\d_.])
  | \belse\s+100(?:\.0)?(?![\w_.])             # 三元市场分支：A 股 100 / 港美 1
    """,
    re.VERBOSE,
)

#: ``*_scale`` 直接赋数值字面量（SourceSpec 与 VolumeNormalizer 两层）。
_SCALE_ASSIGN = re.compile(
    r"\b(?:price|volume|amount|vol)_scale\b\s*(?::\s*float\s*)?=\s*(?P<rhs>[^=].*)$"
)
#: 只有「纯数字表达式」才算裸字面量；``HAND_TO_SHARE`` 之类的名字放行。
_NUMERIC_RHS = re.compile(r"[\d_.\s/()]+")

#: 显式白名单：形似缩放、但语义不是量纲换算的既有代码。
#: 键为相对 ``atst/sources/`` 的路径，值为「归一化后的整行代码」（空白折叠）。
#: 每条都必须写明为什么不是量纲系数——白名单条目被改动后会自动失效。
#: 当前为空：全部换算点（含资金占比、百分比、比率）都已改为具名常量，
#: 机制保留以便个别真例外登记时仍受「行必须存在」的反向校验。
_ALLOWED_LINES: dict[str, tuple[str, ...]] = {}


def _code_lines(path: Path) -> list[tuple[int, str, str]]:
    """(行号, 原样代码行, 抹掉字符串后的代码行)，均已去注释。

    字符串字面量（含 docstring）用空格等宽抹掉后再判定：文档里的
    「×100 / 1/1000」是说明文字，不是代码；原样行只用于白名单比对与报错展示。
    """
    source = path.read_text(encoding="utf-8")
    raw_lines = source.splitlines()
    chars = [list(line) for line in raw_lines]
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        start = node.lineno - 1
        end = (node.end_lineno or node.lineno) - 1
        if start == end:
            _blank(chars[start], node.col_offset, node.end_col_offset)
            continue
        _blank(chars[start], node.col_offset, None)
        for row in range(start + 1, end):
            chars[row] = [" "] * len(chars[row])
        _blank(chars[end], 0, node.end_col_offset)
    out: list[tuple[int, str, str]] = []
    for lineno, (raw, row) in enumerate(zip(raw_lines, chars, strict=True), 1):
        raw_code = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].rstrip()
        code = re.split(r"(?:^|\s)#", "".join(row), maxsplit=1)[0].rstrip()
        if code.strip():
            out.append((lineno, raw_code, code))
    return out


def _blank(row: list[str], start: int, end: int | None) -> None:
    stop = len(row) if end is None else min(end, len(row))
    for index in range(start, stop):
        row[index] = " "


def _normalize(code: str) -> str:
    return re.sub(r"\s+", " ", code).strip()


def _violations(path: Path) -> list[str]:
    rel = path.relative_to(_SOURCES_DIR).as_posix()
    allowed = {_normalize(line) for line in _ALLOWED_LINES.get(rel, ())}
    found: list[str] = []
    for lineno, raw_code, code in _code_lines(path):
        if _normalize(raw_code) in allowed:
            continue
        match = _SCALE_ASSIGN.search(code)
        rhs = match.group("rhs").strip().rstrip(",").rstrip() if match else ""
        if rhs and _NUMERIC_RHS.fullmatch(rhs):
            found.append(f"{rel}:{lineno}: *_scale 赋数值字面量 -> {raw_code.strip()}")
            continue
        token = _SCALE_TOKEN.search(code)
        if token:
            found.append(f"{rel}:{lineno}: 裸缩放字面量 {token.group(0)!r} -> {raw_code.strip()}")
    return found


def test_no_bare_scale_literals_outside_scale_module() -> None:
    """除 ``_scale.py`` 外，``atst/sources`` 下不得再出现裸缩放字面量。"""
    bad: list[str] = []
    for path in sorted(_SOURCES_DIR.rglob("*.py")):
        if path == _SCALE_MODULE:
            continue
        bad.extend(_violations(path))
    assert not bad, "量纲系数只允许写在 atst/sources/_scale.py；请改为 import 常量：\n" + "\n".join(
        bad
    )


def test_allowlist_entries_are_still_present() -> None:
    """白名单必须与源码同步：行已消失/改写即失败，防止它悄悄变成免检通道。"""
    stale: list[str] = []
    for rel, lines in _ALLOWED_LINES.items():
        path = _SOURCES_DIR / rel
        present = {_normalize(raw_code) for _, raw_code, _ in _code_lines(path)}
        stale.extend(f"{rel}: {line}" for line in lines if _normalize(line) not in present)
    assert not stale, "白名单条目已失效，请删除对应条目：\n" + "\n".join(stale)


# --------------------------------------------------------------------------- #
# 3. 已注册 normalizer 只能用具名常量
# --------------------------------------------------------------------------- #
_VOLUME_FACTORS = {_scale.SHARES, _scale.HAND_TO_SHARE}
_AMOUNT_FACTORS = {_scale.YUAN, _scale.WAN_TO_YUAN}
_PRICE_FACTORS = {_scale.YUAN, _scale.FEN_INT_TO_YUAN, _scale.MILLI_INT_TO_YUAN}


def test_normalizer_scales_are_composed_of_named_constants() -> None:
    """新 normalizer 不能自造系数：三项系数必须落在具名常量的闭集里。"""
    bad: list[str] = []
    for source, ncls in sorted(_NORMALIZER_REGISTRY.items()):
        if ncls.volume_scale not in _VOLUME_FACTORS:
            bad.append(f"{source}: volume_scale={ncls.volume_scale!r}")
        if ncls.amount_scale not in _AMOUNT_FACTORS:
            bad.append(f"{source}: amount_scale={ncls.amount_scale!r}")
        if ncls.price_scale not in _PRICE_FACTORS:
            bad.append(f"{source}: price_scale={ncls.price_scale!r}")
    assert not bad, "normalizer 系数必须来自 atst.sources._scale：\n" + "\n".join(bad)


def test_source_spec_scales_are_composed_of_named_constants() -> None:
    """SourceSpec 与 normalizer 同受一条约束（spec 是同一批系数的声明面）。"""
    bad: list[str] = []
    for source, spec in sorted(KNOWN_SOURCES.items()):
        if spec.volume_scale not in _VOLUME_FACTORS:
            bad.append(f"{source}: volume_scale={spec.volume_scale!r}")
        if spec.amount_scale not in _AMOUNT_FACTORS:
            bad.append(f"{source}: amount_scale={spec.amount_scale!r}")
        if spec.price_scale not in _PRICE_FACTORS:
            bad.append(f"{source}: price_scale={spec.price_scale!r}")
    assert not bad, "SourceSpec 系数必须来自 atst.sources._scale：\n" + "\n".join(bad)
