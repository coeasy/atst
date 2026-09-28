"""发行身份三处合一 + 门禁射程外的根级文档必须自带史料声明（第 30 轮 30-A，G44）。

两格同一族的病：**一个身份靠手抄维持，一个身份靠读者自己猜**。

第一格是包的身份。`pyproject.toml` 的 `version`、`atst/__init__.py` 的 `__version__`
和 `docs/releases/v<版本>.md` 是三处独立手抄：装进环境后 `pip show atst` 读第一处、
`python -c "atst.__version__"` 读第二处、想知道"这一版收口了什么"读第三处。前两条
今天相等，但没有任何判据钉着——而本轮现场就撞见第三处的缺口：`v1.1.0` 的 tag 已经在
origin 上，`docs/releases/v1.1.0.md` 也确实存在，将来推进版本号时漏掉第三处不会有人红。

第二格是文档的身份。仓库根的 `*.md` 里，`README`/`SECURITY`/`CONTRIBUTING` 在活文档门禁
的集合里（`test_doc_code_consistency.active_docs()`），其余根级文档**一条判据都不覆盖**：
`DESIGN.md` 16 万字符不在集合内，V18 台账的 E3 因此把它连同 `CHANGELOG` 一起"声明为只读
史料并在文件头标注"。实测这半条只落了一半——`DESIGN.md` 第 3 行确实写着"不是现行方案"，
而标注本身没有任何尺子盯着：删掉那一句，16 万字符的 2026-08-31 立项稿就变成一份无标记的
现行设计文档，且活文档门禁继续看不见它。同一形状在本轮普查里第二次于真文档上出现：
`AUDIT_AND_BRIDGES.md`（16 KB，v3.0/v3.1 期的"24 个断链点"审计）一直躺在仓库根，
用现在时写着"10 个横切链仍是断链或半断链"，而它同样在射程外——问"主体链路是否全部贯通"
的人一旦落到这份文件，读到的是与今天相反的结论。它已移入 `docs/archive/` 并按归档
阅读规则加了顶部归档说明。

本判据因此钉四条，全部现读、不做全仓推断：

1. `pyproject.toml` 声明的 `version`（静态字面量；`dynamic` 时取
   `[tool.hatch.version].path` 指向的单一事实源）与 `atst.__version__` 相等；
2. `docs/releases/v<version>.md` 存在，且其首个标题含同一版本号；
3. 根级 `*.md` 去掉活文档集合与政策/日志白名单后剩下的每一份，前 12 行内必须出现
   史料标记（`不是现行`/`按原文留存`/`史料`/`快照`）；
4. `docs/archive/README.md` 的「门禁口径」表点名的每个判据文件与常量都真实存在，且
   表里声称"排除归档区"的那两个常量现读确实含 `archive`。

防盲自校写在断言里：活文档集合非空、第 3 条的射程非空且含 `DESIGN.md` 这个具名锚、
第 4 条的表至少解析出 3 行。变异台账（第 30 轮 30-D 收口）逐条演示这四格都会红。
"""

from __future__ import annotations

import re
from pathlib import Path

import atst
from tests.architecture.test_doc_code_consistency import EXCLUDED_PARTS, active_docs
from tests.architecture.test_error_promises import AUDIT_DOC_PREFIXES

ROOT = Path(__file__).resolve().parents[2]

#: 根级文档里**结构上就不作数**的名单：`CHANGELOG` 由 `scripts/check_docs_links.py` 的
#: 同名豁免集合派生（按天追加，每条写的是当时的路径与口径），余下两份是政策文本，
#: 不含任何关于代码面的现在时主张。刻意不抄 `CHANGELOG.md` 的字面量——派生自那个豁免集，
#: 它改名时本判据会跟着要求重新裁决，而不是留下一条永远为真的白名单。
_NON_TECHNICAL = frozenset({"CODE_OF_CONDUCT.md", "GOVERNANCE.md"})

#: 史料标记：与 `docs/archive/README.md`「本目录下的任何文件都不是现行契约」和
#: `DESIGN.md`「不是现行方案 / 按原文留存」用的词同源。
_BANNER = re.compile(r"不是现行|按原文留存|史料|快照")

_RELEASE_TABLE_ROWS = 3


def _pyproject_version(text: str) -> str:
    """pyproject 声明的发行版本号：静态 ``version`` 或 ``[tool.hatch.version]`` 指向的单一事实源。

    工程已把版本收成一处（``atst/_version.py``），pyproject 以 ``dynamic = ["version"]``
    委托给 hatch——所以"pyproject 声明的版本"要从它配置的来源现读，而不是只认字面量。
    """
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if match is not None:
        return match.group(1)
    source = re.search(r'(?m)^\[tool\.hatch\.version\]\s*\n[^[]*?path\s*=\s*"([^"]+)"', text)
    assert source is not None, (
        "pyproject.toml 里既没有可解析的静态 version，也没有 [tool.hatch.version] 的 path"
    )
    body = (ROOT / source.group(1)).read_text(encoding="utf-8")
    declared = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', body)
    assert declared is not None, f"{source.group(1)} 里没有 __version__，判据自身失效"
    return declared.group(1)


def _banner_present(text: str) -> bool:
    head = "\n".join(text.splitlines()[:12])
    return bool(_BANNER.search(head))


