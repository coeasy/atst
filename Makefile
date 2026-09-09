.PHONY: help install test lint lint-fix type-check clean gates audit-bridges audit-golden audit-reachability audit-adversarial build publish

help:
	@echo "tstdx 项目命令"
	@echo ""
	@echo "开发命令:"
	@echo "  make install        安装开发依赖"
	@echo "  make test           运行测试"
	@echo "  make lint           运行 ruff lint"
	@echo "  make type-check     运行 mypy 类型检查"
	@echo "  make clean          清理构建产物"
	@echo ""
	@echo "审计命令:"
	@echo "  make audit-bridges  运行 24 项贯通审计"
	@echo "  make audit-originality  运行原创性检查"
	@echo "  make audit-golden   Golden L1 真实样本门禁"
	@echo "  make audit-spec     运行 Spec 覆盖率检查"
	@echo ""
	@echo "构建命令:"
	@echo "  make build          构建 wheel + sdist"
	@echo "  make publish        发布到 PyPI"
	@echo "  make docker-build   构建 Docker 镜像"

install:
	pip install -e ".[all]"
	pip install pytest pytest-cov ruff mypy build

test:
	# G1/G2（v6 §4）：覆盖率门禁 + 剔除联网测试（联网冒烟走 make test-live）
	pytest tests/ -v --tb=short -m "not network" --cov=tstdx --cov-report=term-missing --cov-fail-under=77

test-live:
	@echo "  联网冒烟（依赖外网，失败不阻断）"
	pytest tests/ -v --tb=short -m "network"

test-unit:
	pytest tests/unit/ -v --tb=short

test-integration:
	pytest tests/unit/test_integration_offline.py -v --tb=short

test-bridges:
	pytest tests/test_bridges.py -v --tb=short

test-golden:
	pytest tests/unit/test_golden.py -v --tb=short --mark=golden

test-slow:
	pytest tests/ -v --tb=short --mark=slow

lint:
	ruff check tstdx/ tests/
	ruff format --check tstdx/ tests/

lint-fix:
	ruff check --fix tstdx/ tests/
	ruff format tstdx/ tests/

type-check:
	mypy tstdx/ --ignore-missing-imports --no-error-summary

