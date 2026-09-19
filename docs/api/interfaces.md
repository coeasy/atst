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

> 下表「已下线」不是编辑判断，而是命令账本（`tstdx/protocol/commands.py`）与
> `core._UNVERIFIED_STRUCTURED_BLOCK` 的现值：这些方法在客户端**主动不发**那条帧。
> 由 `tests/architecture/test_offline_capability_honesty.py` 逐行核对，改状态请改账本。

| 方法 | 签名 | 说明 |
|------|------|------|
| `bars` | `(symbol, period="day", count=320, start=0, market=None, index=False, as_format="dict", strict=False)` | K 线/分钟线 |
| `quotes` | `(symbols, as_format="dict")` | 实时行情快照 |
| `quotes_concurrent` | `(symbols, workers=8, as_format="dict")` | 并发批量行情 |
| `security_count` | `(market=0)` | 证券数量 |
| `finance_info` | `(symbol)` | 财务信息（结构与条数可用，**逐字段语义不保证**，见 F-37） |
| `minute_today` | `(symbol)` | **已下线**（0x0537 request/parser 仍 inferred，发包前抛 `NotImplementedFeature`） |
| `security_list` | `(market=0, start=0)` | 证券列表：**已下线**（0x044D 账本 offline，发包前抛 `CommandOffline`） |
| `export_security_list` | `(market=0, max_pages=100)` | 全市场代码表：**已下线**（随 `security_list`，抛 `CommandOffline`） |
| `minute_history` | `(symbol, date)` | 历史分时：**已下线**（0x0FB4 账本 offline，抛 `CommandOffline`） |
| `trade_today` | `(symbol, start=0, count=0)` | 当日逐笔：**已下线**（0x0FC5 inferred 拦截，抛 `NotImplementedFeature`） |
| `block_quotes` | `(block_type=0, start=0)` | 板块行情：**已下线**（0x07E5 账本 offline，抛 `CommandOffline`） |
| `file_download` | `(symbol, filename, offset=0, length=0, max_packets=500, strict=False)` | 文件下载 |
| `auction_snapshot` | `(symbol)` | 集合竞价：**已下线**（0x056A 账本 offline，抛 `CommandOffline`） |
| `volume_price_dist` | `(symbol)` | 量价分布：**已下线**（0x051A 账本 offline，抛 `CommandOffline`） |
| `quotes_snapshot` | `(symbols)` | 批量行情快照（0x054C 账本 offline 但被放行，逐片**回退** 0x0530 ⇒ 可用） |
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

### 命令账本查询面

```python
from tstdx.protocol.commands import (
    COMMANDS,
    CMD,
    by_family,
    by_status,
    cmd,
    get_command,
    unknown_command_ids,
)
```

账本（`tstdx/protocol/commands.py`）是「协议全覆盖」的登记表：85 行、5 协议族
（`quotation 39 / ex_quotation 17 / mac_quotation 16 / goods 11 / f10 2`）。它对外只有
这几个函数——**名单、规模数字、以及「每个名字都有用例真的在调」**这三件事由
`tests/architecture/test_ledger_public_surface.py` 与本表双向核对。

| 函数 | 签名 | 返回 | 口径 |
|------|------|------|------|
| `cmd` | `(name)` | `int` | 命令名 → 命令号。85 行的名字全局唯一，所以不需要族上下文；未登记名抛 `KeyError`，消息里带账本规模 |
| `get_command` | `(cmd, family=Family.STANDARD)` | `Command \| None` | `(族, 号)` → 账本行；未登记号返回 `None`，照旧交 L2 通用解析 + L3 原始透传，不丢包 |
| `by_family` | `(family)` | `Iterator[Command]` | 一族全部行，按命令号升序；未知族名得到空序列，不报错 |
| `by_status` | `(status, family=None)` | `list[Command]` | 按实测状态列全部行：`online` 74 / `offline` 9 / `degraded` 2。发包前的 fail-fast 读的是**单行的** `status`（`tstdx/client/core.py` 的 `_guard_offline` 走 `get_command`），本函数是"按状态列全部行"那一侧 |
| `unknown_command_ids` | `(family=Family.STANDARD)` | `list[Command]` | 语义未经 golden 校正（`verified=False`）的行：默认族 30 条、全账本 76 条。**返回行而不是裸命令号**，「unknown」也不等于「命令不存在」 |

