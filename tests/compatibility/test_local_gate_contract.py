from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: 门禁脚本里写死的仓内路径（Makefile 与 workflow 共用同一判定）。
_REPO_PATH_TOKENS = re.compile(r"(?:tests|scripts|tstdx)/[A-Za-z0-9_/]+\.py")


def _makefile() -> str:
    return (_ROOT / "Makefile").read_text(encoding="utf-8")


def _pyproject() -> str:
    return (_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_install_uses_ci_equivalent_dependency_ssot() -> None:
    makefile = _makefile()

    assert '$(PIP) install -e ".[all,dev]" build twine' in makefile
    assert 'pip install -e ".[all]"' not in makefile
    assert "pip install pytest pytest-cov ruff mypy build" not in makefile


def test_local_test_gate_keeps_ci_offline_scope_and_single_coverage_source() -> None:
    makefile = _makefile()

    assert "--cov-report=xml:coverage.xml" in makefile
    assert '-m "not network"' in makefile
    # 阈值只写在 pyproject `[tool.coverage.report] fail_under` 一处；
    # 命令行副本会让"改一处仍绿"的漂移重新出现（v17 Phase 6）。
    assert "--cov-fail-under" not in makefile
    assert "fail_under = 77" in _pyproject()


def test_marker_targets_use_valid_pytest_marker_syntax() -> None:
    makefile = _makefile()

    assert "--mark=" not in makefile
    assert '-m "golden"' in makefile
    assert '-m "slow"' in makefile


def test_local_gates_include_all_deterministic_specialized_checks() -> None:
    makefile = _makefile()

    gates_line = next(line for line in makefile.splitlines() if line.startswith("gates:"))
    for target in (
        "lint",
        "type-check",
        "test",
        "test-bridges",
        "audit-golden",
        "audit-spec",
        "audit-adversarial",
        "audit-reachability",
        "audit-originality",
        "benchmark-smoke",
        "audit-docs",
    ):
        assert target in gates_line

    assert "--warn-unused-ignores" in makefile
    assert "spec_audit --json --strict" in makefile
    assert "--require-kline-categories 0,4,9" in makefile


def test_every_gates_prerequisite_is_a_defined_target() -> None:
    """`gates: a b c` 里点到未定义的目标，make 只报"无规则可 make"——链路照跑不误。"""
    makefile = _makefile()
    defined = {
        m.group(1)
        for line in makefile.splitlines()
        if (m := re.match(r"([A-Za-z0-9_.-]+):(\s|$)", line))
    }
    phony = next(
        line.split(":", 1)[1] for line in makefile.splitlines() if line.startswith(".PHONY:")
    ).split()
    gates_line = next(line for line in makefile.splitlines() if line.startswith("gates:"))
    missing = [t for t in gates_line.split(":", 1)[1].split() if t not in defined | set(phony)]
    assert not missing, f"gates 点到未定义的 target：{missing}"


def test_makefile_gate_commands_reference_existing_paths() -> None:
    """门禁命令里写死的仓内路径必须存在。

    v16 Phase 2 删掉 `tstdx/native.py` 与其契约测试后，`native-compat` target 仍指向
    已删除的测试文件，`make gates` 因此在最后一步固定失败——而 CI 里对应的 native.yml
    用 `python -m compileall -q <不存在的路径>`，该命令**打印告警却退出 0**，于是
    "编译"一步静默通过、真正跑测试的下一步才红。
    """
    missing = [
        relative
        for relative in _REPO_PATH_TOKENS.findall(_makefile())
        if not (_ROOT / relative).exists()
    ]
    assert not missing, f"Makefile 引用磁盘上不存在的仓内路径：{missing}"


def test_local_publish_cannot_bypass_trusted_release_workflow() -> None:
    makefile = _makefile()

    assert "Direct local publishing is disabled" in makefile
    assert "twine upload" not in makefile
    assert "@exit 2" in makefile


def test_local_build_uses_safe_smoke_enabled_builder() -> None:
    makefile = _makefile()

    assert "scripts/build_package.py --smoke" in makefile


#: `[tool.ruff.lint.per-file-ignores]` 的键：形如 `"tstdx/cli/__init__.py" = ["F401"]`。
_PER_FILE_IGNORE = re.compile(r'^"(?P<path>[^"]+)"\s*=\s*\[', re.MULTILINE)


def test_ruff_per_file_ignores_still_point_at_existing_paths() -> None:
    """每一条 lint 豁免必须落在磁盘上真实存在的路径上。

    豁免指向已删除的文件不是无害的冗余：它让那条规则对**下一个**占用该路径的文件
    静默失效，而没人会想到去看一份不存在的对象的配置。`tstdx/integration/mcp_server.py`
    就是 MCP 面迁进 `tstdx/integration/mcp/` 之后留下的死键。
    """
    text = _pyproject()
    section = text.split("[tool.ruff.lint.per-file-ignores]", 1)
    assert len(section) == 2, "per-file-ignores 段不存在，判据自身失效"
    body = re.split(r"\n\[", section[1], maxsplit=1)[0]
    paths = _PER_FILE_IGNORE.findall(body)
    assert len(paths) >= 4, f"只扫到 {len(paths)} 条豁免，说明解析自身失效了"
    missing = [relative for relative in paths if not (_ROOT / relative.rstrip("*")).exists()]
    assert not missing, f"ruff 豁免指向磁盘上不存在的路径：{missing}"
