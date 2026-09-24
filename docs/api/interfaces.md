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
| `bars` | `(symbol, *, period="day", count=320, start=0, market=None, index=False, as_format="dict", strict=False)` | K 线/分钟线；`count` > 800 时内部自动按 800 一页翻页 |
| `quotes` | `(symbols, *, as_format="dict")` | 实时行情快照（`0x0530` 逐只请求再汇总） |
| `quotes_concurrent` | `(symbols, *, workers=8, as_format="dict")` | 并发批量行情 |
| `security_count` | `(market=0)` | 证券数量 |
| `finance_info` | `(symbol)` | 财务信息（结构与条数可用，**逐字段语义不保证**，见 F-37） |
| `capital_changes` | `(symbol)` | 除权除息 / 股本变迁（`0x000F`）：口径与 `finance_info` 同格——条数可用、字段语义不保证，越域行会带 `field_out_of_domain` 告警 |
| `minute_today` | `(symbol)` | **已下线**（0x0537 request/parser 仍 inferred，发包前抛 `NotImplementedFeature`） |
| `security_list` | `(market=0, start=0)` | 证券列表：**已下线**（0x044D 账本 offline，发包前抛 `CommandOffline`） |
| `export_security_list` | `(market=0, *, max_pages=100)` | 全市场代码表：**已下线**（随 `security_list`，抛 `CommandOffline`） |
| `minute_history` | `(symbol, date)` | 历史分时：**已下线**（0x0FB4 账本 offline，抛 `CommandOffline`） |
| `trade_today` | `(symbol, start=0, count=0)` | 当日逐笔：**已下线**（0x0FC5 inferred 拦截，抛 `NotImplementedFeature`） |
| `block_quotes` | `(block_type=0, start=0)` | 板块行情：**已下线**（0x07E5 账本 offline，抛 `CommandOffline`） |
| `file_download` | `(symbol, filename, *, offset=0, length=0, max_packets=500, strict=False)` | 文件下载 |
| `auction_snapshot` | `(symbol)` | 集合竞价：**已下线**（0x056A 账本 offline，抛 `CommandOffline`） |
| `volume_price_dist` | `(symbol)` | 量价分布：**已下线**（0x051A 账本 offline，抛 `CommandOffline`） |
| `quotes_snapshot` | `(symbols)` | 批量行情快照（0x054C 账本 offline 但被放行，逐片**回退** 0x0530 ⇒ 可用） |
| `snapshot` | `(symbol, *, as_format="dict")` | 单只完整快照 |
| `request` | `(cmd, body, *, ctx=None, as_format="dict")` | 任意命令 → 解析行（L3 时只会看到空列表） |
| `request_result` | `(cmd, body, *, ctx=None) -> ParseResult` | 任意命令 → **完整**解析结果（`tier`/`confidence`/`raw`/`warnings` 都在这里，见 [食谱 06](../cookbook/06_custom_command.md)） |
| `open` | `(*, bestip=False, **speedtest_kwargs) -> Self` | 建连；`bestip=True` 先测速热更新主站池再返回。与 `close()` 组成 `with TdxClient() as client:` |
| `close` | `()` | 释放连接池（`with` 语句自动调用） |
| `bestip` | `(*, timeout=1.0, samples=1, max_workers=16, save_ranking=True, keep_failures=True)` | 运行时测速并热更新主站池 |