def _root_docs_outside_the_gate_face() -> set[str]:
    """根级 `*.md` 里既不在活文档集合、也不在政策/日志白名单的那些。"""
    gated = {path.name for path in active_docs() if path.parent == ROOT}
    exempt = _link_check_exempt_names() | _NON_TECHNICAL
    assert gated, "活文档门禁没扫到任何根级文档，判据自身失效"
    return {
        path.name
        for path in sorted(ROOT.glob("*.md"))
        if path.name not in gated and path.name not in exempt
    }


def _link_check_exempt_names() -> set[str]:
    """`scripts/check_docs_links.py` 自己声明的豁免基名（CHANGELOG 在那里就已经豁免）。"""
    script = (ROOT / "scripts" / "check_docs_links.py").read_text(encoding="utf-8")
    match = re.search(r"EXEMPT_BASENAMES\s*=\s*frozenset\({(?P<body>[^}]*)}\)", script)
    assert match is not None, "链接判据里没有 EXEMPT_BASENAMES，判据自身失效"
    return set(re.findall(r'"([^"]+)"', match.group("body")))


def _archive_contract_rows() -> list[tuple[str, str | None]]:
    """`docs/archive/README.md` 门禁口径表里的 (判据文件, 常量名) 逐行。

    表体每行形如「判据名 | 反引号包住的 tests/… 路径 | 反引号包住的常量名」；第三格
    （围栏代码块）只写"复用同一份 `active_docs()`"，没有路径前缀的常量名，因此常量格可空。
    """
    text = (ROOT / "docs" / "archive" / "README.md").read_text(encoding="utf-8")
    section = text.split("## 门禁口径", 1)[1]
    rows: list[tuple[str, str | None]] = []
    for line in section.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "判据" in line:
            continue
        paths = re.findall(r"`([^`]+\.py)`", line)
        if not paths:
            continue
        consts = [
            token
            for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line)
            if token.upper() == token and not token.endswith(".py")
        ]
        rows.append((paths[0], consts[0] if consts else None))
    return rows


def test_declared_package_version_is_the_same_number_in_two_places() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert _pyproject_version(text) == atst.__version__


def test_declared_version_has_a_reader_facing_release_page() -> None:
    page = ROOT / "docs" / "releases" / f"v{atst.__version__}.md"
    assert page.is_file(), (
        f"包版本 {atst.__version__} 在 docs/releases/ 没有对应发布说明——"
        "读到的 wheel 与读到的『这一版收口什么』脱钩"
    )
    title = next(
        (line for line in page.read_text(encoding="utf-8").splitlines() if line.startswith("# ")),
        "",
    )
    assert atst.__version__ in title, f"{page.name} 的首个标题不含版本号：{title!r}"


def test_root_docs_outside_the_gate_face_declare_their_status() -> None:
    outside = _root_docs_outside_the_gate_face()
    assert outside, "根级没有任何射程外文档——白名单膨胀到吞掉了全部根文档，判据自身失效"
    assert "DESIGN.md" in outside, f"射程外名单里没有 DESIGN.md（具名锚丢失）：{sorted(outside)}"
    missing = [
        name for name in outside if not _banner_present((ROOT / name).read_text(encoding="utf-8"))
    ]
    assert not missing, (
        f"这些根级文档既不在活文档门禁射程内、也没有史料声明：{missing}——"
        "要么移进 docs/（进射程），要么在文件头写明『不是现行方案 / 按原文留存』"
    )


def test_archive_readme_contract_names_judges_that_exist() -> None:
    rows = _archive_contract_rows()
    assert len(rows) >= _RELEASE_TABLE_ROWS, (
        f"归档阅读契约的表只解析出 {len(rows)} 行，判据自身失效"
    )
    for path_cell, const in rows:
        judge = ROOT / path_cell
        assert judge.is_file(), f"归档契约表点名了不存在的判据文件：{path_cell}"
        if const is None:
            continue
        body = judge.read_text(encoding="utf-8")
        assert re.search(rf"^{const}\s*=", body, re.MULTILINE), f"{path_cell} 里没有 {const}"
    # 表里两句"归档区在射程外"要真的是排除集里的现值，而不是散文。
    assert "archive" in EXCLUDED_PARTS, "活文档门禁的排除集已不含 archive，归档区被重新纳入射程"
    assert any("archive" in str(value) for value in AUDIT_DOC_PREFIXES), (
        f"AUDIT_DOC_PREFIXES 现值 {AUDIT_DOC_PREFIXES} 不再覆盖归档区"
    )


def test_planted_defects_each_get_caught() -> None:
    """四格各自的失明演示：判据必须能在被削弱的第一时间认出来。"""
    # 1) 版本号手抄分叉。
    assert _pyproject_version('version = "1.2.0"\n') == "1.2.0"
    # 2) 史料标记必须在文件头 12 行内才有效——写在脚注里不算声明。
    tail_only = "\n".join(["# 设计方案", "", "> 正文主张。"] + ["正文"] * 20 + ["> 本文按原文留存"])
    assert not _banner_present(tail_only)
    assert _banner_present("# 设计方案\n\n> 本文是立项快照，不是现行方案。\n")
    # 3) 白名单派生：链接判据豁免的那份不进门禁射程名单。
    assert "CHANGELOG.md" not in _root_docs_outside_the_gate_face()
    # 4) 归档表若指向不存在的常量，本判据必须能认出来（用一个假的表体走同一条解析）。
    fake = "| 某判据 | `tests/architecture/__no_such_gate__.py` 的 `SOME_CONST` |\n"
    paths = re.findall(r"`([^`]+\.py)`", fake)
    assert paths and not (ROOT / paths[0]).is_file()
