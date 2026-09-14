# 项目接口文档

> 本文件汇总 tstdx 的全部公开接口面（Public API Surface），按层次组织。
> 每个接口标注了导入路径、签名摘要与使用示例。

## 目录

- [1. 客户端层（Client）](#1-客户端层client)
- [2. 门面层（Facade）](#2-门面层facade)
- [3. v14 Runtime 层](#3-v14-runtime-层)
- [4. 服务面（Integration）](#4-服务面integration)
- [5. 数据落地（Output）](#5-数据落地output)
- [6. 流式订阅（Streaming）](#6-流式订阅streaming)
- [7. 域模型（Domain）](#7-域模型domain)
- [8. 工具链（Tools）](#8-工具链tools)

---

## 1. 客户端层（Client）

### TdxClient（同步）

```python
from tstdx import TdxClient
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
from tstdx import AsyncTdxClient
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

## 2. 门面层（Facade）

### UnifiedQuoteAPI

```python
from tstdx.facade import quote_api, UnifiedQuoteAPI
```

| 方法 | 说明 |
|------|------|
| `query(method, args, **kwargs)` | 通用查询入口 |
| `bars(...)` | K 线 |
| `quotes(...)` | 实时行情 |
| `minute(symbol)` | 当日分时 |
| `minute_web(symbol)` | 强制 HTTP 分时 |
| `minute_klines(symbol, period, count)` | 跨日分钟 K 线 |
| `fund(...)` | 基金数据 |
| `fundflow(...)` | 资金流 |
| `changes(...)` | 盘中异动 |
| `hot(...)` | 人气榜 |
| `wencai(query)` | i 问财 |
| `search(...)` | 搜索 |
| `ipos(...)` | IPO 申购 |
| `margin(...)` | 融资融券 |
| `.df` | 惰性 DataFrame 转换 |

### AsyncUnifiedQuoteAPI

```python
from tstdx.facade.async_api import AsyncUnifiedQuoteAPI
```

核心 10 方法桥接 + `arun(method, *args)` 泛化任意方法。

### ApiResponse

```python
from tstdx.facade.response import ApiResponse, ok, err, wrap
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 是否成功 |
| `data` | Any | 数据载荷 |
| `error` | str \| None | 错误信息 |
| `code` | str \| None | 错误码 |
| `extra` | dict | 附加元数据 |
| `.df` | DataFrame | 惰性转换 |

### RuntimeFacadeAdapter

```python
from tstdx.facade.runtime_adapter import RuntimeFacadeAdapter
```

将门面调用桥接到 v14 Runtime 执行。

---

## 3. v14 Runtime 层

### Runtime

```python
from tstdx.runtime import Runtime
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `execute` | `(request: QueryRequest) -> QueryResponse` | 单次执行 |
| `execute_typed` | `(query, metadata=None) -> QueryResponse` | Typed Query 执行 |
| `execute_batch` | `(requests, max_concurrent=8) -> list[QueryResponse]` | 批量执行（缓存去重 + 并发） |
| `register` | `(operation, handler)` | 注册操作处理器 |
| `register_provider` | `(provider)` | 注册 Provider |
| `subscribe` | `(capability, symbols, provider="tdx", interval=1.0, ...)` | 流式订阅 |
| `unsubscribe` | `(subscription_id) -> StreamHandle \| None` | 停止订阅 |
| `get_subscription` | `(subscription_id) -> StreamHandle \| None` | 获取订阅 |
| `subscriptions` | `() -> Mapping[str, StreamHandle]` | 活跃订阅快照 |
| `semantic_cache_stats` | `() -> dict` | 缓存诊断 |

### RuntimeGateway

```python
from tstdx.runtime import RuntimeGateway
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `bars` | `(symbol, period="day", count=320, ...)` | K 线 |
| `quotes` | `(symbols, as_format="dict", ...)` | 实时行情 |
| `security_count` | `(market=0, ...)` | 证券数量 |
| `finance_info` | `(symbol, ...)` | 财务信息 |
| `minute_today` | `(symbol, ...)` | 当日分时 |
| `security_list` | `(market=0, start=0, ...)` | 证券列表 |
| `execute_batch` | `(requests, max_concurrent=8)` | 批量执行 |
| `execute` | `(request)` | 直接执行 |
| `execute_typed` | `(query, **kwargs)` | Typed Query |
| `subscribe` | `(symbols, provider="tdx", interval=1.0, ...)` | 流式订阅 |
| `providers` | `property -> list[str]` | Provider 列表 |
| `semantic_cache_stats` | `() -> dict` | 缓存诊断 |
| `subscriptions` | `() -> Mapping` | 活跃订阅 |

### QueryRequest / QueryResponse

```python
from tstdx.runtime import QueryRequest, QueryResponse
```

```python
# 构造请求
req = QueryRequest(
    operation="bars",
    args=("sh600519",),
    params={"count": 80, "period": "day"},
    metadata={"cache_ttl": 60.0, "provider": "tdx"},
)

# 响应字段
resp.success        # bool
resp.data           # Any
resp.error          # str | None
resp.code           # str | None
resp.metadata       # dict (provider/channel/query_fingerprint/provenance/execution)
```

### create_runtime

```python
from tstdx.runtime import create_runtime
```

```python
runtime = create_runtime(
    router=None,                    # ProviderRouter（可选）
    planner=None,                   # ExecutionPlanner（可选）
    provider_order=None,            # Sequence[str]（Provider 执行顺序）
    semantic_cache=None,            # SemanticResultCache（可选）
    default_cache_ttl=None,         # float（默认缓存 TTL 秒）
)
```

### StreamHandle

```python
from tstdx.runtime import StreamHandle
```

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `id` | str | 订阅标识 |
| `plan` | StreamPlan | 编译后的流计划 |
| `lifecycle` | StreamLifecycle | 生命周期状态机 |
| `snapshot()` | StreamLifecycleSnapshot | 生命周期快照 |
| `stream_state()` | StreamState | 当前状态 |
| `begin_start()` | None | 幂等启动 |
| `begin_stop()` | None | 开始停止 |
| `close()` | None | 关闭 |

---

## 4. 服务面（Integration）

### HTTP REST 网关

```python
from tstdx.integration.http_server import create_app
```

42 端点，方法白名单 + TaskStore 钳制。

### WebSocket JSON-RPC

```python
from tstdx.integration.ws_server import create_ws_server
```

方法：bars/quotes/minute/trades/finance/security_count/stock_changes/subscribe。

### MCP stdio

```python
from tstdx.integration.mcp_server import create_mcp_server
```

12 工具，含 get_stock_changes / get_hot_rank。

---

## 5. 数据落地（Output）

```python
from tstdx.output import write
```

| Sink | URI 格式 | 依赖 |
|------|----------|------|
| DataFrame | `output://dataframe` | pandas |
| Parquet | `parquet://file.parquet` | pyarrow |
| DuckDB | `duckdb://db.db?table=name` | duckdb |
| CSV | `csv://file.csv` | 无 |

---

## 6. 流式订阅（Streaming）

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

## 7. 域模型（Domain）

### Domain Records

```python
from tstdx.domain.records import (
    Bar, Quote, Level, CapitalChange,
    FinanceInfo, StockInfo, FundInfo,
    BondInfo, NewsItem,
)
```

### 数据模型

```python
from tstdx.domain.models import Bar, Quote, Level, to_dataframe
from tstdx.domain.symbol import normalize_symbol, split_symbol
from tstdx.domain.adjust import adjust_bars
from tstdx.domain.calendar import is_trading_day
```

---

## 8. 工具链（Tools）

### 主站巡检

```bash
tstdx hosts audit --family all --timeout 3 --workers 20
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

详见 [docs/api/README.md](api/README.md)。