构造签名同样在这一格：`TdxClient(hosts=None, *, family="quotation", timeout=5.0, max_retries=3,
pool=None, **pool_kwargs)`。`pool_kwargs` 直通 `ConnectionPool`，所以并发旋钮是
`slots_per_host`；**没有 `pool_size` 这个参数**，写了在构造期就 `TypeError`。

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
| `unknown_command_ids` | `(family=Family.STANDARD)` | `list[Command]` | 语义未经 golden 校正（`verified=False`）的行：默认族 32 条、全账本 78 条。**返回行而不是裸命令号**，「unknown」也不等于「命令不存在」 |

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
| `bars` | `(symbol, *, provider=None, policy=None, period="day", count=320, start=0, adjustment="", currentness="historical", strict=False)` | K 线；`strict=True` 时结果携带任何数据瑕疵即抛 `TruncatedDataError`；`period=` 收哪些写法见本文 §6「K 线周期拼写」一表 |
| `quotes` | `(symbols, *, provider=None, policy=None, currentness="live")` | 实时行情 |
| `quotes_batch` | `(symbols, *, provider=None, currentness="live") -> BatchResult` | 逐 symbol 三态审计 |
| `snapshot` | `(symbol, *, provider="tdx", currentness="live")` | 盘口快照 |
| `minute` | `(symbol, *, provider="tdx", currentness="live")` | 当日分时（**默认 tdx 已下线**，抛 `NotImplementedFeature`；分时改用声明该能力的 Web Provider） |
| `trades` | `(symbol, *, provider="tdx", start=0, count=0, currentness="live")` | 逐笔成交（**默认 tdx 已下线**，抛 `NotImplementedFeature`） |
| `security_count` | `(*, market=0, provider="tdx", currentness="business")` | 证券数量 |
| `security_list` | `(*, market=0, start=0, provider="tdx", currentness="business")` | 证券列表分页：**已下线**，总是抛 `CommandOffline` |
| `stream` | `(symbols, *, provider="tdx", interval=1.0, diff_only=False, max_queue=1024, on_quote=None, on_error=None) -> StatefulQuoteStream` | 流式订阅 |
| `execute` | `(spec: QuerySpec) -> QueryResult` | 通用面：任何 capability 同一入口 |
| `call` | `(capability, *args, provider=None, channel=None, currentness="business", **kwargs)` | 便捷通用入口 |
| `execute_with_policy` | `(spec, *, policy: FallbackPolicy) -> OrchestratedResult` | 显式跨源编排 |
| `typed` | `(query: CapabilityQuery, **kwargs) -> TypedQueryResult` | 冻结 dataclass 契约 → 强类型记录 |
| `capabilities` | `() -> tuple[str, ...]` | 能力发现面：**只有名字、没有可用性**，172 项的构成与发不出去的那几个见下节「能力发现面：只有名字，没有可用性」 |
| `close` | `()` | 释放内核连接 |

`AsyncClient` 是同名异步镜像（`async with AsyncClient() as client: ...`）；
传 `policy=` 时 `bars/quotes` 返回 `OrchestratedResult` 而非 `QueryResult`。

`currentness` 的缺省只有上表这一处：`Client.<方法>` 的签名与 `UnifiedRuntime.<方法>` 的缺省
逐字相等，`call`/`_call_core` 不再另立一份"谁能转、谁不能转"的名单（判据见
`tests/architecture/test_face_exposure_projection.py` 的
`test_core_dispatch_is_derived_not_recopied`）。四张服务面的泛型入口（`POST /v13/query/{capability}`、
WS `query`、MCP `query_capability`、CLI `query`）缺省都是 `business`，它的含义是"调用方没表态"，
此时内核看到的是这条能力自己的缺省口径（`bars`→`historical`、`security_*`→`business`、
其余→`live`）；要指定口径就直接点名它——显式写 `business` 与不写，在核心集上是同一个值。

### 能力发现面：只有名字，没有可用性

```python
from tstdx import Client

Client.capabilities()   # 172 项 capability 名，按字典序排好
```

发现面有三处出口，交付的都是**纯名字**：

| 出口 | 形状 | 状态字段 |
|------|------|----------|
| `Client.capabilities()` / `AsyncClient.capabilities()` | `tuple[str, ...]`，172 项 | 无 |
| `GET /v13/capabilities` | `{"capabilities": [...], "providers": {provider: {channel: [...]}}}` | 无 |
| WS `runtime.capabilities` | 同上，两份名单 | 无 |

名单的构成是一个可复算的恒等式：172 = 7 个内核直绑能力 ∪ 167 个 catalog 迁移能力，并且与
`PROVIDERS` 注册表（11 Provider × 56 channel）里出现过的能力名集合逐字相等。三处出口在形状上
就没有放 `available`/`offline` 的位置——条目类型清一色是 `str`（F-66 裁决 (c)：发现面的形状
不动，把这条口径写清）。于是**「名字在名单里」只承诺"这条能力有实现、参数契约可校验"，不承诺
"调用会拿到数据"**。

