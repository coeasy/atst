"""RetryAdvice 逐字段：每个字段要么有人按它行动，要么文档显式承认没有。

第 25 轮量出 `max_retries` 写着"pool 重试上限"而全仓无执行方（台账 G39），第 26 轮
把同一把尺子泛化到七个字段：`note` 当时在 `docs/errors.md` 写着消费者是「各层日志」，
实测包内零读取点，而 `TdxError.to_dict()` 的 advice 字典**恰好漏列本字段**——文档承诺
的出口在唯一序列化点上不存在（F-71）。

三把防盲保险（`tests/support/field_readers.py` 的纪律）由本文件负责：分母取自
`dataclasses.fields`、扫描模块数够大、命中集合非空，尺子自己失明时必须当场变红。
读取点按 **advice 锚定**识别（`<任意>.advice.<字段>`），否则连接池自己的
`self.max_retries`（配置键同名）会把零执行方的字段量成有人读。

第 28 轮按 D3 口径关掉 G39：`max_retries` 从 `RetryAdvice` 物理删除（字段、
`to_dict()` 键、16 处 `max_retries=` 构造实参、反馈载荷那一格），字段名单现为
六个。同一轮修掉本文件的一个盲区——§二 表格原先**按当前字段名过滤**文档行，
所以"文档里还留着一行已删字段"这件事量不出来；现在两向相等（表格行 ⇄ 字段名单），
删一格必须同时删一行，否则本文件变红。
"""

from __future__ import annotations

import ast
import dataclasses
import re

from atst.error_envelope import to_error_envelope
from atst.errors import RetryAdvice, TdxError, ValidationError
from tests.support.field_readers import REPO_ROOT

#: 定义文件自身：`to_dict()` 与 `default_advice=` 都在那里，不算执行方。
DEFINITION_FILE = "atst/errors.py"

#: 文档里表示"没有人按它行动"的措辞（`docs/errors.md` §二表格）。
NO_ACTOR_MARKS = ("无执行方", "无消费方")

DOC_PATH = REPO_ROOT / "docs" / "errors.md"

#: 决策位置：条件、比较、布尔/算术/取反。**不含**字典值与裸返回——把字段抄进
#: 载荷是序列化，不是按它行动（F-71 的形状：`note` 曾被文档写成有人消费）。
_DECISION_NODES = (ast.If, ast.While, ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp)


