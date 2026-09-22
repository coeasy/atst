# FAQ 常见问题

## 安装与依赖

### Q: tstdx 需要安装哪些依赖？

核心运行时**零硬依赖**。按需安装 extras：

> **当前安装路径**（G9，2026-09-22 实测）：本包不在 PyPI 上
> （`https://pypi.org/pypi/tstdx/json` 回 404），所以下表这些写法要等上架后才能直接
> 执行。现在请在本仓库根目录用 `pip install ".[extra]"`（把 `tstdx` 换成 `.`），
> extras 的名字完全相同；README「安装」一节给的是同一口径。

| 需求 | 安装 |
|---|---|
| DataFrame 输出 | `pip install "tstdx[dataframe]"` |
| Parquet 落地 | `pip install "tstdx[parquet]"` |
| DuckDB 落地 | `pip install "tstdx[duckdb]"` |
| HTTP Web 源降级 | `pip install "tstdx[web]"` |
| Prometheus 指标 | `pip install "tstdx[metrics]"` |
| HTTP REST 网关 | `pip install "tstdx[server]"` |
| MCP 工具服务 | `pip install "tstdx[mcp]"` |
| 全部 | `pip install "tstdx[all]"` |

### Q: 支持 Python 3.9 吗？

不支持。最低要求 Python 3.10（使用了 `X | Y` 类型语法与 `zoneinfo`）。

## 连接与网络

### Q: 连接主站超时怎么办？

1. 运行 `tstdx server-test` 测速，选择最快主站
2. 检查防火墙是否放行 TDX 端口（默认 7709）
3. 库内置多主站自动切换（`AllHostsUnreachable` 时自动降级）

### Q: 被主站封 IP 了怎么办？

- 库内置限流器（`RateLimitedLocal`）避免触发封禁
- 配置 `SourcesRouter` 降级到 HTTP Web 源（新浪/腾讯/东财）
- 降低请求频率，避免高峰期大批量拉取

### Q: 实时行情一次能查几只股票？

服务端约束：`0x0530` 一次只能查 1 只（多只超时不返回）。库内部自动逐只请求再汇总，调用方无感。

## 数据口径

### Q: 成交量单位是什么？

全局契约：**volume=股（shares）、amount=元（CNY）、price=元**。各 HTTP Web 源的原始口径（手/万元/百元）已由 `tstdx.web.normalize` 统一归一化。

### Q: 时间戳是什么时区？

内部一律 **UTC**，输出时可通过 `tz` 字段转为本地时区（`Asia/Shanghai`）。

### Q: 复权因子在哪里？

`tstdx.domain.adjust` 提供复权计算；除权除息事件可以来自 `0x000F`（股本变迁）命令，也可以由调用方自带 `events=`。

**线上那一路今天只有条数能采信**：`0x000F` 的记录布局尚未由 golden 样本锁定（F-37，账本 `verified=False`），
四份实采样本重放出的 910 行里 1587 个字段值落在域外。后果分两种，都不静默——落在调整类别上的事件
会带着解不开的日期让 `compute_factors` 抛 `AdjustError`（四份样本中三份，共 34 条事件全部如此），
一条都不落在调整类别上时则退回"因子全为 1.0"的不复权输出（第四份样本）。
要复权请先自带经过验证的事件源：`adjusted_bars(..., events=...)`，事件可来自本地 gbbq 文件
或 Web Provider 的对应能力。

## 兼容性

### Q: 能替代 mootdx / easyquotation 吗？

可以直接使用原生 API，字段口径对齐，迁移成本低：

```python
# mootdx 风格 → tstdx 原生
from tstdx.client import TdxClient

client = TdxClient()
bars = client.bars("sh600036", period="day", count=100)

# easyquotation 风格 → tstdx Web 源
from tstdx.web import get_quotes

quotes = get_quotes(["sh600519"], source="sina")
```

> **垫片状态**：v1.0 时代的 `tstdx.compat.*` / `tstdx.web.easyquotation` 兼容垫片已随
> v1.2.0 清理移除（与已删除的 `_async_bridge` 同类归并）。

详见 [迁移指南](migration/README.md)。

### Q: 从 easy_tdx / eltdx 迁移？

使用原生 API：`tstdx.compat.*` 垫片已随 v1.2.0 移除，对照表见 [easy_tdx 迁移指南](migration/easy_tdx.md)。

## 协议与扩展

### Q: 遇到未知命令怎么办？

三级分派会兜底：L1 精确解析 → L2 启发式 → L3 原始字节透传。可用 `Prober`（限速 1 req/s，仅非交易时段）探测未知命令并自动生成 spec 草稿到 `PROTOCOL_SPEC/UNKNOWN/`。

### Q: 如何贡献新的协议 spec？

1. 在 `PROTOCOL_SPEC/<family>/` 添加 YAML spec
2. 实现/注册解析器
3. 运行 `python -m tstdx.tools.spec_audit` 验证
4. 附上 golden 样本（`tests/golden/`）

详见 [PROTOCOL_SPEC/README.md](../PROTOCOL_SPEC/README.md)。

## 部署

### Q: 有 Docker 镜像吗？

```bash
docker build -t tstdx .
docker run --rm tstdx tstdx --help
# HTTP 行情网关（镜像的 CMD 是 --help，起服务要显式给命令）
docker run -p 8000:8000 --rm tstdx tstdx serve --bind 0.0.0.0 --port 8000
# 等价写法：把 app 直接交给 uvicorn（需 `pip install "tstdx[server]"`）
docker run -p 8000:8000 --rm tstdx python -m uvicorn tstdx.integration.runtime_http:create_runtime_app --factory --host 0.0.0.0
```

### Q: 如何监控？

- `PrometheusExporter` — 独立 HTTP 服务，`GET /metrics` 文本格式（缺省 `127.0.0.1:9090`）
- `StatsdExporter` — UDP 推送
- `OtelExporter` — OTLP JSON 导出（`endpoint` 须显式给出，例如 `http://localhost:4318/v1/metrics`）
