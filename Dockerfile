# Build and test one repository-owned wheel, then install that exact artifact in
# the runtime image. The runtime stage contains no source tree, tests, docs or
# build backend and therefore cannot silently rebuild a different package.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Package inputs plus container/release contract files consumed by the offline
# compatibility tests that run inside this builder.
COPY pyproject.toml README.md CHANGELOG.md LICENSE Dockerfile Dockerfile.release .dockerignore ./
COPY tstdx/ tstdx/

# Offline test/audit inputs used by the normal non-network suite. Keep these out
# of the runtime stage; they exist only so Docker validates the same repository
# contracts as CI instead of an incomplete synthetic checkout.
COPY tests/ tests/
COPY scripts/ scripts/
COPY PROTOCOL_SPEC/ PROTOCOL_SPEC/
COPY docs/ docs/
COPY .github/workflows/ .github/workflows/

# build/twine are packaging validators, not general test dependencies, so keep
# them explicit here instead of inflating the dev extra used by every CI cell.
# They must be installed before `python -m build` is invoked.
RUN python -m pip install --no-cache-dir -e ".[all,dev]" build twine \
    && python -m pytest tests/ -m "not network" --tb=short -q -p no:warnings \
    && tstdx --help >/dev/null \
    && python -m build --wheel \
    && python -m twine check dist/*.whl


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system tstdx && adduser --system --ingroup tstdx tstdx

WORKDIR /app

# Core tstdx has zero mandatory third-party runtime dependencies. Install the
# exact wheel that passed the builder tests; optional extras remain opt-in for
# downstream images instead of being ambiguously requested with --no-deps.
COPY --from=builder /build/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-deps /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && tstdx --help >/dev/null \
    && python -m pip check

USER tstdx

CMD ["tstdx", "--help"]