tdx 这条链上有 8 个名字一调就必然失败：6 个名字踩在被账本判 `offline` 的命令上、2 个名字被
request/parser 仍是 inferred 的结构化拦截挡在发包前。下表由
`tests/architecture/test_offline_capability_honesty.py` 现推——名字集合、命令号、tdx 侧实现、
出路 Provider 四个字段全部来自「命令账本 + `client/core.py` 的 `_UNVERIFIED_STRUCTURED_BLOCK` +
`catalog/capability.py` 绑定表 + `_t_*` 调用图」，在这里手抄一份过期数字过不了门禁。

| 发现名 | tdx 侧实现 | 命令号 | 为什么发不出去 / 一调即抛 | tdx 之外的出路 |
|--------|-----------|--------|---------------------------|----------------|
| `auction` | `auction_snapshot` | `0x056A` | 账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |
| `block_quotes` | `block_quotes` | `0x07E5` | 账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |
| `minute` | `minute_today` | `0x0537` | request/parser 仍为 inferred，结构化 API 在发包前拦下，抛 `NotImplementedFeature` | `baidu`、`eastmoney`、`tencent` |
| `minute_history` | `minute_history` | `0x0FB4` | 账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |
| `security_list` | `security_list` | `0x044D` | 账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |
| `security_list_all` | `export_security_list` | `0x044D` | 自己不写命令号，经 `security_list` 的分页调用由传递闭包判出；账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |
| `trades` | `trade_today` | `0x0FC5` | request/parser 仍为 inferred，结构化 API 在发包前拦下，抛 `NotImplementedFeature` | `baidu`、`tencent` |
| `volume_price` | `volume_price_dist` | `0x051A` | 账本 offline（多主站实测无响应），抛 `CommandOffline` | 无——只有 tdx 声明它 |

这张表只覆盖 tdx 命令账本管得到的 25 个名字（7 个内核直绑 + 18 个 tdx 客户端族 catalog 绑定）；
其余 147 个名字走 web 会话 / web adapter / channel adapter / composed 四类后端，不经过命令账本，
也就无从在这里判生死——它们的可用性由各自的 Provider 契约与 `tests/` 冒烟负责。一处显式登记的
解析盲区是 `f10`：`f10_client` 的分派按 capability 分岔（`runtime/executor.py` 里 `f10` 走
`client.download`、其余走 `client.catalog`），绑定表的 `method` 只是标签，所以这一格对不上实现；
门禁因此同时要求 F10 族账本里一条 offline/拦截命令都没有——哪天 F10 命令下线，这条断言当场红，
盲区不许变成漏报。

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

`quotes` 的失败形状与同族便捷方法共用一条判据：**全部**标的都失败即抛（断网时就是
`AllHostsUnreachable`，与 `bars` 同形），**部分**标的失败则照常返回已集到的行，并携带
一条 `quotes_partial_failure` 告警——于是 `strict=True` 能拒收一份不完整的答案。
"这只代码没有行情"与"根本连不上"在 wire 上不是同一个形状。

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
6 个直连传输层命令（`probe`/`goods`/`f10`/`blocks`/`list`/`quotes-snapshot`，落点口径见 §3 CLI 表），
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

标题里的"10 路由"只数上表这 10 支业务端点；`create_runtime_app()` 返回的 FastAPI 实例还会自带
`/openapi.json`、`/docs`、`/docs/oauth2-redirect`、`/redoc` 四支文档页与三份异常处理器，它们不发
行情、也不受上面的未声明字段口径管辖。

`/v13/capabilities` 交付两份纯名字列表，没有状态字段：口径与那些发不出去的名字见
§2「能力发现面：只有名字，没有可用性」。

### WebSocket JSON-RPC（10 方法）

```python
from tstdx.integration.runtime_ws_server import serve_runtime_ws
```

方法：`quotes`、`bars`、`snapshot`、`minute`、`trades`、`security.count`、
`security.list`、`query`、`runtime.capabilities`、`runtime.health`。

