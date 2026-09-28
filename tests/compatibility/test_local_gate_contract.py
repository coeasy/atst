from __future__ import annotations

import re
from pathlib import Path

from tests.support.gate_inventory import (
    GATES_TO_CI_JOBS,
    ci_check_cells,
    ci_job_keys,
    ci_test_matrix_cells,
    gates_targets,
)

_ROOT = Path(__file__).resolve().parents[2]

#: 门禁脚本里写死的仓内路径（Makefile 与 workflow 共用同一判定）。
_REPO_PATH_TOKENS = re.compile(r"(?:tests|scripts|atst)/[A-Za-z0-9_/]+\.py")


def _makefile() -> str:
    return (_ROOT / "Makefile").read_text(encoding="utf-8")


def _pyproject() -> str:
    return (_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_install_uses_ci_equivalent_dependency_ssot() -> None:
    makefile = _makefile()

    assert '$(PIP) install -e ".[all,dev]" "pre-commit==4.6.2"' in makefile
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

    v16 Phase 2 删掉 `atst/native.py` 与其契约测试后，`native-compat` target 仍指向
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


#: `[tool.ruff.lint.per-file-ignores]` 的键：形如 `"atst/cli/__init__.py" = ["F401"]`。
_PER_FILE_IGNORE = re.compile(r'^"(?P<path>[^"]+)"\s*=\s*\[', re.MULTILINE)


def test_ruff_per_file_ignores_still_point_at_existing_paths() -> None:
    """每一条 lint 豁免必须落在磁盘上真实存在的路径上。

    豁免指向已删除的文件不是无害的冗余：它让那条规则对**下一个**占用该路径的文件
    静默失效，而没人会想到去看一份不存在的对象的配置。`atst/integration/mcp_server.py`
    就是 MCP 面迁进 `atst/integration/mcp/` 之后留下的死键。
    """
    text = _pyproject()
    section = text.split("[tool.ruff.lint.per-file-ignores]", 1)
    assert len(section) == 2, "per-file-ignores 段不存在，判据自身失效"
    body = re.split(r"\n\[", section[1], maxsplit=1)[0]
    paths = _PER_FILE_IGNORE.findall(body)
    assert len(paths) >= 4, f"只扫到 {len(paths)} 条豁免，说明解析自身失效了"
    missing = [relative for relative in paths if not (_ROOT / relative.rstrip("*")).exists()]
    assert not missing, f"ruff 豁免指向磁盘上不存在的路径：{missing}"


# --------------------------------------------------------------------------- #
# 门禁分母的唯一口径（第 27 轮 A3，V19 §3 B-3 / §6 D-5）
# --------------------------------------------------------------------------- #


def test_make_gates_and_ci_jobs_are_the_same_check_set() -> None:
    """发布口径只有一套：`make gates` 的目标与 `ci.yml` 的作业按键一一对应，两边都不许多。

    B-3 的病不是"数字对不上"，而是**没有任何一条判据拥有那个分母**——台账连用 5 轮的"17 道
    门禁"其实是本机 scratch runner 的步数（其中 ruff 算两次），`make gates` 的 11 个目标和
    CI 展开的 check 格数各是另一本账。本步把 11 这套升成唯一发布口径，并用一张显式映射表
    钉住它：加一个本地目标必须同时说出 CI 上谁跑它，加一个 CI 作业必须说出本地链路谁复现它。
    """
    targets = gates_targets()
    jobs = ci_job_keys()
    assert sorted(targets) == sorted(GATES_TO_CI_JOBS), (
        f"`make gates` 目标与本步锁定的映射表不符："
        f"多 {sorted(set(targets) - set(GATES_TO_CI_JOBS))}、"
        f"缺 {sorted(set(GATES_TO_CI_JOBS) - set(targets))}"
    )
    dangling = sorted(set(GATES_TO_CI_JOBS.values()) - set(jobs))
    assert dangling == [], f"映射表指向 CI 里不存在的作业键：{dangling}"
    unmirrored = sorted(set(jobs) - set(GATES_TO_CI_JOBS.values()))
    assert unmirrored == [], f"CI 有确定性作业没被本地链路镜像（本地全绿≠CI 全绿）：{unmirrored}"
    assert len(set(jobs)) == len(jobs), "CI 作业键有重复，映射判据自身失效"


def test_the_documented_gate_denominators_are_computed_not_copied() -> None:
    """三个分母必须能由 `tests/support/gate_inventory.py` 现算：11 / 11 / 17。

    这条看着像在钉数字，实际钉的是"数字有没有唯一算处"：矩阵加一格、CI 加一个作业，
    这里报的是现算值，改的人顺势把口径一起改；而手抄的"17"没有任何地方能重算它，
    所以它不配当分母——V19 §0 从此只把 runner 步数称作"本轮 runner 的 N 步"。
    """
    assert len(gates_targets()) == len(ci_job_keys()) == len(GATES_TO_CI_JOBS) == 11, (
        f"发布口径的三处计数不再相等：gates {len(gates_targets())}、"
        f"CI 作业 {len(ci_job_keys())}、映射表 {len(GATES_TO_CI_JOBS)}"
    )
    assert ci_test_matrix_cells() == 7, f"test 矩阵现算为 {ci_test_matrix_cells()} 格"
    assert ci_check_cells() == 17, f"CI check 格现算为 {ci_check_cells()}"