F-65 裁决 (b) 删掉了两个查询函数，不留别名：`stats()`（按族聚合的计数字典，`tstdx/` 内
零读取点，唯一的读者是它自己的测试）、`get_command_by_name()`（对 85 行做线性名字扫描，
全包零调用、零测试）。名字 → 整行的需求由 `get_command(cmd(name), family)` 覆盖，用例
逐行验证它给的是同一个对象，所以删除不削能力。

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
| `bars` | `(symbol, *, provider=None, policy=None, period="day", count=320, start=0, adjustment="", currentness="historical", strict=False)` | K 线；`strict=True` 时结果携带任何数据瑕疵即抛 `TruncatedDataError` |
| `quotes` | `(symbols, *, provider=None, policy=None, currentness="live")` | 实时行情 |
| `quotes_batch` | `(symbols, *, provider=None, currentness="live") -> BatchResult` | 逐 symbol 三态审计 |
| `snapshot` | `(symbol, *, provider="tdx")` | 盘口快照 |
| `minute` | `(symbol, *, provider="tdx")` | 当日分时（**默认 tdx 已下线**，抛 `NotImplementedFeature`；分时改用声明该能力的 Web Provider） |
| `trades` | `(symbol, *, provider="tdx", start=0, count=0)` | 逐笔成交（**默认 tdx 已下线**，抛 `NotImplementedFeature`） |
| `security_count` | `(*, market=0, provider="tdx")` | 证券数量 |
| `security_list` | `(*, market=0, start=0, provider="tdx")` | 证券列表分页：**已下线**，总是抛 `CommandOffline` |
| `stream` | `(symbols, *, provider="tdx", interval=1.0, diff_only=False, max_queue=1024, on_quote=None, on_error=None) -> StatefulQuoteStream` | 流式订阅 |
| `execute` | `(spec: QuerySpec) -> QueryResult` | 通用面：任何 capability 同一入口 |
| `call` | `(capability, *args, provider=None, channel=None, currentness="business", **kwargs)` | 便捷通用入口 |
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
（`provider / channel / capability / fingerprint / provenance / warnings`）即审计凭据。
`warnings` 是本次结果携带的数据瑕疵清单（`WarningCode` + 一句人话，发射口只有
`tstdx/diagnostics.py` 一个）：空元组是"干净"这一判断的证据，三张服务面把它逐条写进
`meta.warnings`；`strict=True` 时内核改为在返回前抛 `TruncatedDataError`。

### QuerySpec / QueryPlan

```python
from tstdx import QuerySpec, QueryPlan

spec = QuerySpec.build(
    "bars", symbols="sh600519", period="day", count=80,
    provider=None, currentness="historical", deadline_ms=5000, options={},
)
```

`QuerySpec` 字段：`capability, symbols, provider, channel, period, count, start,
adjustment, currentness, deadline_ms, schema_version, options_json`。
`QueryPlan` 字段：`spec, provider, channel, fingerprint, budget`——`budget`
（`ExecutionBudget`）是 `deadline_ms` 的唯一载体，逐跳约束传输层超时；plan 上不再
另存 deadline、批量上限或 channel 布尔位，那些字段曾经无人读取。参数在规划期按
Provider 实现的**真实签名**校验，不合法即 `ValidationError` 且不发请求。

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
from tstdx.typed_query import FundHoldingsQuery          # 60+ 冻结契约，10 领域基类
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

四个服务面的数据命令全部委托同一个 `Client`（含 `stream`：CLI 不自己构造流）。CLI 另有
6 个传输/诊断命令（`probe`/`goods`/`f10`/`blocks`/`list`/`quotes-snapshot`）直连传输层客户端，
不经内核——它们是协议诊断面，不是第二套能力执行路径（口径见 `tstdx/cli/runtime_commands.py`
的模块 docstring，守卫是 `test_service_faces_never_import_the_web_layer` 与
`test_service_faces_never_build_a_stream_themselves`）。

三张 wire 面（HTTP REST、WebSocket JSON-RPC、MCP stdio）对**未声明的请求字段**口径一致：
一律当场拒绝，不存在「收下但无人读」的第三种下场（F-47 裁决 (a)，Phase 5 第 40 步落地，2026-09-19）。
白名单就是各面自己那份声明，不是第二份抄件——HTTP 查询串 = 路由签名本身，HTTP body 与 WS `params`
= `tstdx/integration/wire_fields.py` 里的两份名单，MCP `arguments` = 该工具的 `inputSchema`
（9 张 schema 都写着 `additionalProperties: false`，且这条声明被真实执行）。拒绝的落点是 HTTP
`422`（`E1010` / `ValidationError`）与 JSON-RPC `-32602`；人读的那句话与机读侧的
`context.unknown_fields` 都点名被拒的那个键。判据见 `tests/runtime/test_wire_declared_fields.py`
（名单与分派读取点求差 + 三面逐路由 / 逐方法 / 逐工具真打一遍）。

> **破坏性变更口径**：此前多余的查询串参数与 body / `params` / `arguments` 键会被静默忽略并照常
> 返回 200 或 result，现在开始被拒。客户端追加的缓存穿透参数（`_=1700000000` 一类）同样会被拒——
> 路由签名没声明它，它也就不改变任何行为。

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

### Client.stream（唯一入口）

```python
from tstdx import Client

with Client() as client:
    stream = client.stream(
        "sh600519",
        provider="tdx",
        interval=1.0,
        on_quote=lambda code, quote: print(code, quote["price"]),
    )
    stream.start()
    time.sleep(5)
    stream.stop()
```

`client.stream` 先用 `tstdx.stream_contract.StreamPlanner` 编译出一张精确计划：当前只有
`quotes` 能力与 `tdx` Provider 存在 Direct 流式绑定，偏离即抛 `ValidationError`（CLI 的
`tstdx stream sh600519` 走同一条路、同一个判据）。参数见 §2 的 `stream` 行。

### StatefulQuoteStream / AsyncStatefulQuoteStream（生命周期对象）

```python
from tstdx.streaming import AsyncStatefulQuoteStream, StatefulQuoteStream
```

`Client.stream` / `AsyncClient.stream` 返回的对象，轮询走调用方的 `UnifiedRuntime`；
`state` 与 `failure_reason` 给出显式生命周期状态（`StreamState`）。

### QuoteStream / AsyncQuoteStream（轮询基类）

```python
from tstdx.streaming import AsyncQuoteStream, QuoteStream
```

上面两者的轮询基类，属包内组合件：服务面与业务代码不应直接构造它们，否则会绕开
`StreamPlanner` 的 fail-closed 判定并各自 new 出 runtime（守卫见
`test_service_faces_never_build_a_stream_themselves`）。

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