监听地址与路径由 `RuntimeWsConfig` 决定，默认 127.0.0.1:8765 上的 `/v13/ws`；连到别的路径
会被以 1008 状态码关闭（reason 为 "unsupported path"）。`serve_runtime_ws` 是协程，返回 websockets
库的 server 对象，本仓库不给它 `-m` 入口，也没有对应的命令行子命令（§3 CLI 表那一格里没有它）。

`runtime.capabilities` 与 HTTP 同一份两份纯名字列表，同样**只有名字、没有可用性**
（§2「能力发现面：只有名字，没有可用性」）。

### MCP stdio（9 工具）

```python
from tstdx.integration.mcp import create_mcp_server, TOOLS
```

工具：`query_capability`（通用入口）+ `get_bars`、`get_quote`、`get_quotes`、
`get_snapshot`、`get_minute_today`、`get_trades`、`get_security_count`、
`get_security_list`。

### CLI（31 子命令 / 36 个叶子命令）

```bash
tstdx --help
```

下表是 CLI 的**接口面本身**，不是一句"见 `--help`"：命令名、位置参数、旗标集合与落点全部
由 `tests/architecture/test_cli_reference_table.py` 从 `argparse` 现值与处理器源码重新派生，
逐格核对。改一个旗标、加一支命令、或把一支命令从内核挪到直连传输层而不改这张表，门禁就红。

**落点**只有六种，含义是"这条命令的数据从哪儿来"：

| 落点 | 含义 |
|------|------|
| 内核·typed | `Client` 的类型化方法或 `client.call`，stdout 打 `serialize_result` 信封（带 `provenance`/`currentness`） |
| 内核·rows | 同一内核，经 `tstdx/cli/runtime_commands.py::_ClientRows` 把信封拆成裸行；`--json` 决定行数组还是人读表格 |
| 直连传输层 | 不经内核，直接 `TdxClient` / `get_client(...)`——协议诊断面，不是第二套能力执行路径 |
| 服务面宿主 | 拉起 HTTP 应用本身 |
| 传输·诊断 | 主站解析与测速（`tstdx.transport.*` / `tstdx.tools.host_audit`） |
| 反馈 | `tstdx.feedback`，不发行情请求 |
| 元信息 | 只打印包版本，不发请求也不建连接 |

| 命令 | 位置参数 | 旗标 | 落点 |
|------|----------|------|------|
| `adjusted-bars` | `symbol` | `--method` `--period` `--count` `--timeout` `--json` | 内核·rows |
| `all-market` | — | `--node` `--source` `--page-size` `--max-pages` `--timeout` `--json` | 内核·rows |
| `baidu` | `symbol` | `--kind` `--period` `--count` `--end-time` `--limit` `--timeout` `--json` | 内核·rows |
| `bars` | `symbol` | `--provider` `--fallback` `--host` `--period` `--count` `--start` `--adjustment` | 内核·typed |
| `blocks` | `block_type` | `--count` `--timeout` `--json` | 直连传输层 |
| `capabilities` | — | — | 内核·typed |
| `changes` | — | `--types` `--page` `--size` `--json` | 内核·rows |
| `f10` | `symbol` | `--file` `--timeout` `--json` | 直连传输层 |
| `feedback stats` | — | `--json` | 反馈 |
| `feedback submit` | — | `--message` `--endpoint` `--store-dir` | 反馈 |
| `fund estimate` | `code` | `--timeout` `--json` | 内核·rows |
| `fund list` | — | `--timeout` `--json` | 内核·rows |
| `fund nav` | `code` | `--page-size` `--page-index` `--timeout` `--json` | 内核·rows |
| `goods` | `symbol` | `--kind` `--period` `--count` `--timeout` `--json` | 直连传输层 |
| `hosts audit` | — | `--family` `--timeout` `--samples` `--workers` `--report` `--markdown` `--ranking-file` `--no-save-ranking` `--strict` `--quiet` | 传输·诊断 |
| `hosts list` | — | `--timeout` | 传输·诊断 |
| `hosts scan` | — | `--timeout` | 传输·诊断 |
| `hot` | — | `--page` `--size` `--json` | 内核·rows |
| `index constituents` | `code` | `--timeout` `--json` | 内核·rows |
| `list` | `market` | `--start` `--count` `--timeout` `--json` | 直连传输层 |
| `margin` | `symbol` | `--days` `--timeout` `--json` | 内核·rows |
| `minute` | `symbol` | `--provider` `--host` | 内核·typed |
| `minute-klines` | `symbol` | `--period` `--count` `--timeout` `--json` | 内核·rows |
| `probe` | `cmd` | `--market` `--code` `--rate-limit` `--archive-dir` `--allow-trading-hours` `--timeout` `--json` | 直连传输层 |
| `query` | `capability` | `--provider` `--channel` `--currentness` `--args` `--kwargs` | 内核·typed |
| `quotes` | `symbols` | `--provider` `--fallback` `--host` | 内核·typed |
| `quotes-snapshot` | `symbols` | `--timeout` `--json` | 直连传输层 |
| `sector-flow` | — | `--board` `--sort` `--limit` `--timeout` `--json` | 内核·rows |
| `security-count` | — | `--provider` `--host` `--market` | 内核·typed |
| `security-list` | — | `--provider` `--host` `--market` `--start` | 内核·typed |
| `serve` | — | `--port` `--bind` | 服务面宿主 |
| `server-test` | — | `--timeout` | 传输·诊断 |
| `snapshot` | `symbol` | `--provider` `--host` | 内核·typed |
| `stream` | `symbols` | `--provider` `--host` `--interval` `--diff-only` `--max-queue` `--timeout` `--seconds` | 内核·typed |
| `trades` | `symbol` | `--provider` `--host` `--start` `--count` | 内核·typed |
| `version` | — | — | 元信息 |