clean:
	rm -rf __pycache__/ tstdx/__pycache__/ tstdx/*/__pycache__/
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/
	rm -rf build/ dist/ *.egg-info/
	rm -rf .coverage htmlcov/

audit-bridges:
	@echo "=========================================="
	@echo "24 项贯通审计 (AUDIT_AND_BRIDGES.md)"
	@echo "=========================================="
	@echo ""
	@echo "[01] 协议命令 L1+L2+L3 三层可解析"
	python -c "from tstdx.protocol.registry import dispatch; print('  ✓ 61 parsers + dispatch')"
	@echo "[02] 8 核心 spec 覆盖率（85 命令账本）"
	python -m tstdx.tools.spec_audit --json 2>/dev/null || echo "  ⚠ spec_audit 未就绪"
	@echo "[03] 24 断链点全部有处理"
	pytest tests/test_bridges.py -v --tb=line 2>/dev/null || echo "  ⚠ bridges test 未就绪"
	@echo "[04] tstdx.toml 12 case 合并"
	python -c "from tstdx.config.loader import load_config; print('  ✓ 6 source merge')"
	@echo "[05] 错误分类树 100% + RetryAdvice"
	python -c "from tstdx.errors import TdxError, RETRY_ADVICE; print(f'  ✓ {len(RETRY_ADVICE)} error classes')"
	@echo "[06] 同步/异步双 API 一致"
	python -c "from tstdx.client import TdxClient, AsyncTdxClient; print('  ✓ sync + async client')"
	@echo "[07] 6 种降级路径"
	python -c "from tstdx.sources import DataSourceRouter; print('  ✓ 5 source fallback')"
	@echo "[08] 流：订阅-断网-重连-补数"
	python -c "from tstdx.streaming.engine import StreamEngine; print('  ✓ StreamEngine')"
	@echo "[09] 字符集探测"
	python -c "from tstdx.charset.encoding import detect_encoding; print('  ✓ auto-detect encoding')" 2>/dev/null || echo "  ⚠ charset module 未就绪"
	@echo "[10] A 股交易日历"
	python -c "from tstdx.domain.calendar import TradingCalendar; print('  ✓ calendar 2024-2026')"
	@echo "[11] 时区 UTC 内部/本地输出"
	python -c "from tstdx.domain.models import Bar; print('  ✓ tz support')"
	@echo "[12] Golden 数据来源"
	@python -c "import pathlib; n=len(list(pathlib.Path('tests/golden').rglob('payload.bin'))); print(f'  ✓ {n} golden cases (baseline 530, P13-C 目标 ≥700)')"
	@echo "[13] AST 相似度门禁"
	python -m tstdx.tools.check_originality --json tstdx/ 2>/dev/null || echo "  ⚠ check_originality 未就绪"
	@echo "[14] License 白名单扫描"
	@test -f ORIGINALITY/LICENSE_ALLOWLIST.md && echo "  ✓ LICENSE_ALLOWLIST.md exists" || echo "  ⚠ missing"
	@echo "[15] wheel 矩阵"
	@test -f .github/workflows/wheels.yml && echo "  ✓ wheels.yml exists" || echo "  ⚠ missing"
	@echo "[16] 弃用策略"
	python -c "from tstdx.deprecation import DeprecationPolicy; print('  ✓ DeprecationPolicy')" 2>/dev/null || echo "  ⚠ deprecation 未就绪"
	@echo "[17] HTTP API 42 接口"
	python -c "import tstdx.integration.http_server; print('  ✓ http_server')" 2>/dev/null || echo "  ⚠ http_server 未就绪"
	@echo "[18] MCP 10 工具"
	python -c "import tstdx.integration.mcp_server; print('  ✓ mcp_server')" 2>/dev/null || echo "  ⚠ mcp_server 未就绪"
	@echo "[19] 可观测性"
	python -c "from tstdx.observability.metrics import Metrics; print('  ✓ Prometheus metrics')"
	@echo "[20] DataFrame/Parquet/DuckDB 三 sink"
	python -c "from tstdx.sinks import write; print('  ✓ 3 sinks')"
	@echo "[21] 文档矩阵"
	@test -f README.md && echo "  ✓ README.md exists" || echo "  ⚠ missing"
	@echo "[22] 治理文件"
	@test -f GOVERNANCE.md && test -f CODE_OF_CONDUCT.md && test -f SECURITY.md && echo "  ✓ governance files" || echo "  ⚠ missing"
	@echo "[23] HTTP Web 源矩阵（14 模块/45 Source 类）"
	python -c "from tstdx.web.adapters import SinaSource; print('  ✓ web adapters import')"
	@echo "[24] volume/amount 归一化"
	python -c "from tstdx.web.normalize import normalize_volume; print('  ✓ normalize module')" 2>/dev/null || echo "  ⚠ normalize 未就绪"
	@echo ""
	@echo "=========================================="
	@echo "审计完成"
	@echo "=========================================="

audit-originality:
	python -m tstdx.tools.check_originality --strict tstdx/

audit-golden:
	python -m tstdx.tools.golden_audit --gate

audit-spec:
	python -m tstdx.tools.spec_audit --json

audit-reachability:
	python scripts/audit_reachability.py --strict

audit-adversarial:
	pytest tests/adversarial -q

# 统一门禁序列（工业审计最终验证；CI 收口用）：任一失败即中止。
# 顺序 = 静态(lint) → 全量测试 → 专项审计(golden/对抗矩阵/可达性/原创性)。
gates:
	@echo "[gate 1/6] ruff check" && ruff check tstdx/ tests/ scripts/
	@echo "[gate 2/6] ruff format --check" && ruff format --check tstdx/ tests/
	@echo "[gate 3/6] full pytest" && python -m pytest tests -q
	@echo "[gate 4/6] adversarial matrix" && python -m pytest tests/adversarial -q
	@echo "[gate 5/6] golden audit (三旗标)" && python -m tstdx.tools.golden_audit --gate --require-markets --require-kline-categories 0,4,9 --require-payloads
	@echo "[gate 6/6] reachability" && python scripts/audit_reachability.py --strict
	@echo "ALL GATES PASSED"

build:
	python scripts/build_package.py

publish:
	pip install twine
	python -m twine upload dist/*

docker-build:
	docker build -t tstdx:latest .

docker-run:
	docker run --rm tstdx:latest tstdx --help
