# Build and test one repository-owned wheel, then install that exact artifact in
# the runtime image. The builder receives the complete repository contract after
# `.dockerignore` filtering; the runtime stage receives only the tested wheel.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# `.dockerignore` is the single build-context boundary. Copying the whole filtered
# repository prevents new contract tests from silently failing because a newly
# referenced Makefile/workflow/config/fixture was forgotten in a selective COPY.
COPY . .

# build/twine are packaging validators, not general test dependencies, so keep
# them explicit here instead of inflating the dev extra used by every CI cell.
RUN python -m pip install --no-cache-dir -e ".[all,dev]" build twine \
    && python -m pytest tests/ -m "not network" --tb=short -q -p no:warnings \
    && atst --help >/dev/null \
    && python -m build --wheel \
    && python -m twine check dist/*.whl


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system atst && adduser --system --ingroup atst atst

WORKDIR /app

# Core atst has zero mandatory third-party runtime dependencies. Install the
# exact wheel that passed the builder tests; optional extras remain opt-in for
# downstream images instead of being ambiguously requested with --no-deps.
COPY --from=builder /build/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-deps /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && atst --help >/dev/null \
    && python -m pip check

USER atst

CMD ["atst", "--help"]
