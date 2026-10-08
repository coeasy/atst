# FAQ 常见问题

## 安装与依赖

### Q: atst 需要安装哪些依赖？

核心运行时**零硬依赖**。按需安装 extras：

> **当前安装路径**（G9，2026-09-22 实测）：本包不在 PyPI 上
> （`https://pypi.org/pypi/atst/json` 回 404），所以下表这些写法要等上架后才能直接
> 执行。现在请在本仓库根目录用 `pip install ".[extra]"`（把 `atst` 换成 `.`），
> extras 的名字完全相同；README「安装」一节给的是同一口径。

| 需求 | 安装 |
|---|---|
| DataFrame 输出 | `pip install "atst[dataframe]"` |
| Parquet 落地 | `pip install "atst[parquet]"` |
| DuckDB 落地 | `pip install "atst[duckdb]"` |
| HTTP Web 源降级 | `pip install "atst[web]"` |
| Prometheus 指标 | `pip install "atst[metrics]"` |
| HTTP REST 网关 / WebSocket RPC | `pip install "atst[server]"` |
| capture 工具链（时区 + zstd） | `pip install "atst[tools]"` |
| MCP 工具服务 | 无需 extras：`atst/integration/mcp/` 是纯标准库的 JSON-RPC over stdio |
| 全部 | `pip install "atst[all]"` |

> **为什么 MCP 没有 extra**（第 23 轮实测）：全仓对 `mcp` / `pydantic` 两个第三方包的
> 读取次数为 0（`atst/`、`scripts/`、`tests/` 一起按 `import` 普查，只有 `DESIGN.md`
> 的一段示例代码提到 pydantic）。过去 `pyproject.toml` 里那两份 extra 是没人按它行动的
> 声明，已经删除；照它装包只会多装两个用不上的包。

### Q: 支持 Python 3.9 吗？

不支持。最低要求 Python 3.10（使用了 `X | Y` 类型语法与 `zoneinfo`）。

## 连接与网络

### Q: 连接主站超时怎么办？

1. 运行 `atst server-test` 测速，选择最快主站
2. 检查防火墙是否放行 TDX 端口（默认 7709）
3. 库内置多主站自动切换（`AllHostsUnreachable` 时自动降级）

### Q: 被主站封 IP 了怎么办？

- 库内置令牌桶限速（`atst/transport/ratelimit.py`）避免触发封禁；超限的行为由
  `[rate_limit]` 段的 `strict` 决定——`false`（默认）阻塞等令牌，`true` 立即抛
  `RateLimitedLocal`
- **不会**自动改走 HTTP Web 源：跨源的形状只有一种，调用方显式给
  `Client().quotes(symbols, policy=FallbackPolicy(providers=("tdx", "tencent")))`
  （v16 删除了 `SourcesRouter` 那条自动降级链；`atst.web` 便捷入口的按序尝试是 web 层自己的事，
  与内核无关）
- 降低请求频率，避免高峰期大批量拉取

### Q: 实时行情一次能查几只股票？

服务端约束：`0x0530` 一次只能查 1 只（多只超时不返回）。库内部自动逐只请求再汇总，调用方无感。

## 数据口径

### Q: 成交量单位是什么？

全局契约：**volume=股（shares）、amount=元（CNY）、price=元**。各 HTTP Web 源的原始口径（手/万元/百元）已由 `atst.web.normalize` 统一归一化。

### Q: 时间戳是什么时区？

**没有时区换算这一说**：`datetime` 是服务端按**交易所本地时间**给的字符串（日线只有日期，
分钟线带 `HH:MM`），库照原样透出，不换算成 UTC、也不提供任何 `tz` 出参。
需要带时区的时间戳请在调用方自己按 `Asia/Shanghai` 解释这个字符串。
规格档案层因此也不带任何时区入参：`DataProfile` 的字段全是解码器真正按它行动的差异维度。

### Q: 复权因子在哪里？

复权**因子是算出来的，不是取来的**：`atst/domain/adjust.py` 的 `AdjustEngine` 用事件（除权日、
每股送转 `S`、每股派息 `D`、配股比例 `R` 与配股价 `P_r`）按
`P_ref = (P − D + P_r·R)/(1 + S + R)` 逐事件算 `1/k`，再累乘成因子。所以"数据来源"分两半：

| 半 | 来源 | 说明 |
|----|------|------|
| 事件 | **东财数据中心（默认）** | `dividend_history`（`RPT_SHAREBONUS_DET`：送股/转增/派息）+ `rights_issue`（`RPT_IPO_ALLOTMENT`：配股比例与配股价），按**除权日**合成一天一条 |
| 原始 K 线 | 本地 `vipdoc` 优先，否则按 `provider`（缺省 `tdx`）在线取 | 两者都不带复权，因子在内存里算 |

`Client.call("adjusted_bars", "600519", method="qfq")` **不需要**调用方自带事件——默认那一路已经接线：

```python
with Client() as c:
    qfq = c.call("adjusted_bars", "600519", method="qfq", count=320).data
    hfq = c.call("adjusted_bars", "600519", method="hfq", count=320).data
```