四条组命令（`feedback` / `fund` / `hosts` / `index`）必须给出叶子命令才能执行，所以上表的
36 行 = 27 支单命令 + 9 支叶子命令。`--json` 只出现在 rows 与直连传输层那两支落点上：
内核·typed 那九支**只发 JSON 信封，没有 `--json` 可关**。

下面这一段是 36 支叶子命令各一条可直接照抄的示例：示例里的每一个参数都会被真实 parser 解析
（`test_every_documented_cli_example_parses`），`test_every_leaf_command_has_an_example` 再要求
36 支叶子一支不缺。

```bash
tstdx version                                              # 包版本
tstdx capabilities                                         # 内核能力名清单（只有名字，无可用性）
tstdx query stock_changes --args [[8201]] --kwargs {"size": 5}   # 任意能力的通用入口
tstdx quotes sh600519 sz000001                            # 实时行情
tstdx bars sh600519 --period day --count 80               # 日 K 线
tstdx snapshot sh600519                                    # 规范快照
tstdx minute sh600519                                      # 今日分时
tstdx trades sh600519 --count 100                          # 逐笔成交
tstdx security-count --market 0                             # 某市场的代码总数
tstdx security-list --market 0 --start 0                   # 分页代码表
tstdx stream sh600519 --interval 3 --diff-only --seconds 30    # 流式订阅
tstdx hosts audit --family quotation --timeout 3           # 主站巡检（5 族）
tstdx hosts list                                            # 当前生效的主站池
tstdx hosts scan                                            # 并发测速并写排名文件
tstdx server-test                                           # 主站连通性测速
tstdx serve --bind 127.0.0.1 --port 8000                   # HTTP 网关（上面那 10 路由）
tstdx feedback submit --message "这里写问题描述"            # 反馈上报
tstdx feedback stats --json                                 # 本地反馈统计
tstdx probe 0x052D --market 0 --code sh600000              # 未知命令主动探测
tstdx changes --types 8201,8193 --size 10                   # 盘中异动池
tstdx hot --page 1 --size 10                                # 股吧人气榜
tstdx margin sh600519 --days 10                             # 融资融券明细
tstdx sector-flow --board industry --sort main_net --limit 10    # 板块资金流
tstdx adjusted-bars sh600519 --method qfq --count 100       # 复权 K 线
tstdx all-market --node hs_a --source sina --max-pages 1    # 全市场行情摘要
tstdx minute-klines sh600519 --period 5min --count 48       # 分钟 K 线
tstdx baidu sh600519 --kind kline --period day --count 40   # 百度财经源
tstdx fund nav 000001 --page-size 20                        # 基金历史净值
tstdx fund estimate 000001                                  # 基金盘中估值
tstdx fund list --json                                      # 基金代码列表
tstdx index constituents 000300                             # 指数成分股
tstdx blocks 1 --count 50                                   # 板块行情（直连传输层）
tstdx goods AU2412 --kind quote                             # 商品行情（直连传输层）
tstdx f10 sh600519                                          # F10 栏目目录
tstdx f10 sh600519 --file 公司概况                          # F10 正文下载并解析
tstdx list 0 --count 100                                    # 代码表（直连传输层）
tstdx quotes-snapshot sh600519 sz000001 --json              # 批量快照（直连传输层）
```

