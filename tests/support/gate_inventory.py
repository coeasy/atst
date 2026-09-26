"""门禁清单的唯一解析处：本地 `make gates` 的目标 ↔ CI `ci.yml` 的作业。

第 27 轮（V19 §3 B-3 / §6 D-5）登记的理由：仓里同时流通三个"门禁分母"——本机 scratch
runner 的步数、`make gates` 的目标数、CI 一次运行展开的 check 格数。三个里面只有第二个
同时满足"写在 Makefile 里、被判据钉住、且逐个对得上 CI 作业"，所以自本步起它被当作唯一
的发布口径；另外两个如果还要出现在任何文档里，必须由这里的函数现算，而不是手抄一个数。

单实现是刻意的：`test_local_gate_contract.py` 与文档侧的规模判据此前各抄一份名单，
这正是"抄一次就过期"的形状。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: `make gates` 的每个目标 → `ci.yml` 里镜像它的作业键。
#: 两个方向都不许多：加一个本地目标必须说明 CI 上谁跑它，加一个 CI 作业必须说明本地链路
#: 上谁复现它——否则"本地全绿"与"CI 全绿"就又是两本账。
GATES_TO_CI_JOBS: dict[str, str] = {
    "lint": "lint",
    "type-check": "type-check",
    "test": "test",
    "test-bridges": "bridges",
    "audit-golden": "golden-gate",
    "audit-spec": "spec-coverage",
    "audit-adversarial": "adversarial-matrix",
    "audit-reachability": "reachability",
    "audit-originality": "originality",
    "benchmark-smoke": "benchmark-smoke",
    "audit-docs": "docs-links",
}

#: workflow 里缩进两格的键就是作业键（步骤级键缩进更深，`on:`/`jobs:` 在零格）。
_JOB_KEY = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)


def gates_targets() -> list[str]:
    """`gates:` 那一行点到的前置目标，保持 Makefile 里的顺序。"""
    line = next(
        line
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("gates:")
    )
    targets = line.split(":", 1)[1].split()
    assert targets, "一个 gates 目标都没读到，尺子自身失效"
    return targets


def ci_job_keys() -> list[str]:
    """`ci.yml` 的作业键（不含矩阵展开）。"""
    jobs_section = CI_WORKFLOW.read_text(encoding="utf-8").split("jobs:", 1)[1]
    keys = _JOB_KEY.findall(jobs_section)
    assert len(keys) >= 10, f"只扫到 {len(keys)} 个 CI 作业键，尺子自身失效"
    return keys


def ci_test_matrix_cells() -> int:
    """`test` 作业的矩阵格数：`python-version` 列表 + `include` 追加的额外操作系统。"""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    test_job = text.split("\n  test:", 1)[1].split("\n  originality:", 1)[0]
    versions = re.search(r"python-version:\s*\[([^\]]*)\]", test_job)
    assert versions, "test 作业里没读到 python-version 矩阵，尺子自身失效"
    base = len([item for item in versions.group(1).split(",") if item.strip()])
    includes = len(re.findall(r"^\s+- os: ", test_job, re.M))
    cells = base + includes
    assert cells >= 2, f"test 矩阵只算出 {cells} 格，尺子自身失效"
    return cells


def ci_check_cells() -> int:
    """CI 一次运行在 check 面上展开的格子数：单作业各 1 格，`test` 按矩阵展开。"""
    return len(ci_job_keys()) - 1 + ci_test_matrix_cells()