也可以传 `events=` 自带（本地 gbbq 解析出来的、或别家源的事件），此时不走东财。

### Q: 能不能直接用 TDX 的复权因子／复权数据？

**TDX 没有"复权因子"这种现成数据可拿。** 实测（2026-10-08）：注册表里与除权相关的命令只有
`0x000F`（`capital_changes`，除权除息原始记录），**没有任何"复权 K 线"或"复权因子"命令**——
通达信自己也是"原始 K 线 + 除权数据 → 客户端算复权"，与 atst 的架构同构。可选的三条路：

| 路 | 现状 |
|----|------|
| 协议 `0x000F` | **不可用**：记录布局未由真机 golden 锁定（F-37）。本轮实测 `sh600519` 回 250 条，**250/250 条字段落在域外**（`market=48`、`code='519\x01'`、`date=''`）。显式点名 `event_source="tdx"` 会被布局闸当场拒成 `E1010` 并告诉你改用 `eastmoney`；**不再**炸成 E9000 |
| 本地 `T0002/hq_cache/gbbq` | 本仓库**没有实现**该文件的读取，本机也没有 TDX 安装可对拍。它的格式没有公开规范，按"不猜解析器"的纪律不做 |
| 东财数据中心 | **这是默认路**，列名已逐字对拍（`BONUS_RATIO` / `IT_RATIO` / `PRETAX_BONUS_RMB` / `EX_DIVIDEND_DATE`，配股 `PLACING_RATIO` / `ISSUE_PRICE`） |

结论：要复权就用默认那一路；`events=` 是给自带事件源的调用方的口子。

**实测校验（2026-10-08）**：`sh600519` 的 hfq 与 raw 在**非除权日逐根收益率完全一致**（0 处不符）；
事件日反推的每股分红与公告精确对上（2024-06-19 = 308.76 元/10 股、2024-12-20 = 238.82、
2011 年 `10送1派23` 得 `1/k = 1.112029`，含送股与派息的交叉项）。同一根 bar 在
`count=5 / 60 / 2000` 下因子完全一致（600519 = 7.39106838），不再随窗口漂移。

**宽表日线**（`daily_enriched`，24 列）把复权 K 线与估值序列拼成一行一交易日，`turnover` / `vol_ratio`
/ `is_st` 三条口径的**来源标注**（`vol_ratio_basis` / `is_st_source`）随行给出。详见
[接口文档](api/interfaces.md)。

## 兼容性

### Q: 能替代 mootdx / easyquotation 吗？

可以直接使用原生 API，字段口径对齐，迁移成本低：

```python
# mootdx 风格 → atst 原生
from atst.client import TdxClient

client = TdxClient()
bars = client.bars("sh600036", period="day", count=100)

# easyquotation 风格 → atst Web 源
from atst.web import get_quotes

quotes = get_quotes(["sh600519"], source="sina")
```

> **垫片状态**：v1.0 时代的 `atst.compat.*` / `atst.web.easyquotation` 兼容垫片已随
> v1.2.0 清理移除（与已删除的 `_async_bridge` 同类归并）。

详见 [迁移指南](migration/README.md)。

### Q: 从 easy_tdx / eltdx 迁移？

使用原生 API：`atst.compat.*` 垫片已随 v1.2.0 移除，对照表见 [easy_tdx 迁移指南](migration/easy_tdx.md)。

## 协议与扩展

### Q: 遇到未知命令怎么办？

三级分派会兜底：L1 精确解析 → L2 启发式 → L3 原始字节透传。可用 `Prober`（限速 1 req/s，仅非交易时段）探测未知命令并自动生成 spec 草稿到 `PROTOCOL_SPEC/UNKNOWN/`。

### Q: 如何贡献新的协议 spec？

1. 在 `PROTOCOL_SPEC/<family>/` 添加 YAML spec
2. 实现/注册解析器
3. 运行 `python -m atst.tools.spec_audit` 验证
4. 附上 golden 样本（`tests/golden/`）

详见 [PROTOCOL_SPEC/README.md](../PROTOCOL_SPEC/README.md)。

## 部署

### Q: 有 Docker 镜像吗？

```bash
docker build -t atst .
docker run --rm atst atst --help
# HTTP 行情网关（镜像的 CMD 是 --help，起服务要显式给命令）
docker run -p 8000:8000 --rm atst atst serve --bind 0.0.0.0 --port 8000
# 等价写法：把 app 直接交给 uvicorn（需 `pip install "atst[server]"`）
docker run -p 8000:8000 --rm atst python -m uvicorn atst.integration.runtime_http:create_runtime_app --factory --host 0.0.0.0
```

### Q: 如何监控？

- `PrometheusExporter` — 独立 HTTP 服务，`GET /metrics` 文本格式（缺省 `127.0.0.1:9090`）
- `StatsdExporter` — UDP 推送
- `OtelExporter` — OTLP JSON 导出（`endpoint` 须显式给出，例如 `http://localhost:4318/v1/metrics`）