`feedback submit` / `fund estimate` / `index constituents` 的 `--timeout` 现在两种位置都吃：
`tstdx fund estimate 000001 --timeout 5` 与 `tstdx fund --timeout 5 estimate 000001` 等价
（第 22 轮 G26 之前只有后一种能解析，前一种当场 exit 2）。

`serve` 只承载上面那张 HTTP 表（10 路由），**不承载 WebSocket**：`create_runtime_app()` 里没有任何
`websocket` 路由，JSON-RPC 面要另外跑 `await serve_runtime_ws()`（`tstdx/integration/runtime_ws_server.py`，
默认 `127.0.0.1:8765`、路径 `/v13/ws`，见 `RuntimeWsConfig`）——那一支没有 `python -m` 入口，
也没有 `tstdx` 子命令，只能作为协程由调用方托管。二者不是同一个端口上的两个协议。
`tstdx.integration.runtime_tasks.RuntimeTaskStore`
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

`Quote` 的三格在 7709 实时面上没有来源，别按『可能有值』写代码：`datetime` 恒为 `None`、
`bid` / `ask` 恒为空列表，而 `price`/`volume`/`amount` 都是真数。0x0530 的响应里五档尾段
长度随标的而变、精确布局尚未由真机 golden 锁定，解析器按『不臆造未锁定布局』的契约把整段
原样收进 `extra['tail_leb128']`（未识别的 `u4` 进 `extra['_u4']`），因此这三格是**主动留空**
而不是丢字段。要时间戳请取发起请求的时刻（响应不含它）；要盘口深度，这条链上目前没有
任何接口给得了。实测口径与判据见 `docs/tdx_status.md` §一之二。

### 市场拼写（`market=` 收哪些写法）

`market` 的值域由入口解析器那张表派生，不是文档手抄：`0`/`1`/`2` 与 `sz`/`sh`/`bj`
在六张面（库/CLI/HTTP/WS/MCP）上同答案，大小写与空白不敏感，其余写法一律
`ParseError`。但**入口收 ≠ 协议走得通**：`Symbol.tdx_market` 给 SZ/SH/BJ 分别返回
0/1/2，而 symbol-based 的 7709 请求在 `split_symbol` 处只放行 0/1——北交所标的在这里抛
`ParseError`（"尚未验证 market=2"），不是静默按沪市发。

```python
from tstdx.domain.integrity import tdx_market_ids  # 协议侧认得的市场 id，现读
```

### K 线周期拼写（`period=` 收哪些写法）

周期词表只有 `tstdx/domain/period.py` 一处声明：规范档 `CANONICAL_PERIODS` 11 个
（`tick`/`1min`/`5min`/`15min`/`30min`/`60min`/`day`/`week`/`month`/`season`/`year`），
公开别名 `PERIOD_ALIASES` 29 个，合起来 40 种写法。空白与大小写不敏感
（`normalize_bar_period` 先 `strip().lower()` 再查别名），`"  5M "` 与 `"5min"` 同答案。
内核、CLI、HTTP、WS、MCP 五张面吃同一份派生表，不再各抄一张——第 18 轮之前那五份手抄件
互相分叉，量出 15 个"一面收、另一面拒"的拼写。别名解析只发生在公开入口：内核与会话面
先 `normalize_bar_period` 再把规范拼写交所选源，而上游裸源只认规范拼写。