def _advice_field_reads() -> tuple[int, dict[str, list[tuple[str, int, bool]]]]:
    """扫 ``atst/``：返回 (扫过的模块数, 相对路径 → [(字段, 行号, 是否参与决策)])。

    只认链条里出现 `.advice.` 的属性读取；决策位置指该读取落在条件/比较/算术/取反/返回里，
    纯字典值（序列化）不在其中。
    """

    fields = {item.name for item in dataclasses.fields(RetryAdvice)}
    per_file: dict[str, list[tuple[str, int, bool]]] = {}
    scanned = 0
    for path in sorted((REPO_ROOT / "atst").rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        decisions: set[tuple[int, int]] = set()
        for holder in ast.walk(tree):
            if not isinstance(holder, _DECISION_NODES):
                continue
            for inner in ast.walk(holder):
                if isinstance(inner, ast.Attribute):
                    decisions.add((inner.lineno, inner.col_offset))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in fields:
                continue
            #: 锚点取**被读对象的名字**而不是链条形状：`pool.py` 的真实执行方写成
            #: ``advice = exc.advice`` 之后再读 ``advice.retryable``，只按属性链认锚
            #: 会把执行方量没（第 26 轮第一版现场）。
            if "advice" in ast.unparse(node.value):
                relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
                per_file.setdefault(relative, []).append(
                    (node.attr, node.lineno, (node.lineno, node.col_offset) in decisions)
                )
    return scanned, per_file


def _advice_field_names() -> set[str]:
    return {item.name for item in dataclasses.fields(RetryAdvice)}


def _doc_field_rows() -> dict[str, str]:
    """解析 ``docs/errors.md`` §二的字段契约表：字段名 → 消费方单元格。

    只看 §二 那一张表，并且**不做字段名单过滤**。旧版用当前字段名单当过滤器，
    于是"文档里还写着一个已经删掉的字段"这一向是瞎的——第 28 轮删 `max_retries`
    （台账 G39）时实测：字段与序列化键都删干净后本判据照样绿，因为那一行文档
    被过滤器丢掉了。删字段的代价必须由判据数出来，所以两向相等才是判据。
    """

    text = DOC_PATH.read_text(encoding="utf-8")
    _, _, section = text.partition("## 二、")
    assert section, "docs/errors.md 的 §二 标题没了，本判据失去锚点"
    body = section.split("\n## ", 1)[0]
    rows: dict[str, str] = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        match = re.fullmatch(r"`([a-z_]+)`", cells[0])
        if match:
            rows.setdefault(match.group(1), cells[2])
    return rows


def test_ruler_is_calibrated():
    """尺子必须能分辨"按它行动"和"把它抄进字典"，否则本判据是假绿。"""

    acting = ast.parse("def f(a):\n    if a.advice.retryable:\n        return 1\n")
    copied = ast.parse('def f(a):\n    return {"retryable": a.advice.retryable}\n')

    def _decided(tree: ast.Module) -> list[bool]:
        decisions: set[tuple[int, int]] = set()
        for holder in ast.walk(tree):
            if isinstance(holder, _DECISION_NODES):
                for inner in ast.walk(holder):
                    if isinstance(inner, ast.Attribute):
                        decisions.add((inner.lineno, inner.col_offset))
        return [
            (node.lineno, node.col_offset) in decisions
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "retryable"
        ]

    assert _decided(acting) == [True], "决策位检测失明"
    assert _decided(copied) == [False], "把纯序列化当成了执行方"


def test_scanned_surface_is_not_blind():
    """防盲：扫描面覆盖整个包，且 advice 锚定读取点确实存在。"""

    scanned, per_file = _advice_field_reads()
    assert scanned >= 150, f"只扫了 {scanned} 个模块，尺子在失明状态"
    hits = [item for sites in per_file.values() for item in sites]
    assert hits, "全包零 advice 锚定读取点：锚定规则或包结构变了，本判据失去意义"
    assert "atst/transport/pool.py" in per_file, (
        f"传输层执行方不在命中名单里（尺子失明），命中：{sorted(per_file)}"
    )


def test_every_advice_field_has_actor_or_is_documented_as_having_none():
    """每个字段：有决策位读取点，或在文档里显式写着无执行方 / 只经序列化出口。"""

    _, per_file = _advice_field_reads()
    docs = _doc_field_rows()
    assert docs.keys() == _advice_field_names(), "文档字段表与 RetryAdvice 字段名单分叉"

    executed, undocumented = [], []
    for name in sorted(_advice_field_names()):
        sites = [
            (relative, line, decided)
            for relative, items in per_file.items()
            if relative != DEFINITION_FILE
            for (field, line, decided) in items
            if field == name
        ]
        if any(decided for _, _, decided in sites):
            executed.append(name)
            if any(mark in docs[name] for mark in NO_ACTOR_MARKS):
                undocumented.append(f"{name}: 有执行方却被文档写成 {NO_ACTOR_MARKS}")
            continue
        cell = docs[name]
        honest = any(mark in cell for mark in NO_ACTOR_MARKS) or "to_dict" in cell
        if not honest:
            undocumented.append(f"{name}: 零执行方，但文档消费方写着 {cell!r}")
    assert not undocumented, "\n".join(undocumented)
    assert {"retryable", "backoff", "switch_host"} <= set(executed), (
        f"传输层执行方消失，只剩 {executed}"
    )


def test_to_dict_serializes_every_advice_field():
    """序列化出口必须逐字段齐全：新增字段漏列即红（F-71 的形状）。"""

    advice = TdxError("x", advice=RetryAdvice(note="提示")).to_dict()["advice"]
    assert set(advice) == _advice_field_names(), (
        f"to_dict 的 advice 键 {sorted(advice)} 与字段名单 {sorted(_advice_field_names())} 不一致"
    )
    assert advice["note"] == "提示", "note 未被序列化"


def test_reporter_payload_keys_match_doc_claim():
    """文档写"反馈上报只带两格"，实测必须就是那两格。"""

    tree = ast.parse((REPO_ROOT / "atst" / "feedback" / "reporter.py").read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "advice" for target in node.targets
        ):
            for literal in ast.walk(node):
                if (
                    isinstance(literal, ast.Dict)
                    and all(
                        isinstance(k, ast.Constant) and isinstance(k.value, str)
                        for k in literal.keys
                    )
                    and any(k.value == "retryable" for k in literal.keys)
                ):
                    keys = {k.value for k in literal.keys}
    assert keys == {"retryable", "backoff"}, f"上报载荷实际带 {sorted(keys)}"
    assert "retryable` / `backoff` 两格" in DOC_PATH.read_text(encoding="utf-8"), (
        "文档里的上报格数与实测分叉"
    )


def test_http_mcp_envelope_exposes_only_retryable():
    """文档口径：错误信封只暴露 ``retryable``，不带 advice 字典。"""

    payload = to_error_envelope(ValidationError("x")).to_dict()
    assert payload["code"] == "E1010"
    assert payload["retryable"] is False
    assert "advice" not in payload
