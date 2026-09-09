.PHONY: help install test test-live test-unit test-integration test-bridges test-golden test-slow lint lint-fix type-check clean gates audit-originality audit-golden audit-spec audit-reachability audit-adversarial audit-docs benchmark-smoke native-compat host-audit build publish docker-build docker-run

PYTHON ?= python
PIP = $(PYTHON) -m pip
PYTEST = $(PYTHON) -m pytest
RUFF = $(PYTHON) -m ruff
MYPY = $(PYTHON) -m mypy

help:
	@echo "tstdx project commands"
	@echo ""
	@echo "Development:"
	@echo "  make install         Install the CI-equivalent dev/test environment"
	@echo "  make lint            Ruff lint + format check"
	@echo "  make type-check      mypy with the same CI flags"
	@echo "  make test            Offline test suite with coverage >= 77"
	@echo "  make gates           Run the deterministic PR merge-gate suite"
	@echo ""
	@echo "Operational probes (not PR merge gates):"
	@echo "  make test-live       Run network-marked tests"
	@echo "  make host-audit      Run strict live TDX host-pool audit"
	@echo ""
	@echo "Build/release:"
	@echo "  make build           Isolated wheel+sdist build + clean-venv smoke"
	@echo "  make docker-build    Build and self-test the source Docker image"
	@echo "  make publish         Disabled locally; publish via GitHub Release/OIDC"

install:
	$(PIP) install -e ".[all,dev]" build twine

test:
	$(PYTEST) tests/ -v --tb=short -m "not network" \
		--cov=tstdx \
		--cov-report=term-missing \
		--cov-report=xml:coverage.xml \
		--cov-fail-under=77

test-live:
	$(PYTEST) tests/ -v --tb=short -m "network" --junitxml=reports/live-smoke.xml

test-unit:
	$(PYTEST) tests/unit/ -v --tb=short

test-integration:
	$(PYTEST) tests/unit/test_integration_offline.py -v --tb=short

test-bridges:
	$(PYTEST) tests/test_bridges.py -v --tb=short

test-golden:
	$(PYTEST) tests/ -v --tb=short -m "golden"

test-slow:
	$(PYTEST) tests/ -v --tb=short -m "slow"

lint:
	$(RUFF) check tstdx/ tests/ scripts/
	$(RUFF) format --check tstdx/ tests/ scripts/

lint-fix:
	$(RUFF) check --fix tstdx/ tests/ scripts/
	$(RUFF) format tstdx/ tests/ scripts/

type-check:
	$(MYPY) tstdx/ --ignore-missing-imports --no-error-summary --warn-unused-ignores

audit-originality:
	$(PYTHON) -m tstdx.tools.check_originality --strict tstdx/

audit-golden:
	$(PYTHON) -m tstdx.tools.golden_audit --gate --require-markets \
		--require-kline-categories 0,4,9 --require-payloads

audit-spec:
	$(PYTHON) -m tstdx.tools.spec_audit --json --strict

audit-reachability:
	$(PYTHON) scripts/audit_reachability.py --strict

audit-adversarial:
	$(PYTEST) tests/adversarial -q --tb=short

audit-docs:
	$(PYTHON) scripts/check_docs_links.py

benchmark-smoke:
	$(PYTHON) scripts/run_benchmark_smoke.py

native-compat:
	$(PYTEST) tests/compatibility/test_native_fallback_contract.py -q --tb=short

host-audit:
	$(PYTHON) scripts/audit_hosts.py --timeout 2 --workers 24 --strict \
		--report audit_report.json --markdown audit_summary.md

# Deterministic PR merge gates only. Network probes remain separate by design.
# This mirrors the blocking CI jobs without weakening thresholds or strict flags.
gates: lint type-check test test-bridges audit-golden audit-spec audit-adversarial audit-reachability audit-originality benchmark-smoke audit-docs native-compat
	@echo "ALL DETERMINISTIC GATES PASSED"

build:
	$(PYTHON) scripts/build_package.py --smoke

# PyPI publishing is intentionally centralized in .github/workflows/wheels.yml,
# where tag/version identity, 12-cell wheel smoke and OIDC trusted publishing are
# enforced. A local twine shortcut would bypass those provenance gates.
publish:
	@echo "Direct local publishing is disabled. Publish a matching GitHub Release tag to use the OIDC release workflow."
	@exit 2

docker-build:
	docker build -t tstdx:latest .

docker-run:
	docker run --rm tstdx:latest tstdx --help

clean:
	rm -rf __pycache__/ tstdx/__pycache__/ tstdx/*/__pycache__/
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/
	rm -rf build/ dist/ *.egg-info/
	rm -rf .coverage htmlcov/ reports/
	rm -f coverage.xml audit_report.json audit_summary.md
	rm -f benches/results/ci_smoke.json benches/results/ci_time_smoke.json