```python
from tstdx.domain.period import CANONICAL_PERIODS, PERIOD_ALIASES, normalize_bar_period
```

各面的实际接受集（第 19 轮离线实测，HEAD `2ae022b`）。"接受写法数"是这一入口能认下的
拼写个数，"服务档"是它真能取回的规范周期档数——两者之差就是这一面派生出来的别名：

| 入口 | 服务档 | 接受写法数 | 不服时的行为 |
|---|---|---|---|
| `Client.bars` / 统一 `bars` 查询（CLI `bars --period`、HTTP、WS、MCP 同此） | `1min`/`5min`/`15min`/`30min`/`60min`/`day`/`week`/`month`/`season`/`year`（10） | 39 | `ValidationError` `[E1010]`，消息带该 channel 的可选集 |
| `WebQuoteSession.klines`（ifzq 会话面） | 5 个分钟档 + `day`/`week`/`month`（8） | 31 | `ValueError`，列出 `KLINES_PERIOD_ALIASES` |
| `WebQuoteSession.history(source="sina")` | `5min`/`15min`/`30min`/`60min`/`120min`/`1200min`/`day`（7） | 20 | `ValueError` |
| `WebQuoteSession.history(source="eastmoney")` | `1min`/`5min`/`15min`/`30min`/`60min`/`day`（6） | 22 | `ValueError` |
| `baidu_kline` | `day`/`week`/`month`（3） | 13 | `ValueError`，并指路"分钟线请走腾讯面" |
| `SinaHistoryKlineSource`（裸源，只收规范拼写） | 同 sina 会话面（7） | 7 | `ValueError` |
| `EastmoneyHistoryKlineSource`（裸源） | 同东财会话面（6） | 6 | `ValueError` |
| `KlineSource`（腾讯 fqkline 裸源） | 5 个分钟档 + `day`/`week`/`month`（8） | 8 | `ValueError` |
| 腾讯 mkline 分钟裸源 | 5 个分钟档（5） | 5 | `ValueError` |

三条读表时要记住的口径：

- **`120min`/`1200min` 是新浪专属档**，故意不进规范集合：tdx 协议没有对应的 K 线
  category，把它们收进规范档就得伪造一个协议号，而本库的规矩是不猜协议字节。它们只由
  `sina/history_kline` 声明，因此 `bars(period="120min")` 走默认路由会被
  `[E1010]` 拒掉，写上 `provider="sina"` 才成立。
- **`tick` 是规范档但不是 K 线档**：分笔走 `ticks` 能力，`Client.bars(period="tick")`
  同样报 `[E1010]`。
- **"这一面不服务"必须显式报错，不许换个周期返回**。第 19 轮前新浪表里有
  `"1min": 5` 这一格，把 1 分钟请求悄悄换成了 5 分钟线；百度表里有 `"1m": 3`，而域内
  `1m` 是 1 分钟，即 1 分钟在百度面会被解成月线。两格都已删掉，换成了上面那一列的
  `ValueError`。

### 出口处的域尺子（`tstdx.domain.integrity`）

解码越域值在结果里看得见，靠的是这四个名字：

```python
from tstdx.domain.integrity import (
    FIELD_CHECKERS,
    illegal_code,
    illegal_market,
    row_violations,
)
```

`row_violations(row)` 是客户端出口（`_forward_decode_caveats`）唯一的判据：一条记录里
哪个字段落在库自己声明的域外，它就点名列出哪个字段，发 `field_out_of_domain` 告警并随
`ResultMeta.warnings` 上五张面；`strict=True` 时同一条判据升级为 `TruncatedDataError`。
它的用武之地是记录布局尚未由真机 golden 锁定的那两条命令（`0x000F` 资本变动 /
`0x0010` 财务）——页内字节数对得上、解码层因此一个字都不记，而值已经错位。
域表本身（`FIELD_CHECKERS` 覆盖哪些字段、边界是多少）以模块现值为准，
`tests/unit/test_golden.py` 用实采样本重放盯着它不许松口。

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
