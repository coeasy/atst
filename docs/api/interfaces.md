# 项目接口文档

> 本文件汇总 tstdx 的全部公开接口面（Public API Surface），按层次组织。
> 每个接口标注了导入路径、签名摘要与使用示例。

## 目录

- [1. 协议层客户端（TdxClient 族）](#1-协议层客户端tdxclient-族)
- [2. 统一内核层（Client + UnifiedRuntime）](#2-统一内核层client--unifiedruntime)
- [3. 服务面（Integration）](#3-服务面integration)
- [4. 数据落地（Output）](#4-数据落地output)
- [5. 流式订阅（Streaming）](#5-流式订阅streaming)
- [6. 域模型（Domain）](#6-域模型domain)
- [7. 工具链（Tools）](#7-工具链tools)

---

## 1. 协议层客户端（TdxClient 族）

### TdxClient（同步）

```python
from tstdx.client import TdxClient
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `bars` | `(symbol, period="day", count=320, start=0, market=None, index=False, as_format="dict", strict=False)` | K 线/分钟线 |
| `quotes` | `(symbols, as_format="dict")` | 实时行情快照 |
| `quotes_concurrent` | `(symbols, workers=8, as_format="dict")` | 并发批量行情 |
| `security_count` | `(market=0)` | 证券数量 |
| `finance_info` | `(symbol)` | 财务信息 |
| `minute_today` | `(symbol)` | 当日分时 |
| `security_list` | `(market=0, start=0)` | 证券列表 |
| `export_security_list` | `(market=0, max_pages=100)` | 全市场代码表 |
| `minute_history` | `(symbol, date)` | 历史分时 |
| `trade_today` | `(symbol, start=0, count=0)` | 当日逐笔 |
| `block_quotes` | `(block_type=0, start=0)` | 板块行情 |
| `file_download` | `(symbol, filename, offset=0, length=0, max_packets=500, strict=False)` | 文件下载 |
| `auction_snapshot` | `(symbol)` | 集合竞价 |
| `volume_price_dist` | `(symbol)` | 量价分布 |
| `quotes_snapshot` | `(symbols)` | 批量行情快照 |
| `snapshot` | `(symbol, as_format="dict")` | 单只完整快照 |
| `request` | `(cmd, body, ctx=None, as_format="dict")` | 通用命令 |
| `bestip` | `(timeout=1.0, samples=1, max_workers=16, ...)` | 运行时测速 |

### AsyncTdxClient（异步）

```python
from tstdx.client import AsyncTdxClient
```

与 TdxClient 签名镜像，所有方法为 `async def`。

### 多协议族客户端

| 类 | 导入路径 | 协议族 | 说明 |
|----|----------|--------|------|
| `GoodsClient` | `tstdx.client.sync` | GOODS (7727) | 商品/期货/期权/外汇 |
| `ExMarketClient` | `tstdx.client.sync` | EXTENDED (7727) | 港股/美股/期货/外汇 |
| `MacClient` | `tstdx.client.sync` | MAC | MAC 专属 |
| `F10Client` | `tstdx.client.sync` | F10 | F10 资料 |

### 工厂

```python
from tstdx.client import get_client
client = get_client("std")     # TdxClient
client = get_client("goods")   # GoodsClient
client = get_client("async")   # AsyncTdxClient
```

---

## 2. 统一内核层（Client + UnifiedRuntime）

### Client（唯一业务入口，同步）

```python
from tstdx import Client, AsyncClient
```

| 方法 | 签名摘要 | 说明 |
|------|----------|------|
| `bars` | `(symbol, *, provider=None, policy=None, period="day", count=320, start=0, adjustment="", currentness="historical", max_age=None)` | K 线 |
| `quotes` | `(symbols, *, provider=None, policy=None, currentness="live", max_age=None)` | 实时行情 |
| `quotes_batch` | `(symbols, *, provider=None, currentness="live", max_age=None) -> BatchResult` | 逐 symbol 三态审计 |
| `snapshot` | `(symbol, *, provider="tdx")` | 盘口快照 |
| `minute` | `(symbol, *, provider="tdx")` | 当日分时 |
| `trades` | `(symbol, *, provider="tdx", start=0, count=0)` | 逐笔成交 |
| `security_count` | `(*, market=0, provider="tdx")` | 证券数量 |
| `security_list` | `(*, market=0, start=0, provider="tdx")` | 证券列表分页 |
| `stream` | `(symbols, *, provider="tdx", interval=1.0, diff_only=False, max_queue=1024, on_quote=None, on_error=None) -> StatefulQuoteStream` | 流式订阅 |
| `execute` | `(spec: QuerySpec) -> QueryResult` | 通用面：任何 capability 同一入口 |
| `call` | `(capability, *args, provider=None, channel=None, currentness="business", max_age=None, **kwargs)` | 便捷通用入口 |
| `execute_with_policy` | `(spec, *, policy: FallbackPolicy) -> OrchestratedResult` | 显式跨源编排 |
| `typed` | `(query: CapabilityQuery, **kwargs) -> TypedQueryResult` | 冻结 dataclass 契约 → 强类型记录 |
| `capabilities` | `() -> tuple[str, ...]` | 当前 172 项 capability |
| `close` | `()` | 释放内核连接 |

`AsyncClient` 是同名异步镜像（`async with AsyncClient() as client: ...`）；
传 `policy=` 时 `bars/quotes` 返回 `OrchestratedResult` 而非 `QueryResult`。

### UnifiedRuntime（唯一执行内核）

```python
from tstdx import UnifiedRuntime
from tstdx.runtime import KernelExecutor   # 执行面 Protocol（测试接缝）

runtime = UnifiedRuntime(
    default_provider="tdx", timeout=5.0, hosts=None, vipdoc_root=None,
    executor=None,      # 注入 KernelExecutor 实现即接管全部执行体
)
```

执行路径固定为一条：`QuerySpec → QueryPlanner.compile → QueryPlan →
executor.execute → QueryResult`。内核零缓存、不自动换源；`QueryResult.meta`
（`provider / channel / capability / fingerprint / provenance`）即审计凭据。

### QuerySpec / QueryPlan

```python
from tstdx import QuerySpec, QueryPlan

spec = QuerySpec.build(
    "bars", symbols="sh600519", period="day", count=80,
    provider=None, currentness="historical", max_age=None, deadline_ms=5000, options={},
)
```

`QuerySpec` 字段：`capability, symbols, provider, channel, period, count, start,
adjustment, currentness, max_age, deadline_ms, schema_version, options_json`。
`QueryPlan` 字段：`spec, provider, channel, fingerprint, deadline_ms, batch_limit,
live_channel, local_channel, budget`。参数在规划期按 Provider 实现的**真实签名**校验，
不合法即 `ValidationError` 且不发请求。

### FallbackPolicy / ProviderOrchestrator

```python
from tstdx import FallbackPolicy
from tstdx.runtime.orchestration import OrchestratedResult, ProviderAttempt

policy = FallbackPolicy(providers=("tdx", "tencent"))   # 或 FallbackPolicy.build("tdx", "tencent")
out = client.quotes("sh600519", policy=policy)
out.result      # QueryResult —— 第一个成功源的载荷
out.attempts    # tuple[ProviderAttempt(provider, status, code), ...]
```

跨源只在显式策略下发生；默认路径永不触发。

### BatchItem / BatchResult

```python
from tstdx import BatchItem, BatchResult
```

批量入口只有一个：`client.quotes_batch(symbols, ...)`（内核逐 symbol 直连同一
Provider，串行下发、无隐藏换源）。`BatchResult.items` 为 `{symbol: BatchItem}`，
`BatchItem.status ∈ {ok, missing, failed, not_attempted}`；另有 `errors`
（`ErrorEnvelope` 映射）、`requested`、`partial`、`status_counts`、`success`、
`failed`、`missing`。

> v13 的 `BatchSpec` 请求信封已在 v17 Phase 5 的可达性收口中**删除**（clean
> break，无别名）：它从来没有生产消费者，批量展开事实只存在于内核那条循环里。

### CapabilityQuery 与 Domain Records

```python
from tstdx.typed_query import FundHoldingsQuery          # 60+ 冻结契约，11 领域基类
from tstdx.domain.records import FinancialRecord, FundRecord, BondRecord, NewsRecord
```

`client.typed(FundHoldingsQuery(code="000001"))` → `TypedQueryResult(data, capability)`。
Domain Record 族共 9 类：`Financial/Fund/Bond/News/Research/Option/MarketData/Search/Macro`。

### 溯源守卫与启动对账

```python
from tstdx.runtime.provenance import validate_runtime_provenance   # (identity, result) -> None
from tstdx.runtime.audit import audit_runtime                      # () -> RuntimeAuditReport
```

`validate_runtime_provenance` 在结果 provenance 与请求身份不一致时抛错（换源即抛）；
`audit_runtime()` 启动时对账 registry / catalog / `DIRECT_BINDINGS` 三方事实。

---

## 3. 服务面（Integration）

四个服务面全部委托同一个 `Client`，不存在第二套执行路径。

### HTTP REST 网关（10 路由）

```python
from tstdx.integration.runtime_http import create_runtime_app
app = create_runtime_app()          # FastAPI 实例，交由 uvicorn 承载
```

| 路由 | 方法 |
|------|------|
| `/v13/quotes` | GET |
| `/v13/bars/{symbol}` | GET |
| `/v13/snapshot/{symbol}` | GET |
| `/v13/minute/{symbol}` | GET |
| `/v13/trades/{symbol}` | GET |
| `/v13/security/count` | GET |
| `/v13/security/list` | GET |
| `/v13/query/{capability}` | POST（任意 capability 的通用入口）|
| `/v13/capabilities` | GET |
| `/v13/runtime/health` | GET |

### WebSocket JSON-RPC（10 方法）

```python
from tstdx.integration.runtime_ws_server import serve_runtime_ws
```

方法：`quotes`、`bars`、`snapshot`、`minute`、`trades`、`security.count`、
`security.list`、`query`、`runtime.capabilities`、`runtime.health`。

### MCP stdio（9 工具）

```python
from tstdx.integration.mcp import create_mcp_server, TOOLS
```

工具：`query_capability`（通用入口）+ `get_bars`、`get_quote`、`get_quotes`、
`get_snapshot`、`get_minute_today`、`get_trades`、`get_security_count`、
`get_security_list`。

### CLI（31 子命令）

```bash
tstdx --help
```

全部子命令委托 `Client`；`tstdx.integration.runtime_tasks.RuntimeTaskStore`
为服务面提供有界后台任务存储。

---

## 4. 数据落地（Output）

```python
from tstdx.output import write
```

| Sink | URI 格式 | 依赖 |
|------|----------|------|
| DataFrame | 显式 `fmt="dataframe"`（或 `to_dataframe(items)`）| pandas |
| Parquet | `path/file.parquet`（`.parquet`/`.pq` 后缀）| pyarrow |
| DuckDB | `duckdb:<db 路径>@<表名>`，省略路径即内存库 | duckdb |
| CSV | `path/file.csv`（`.csv` 后缀）| 无 |

---

## 5. 流式订阅（Streaming）

### QuoteStream（同步）

```python
from tstdx.streaming import QuoteStream
```

### AsyncQuoteStream（异步）

```python
from tstdx.streaming import AsyncQuoteStream
```

### StreamEngine（内核）

```python
from tstdx.streaming.engine import StreamEngine
```

组件：`ReconnectPolicy` + `BackpressureQueue` + `DeltaMerger` + `GapFiller` + `StreamEngine`。

### PushChannel

```python
from tstdx.streaming.push import PushChannel
```

0x0547 原始推送通道（可选高级 API）。

---

## 6. 域模型（Domain）

### Domain Records

```python
from tstdx.domain.records import (
    FinancialRecord, FundRecord, BondRecord, NewsRecord, ResearchRecord,
    OptionRecord, MarketDataRecord, SearchRecord, MacroRecord,
)
```

### 数据模型

```python
from tstdx.domain.models import Bar, Quote, Level, to_dataframe
from tstdx.domain.symbol import normalize_symbol, split_symbol
from tstdx.domain.adjust import AdjustEngine, to_adjusted, compute_factors
from tstdx.domain.calendar import is_trading_day
```

---

## 7. 工具链（Tools）

### 主站巡检

```bash
tstdx hosts audit --family quotation --family ex_quotation --timeout 3 --workers 20
python scripts/audit_hosts.py --report audit.json
python scripts/contract_audit.py --ci
```

### 协议规范工具

```bash
python -m tstdx.tools.capture        # 合规采集
python -m tstdx.tools.codegen        # 从 YAML 生成骨架
python -m tstdx.tools.spec_audit     # 双向漂移检查
python -m tstdx.tools.golden_audit   # Golden 门禁
```

### 可达性检查

```bash
python scripts/audit_reachability.py --strict
```

---

## 完整模块索引

详见 [API 参考索引](README.md)。
