# B11（REFACTOR_PLAN_v7 §2.9）：多阶段构建
#   builder 阶段：装 dev 依赖跑测试冒烟（测试失败 → 镜像构建失败）
#   runtime 阶段：仅含 tstdx 运行代码，剔除 tests/ / PROTOCOL_SPEC/ / docs/
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY tstdx/ tstdx/
COPY tests/ tests/

RUN pip install --no-cache-dir ".[all]" pytest \
    && python -m pytest tests/ -m "not network" --tb=short -q -p no:warnings \
    && tstdx --help >/dev/null


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system tstdx && adduser --system --ingroup tstdx tstdx

WORKDIR /app

# 仅安装运行时代码（不 COPY tests/ PROTOCOL_SPEC/ docs/，体积与攻击面最小化）
COPY --from=builder /build/pyproject.toml /build/README.md /build/LICENSE ./
COPY --from=builder /build/tstdx/ tstdx/
RUN pip install --no-cache-dir --no-deps ".[all]" \
    && tstdx --help >/dev/null

USER tstdx

CMD ["tstdx", "--help"]
