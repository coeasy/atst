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
- [8. 可观测性与反馈（Observability / Feedback）](#8-可观测性与反馈observability--feedback)

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

心跳与空闲回收在异步面**不是"构造即起跑"**：`AsyncConnectionPool` 只在第一次真正握住一条
socket 时（`_get_conn_locked`）武装那条线程，`AsyncTdxClient.open()` 与 `async with pool`
都不武装它；同步池则在构造器里就武装。两边因此"从什么时候开始回收空闲槽位"差这一刻。
第 31 轮之前异步面**从未起跑过**——`heartbeat_interval` / `idle_timeout` 传进去也只是被存
起来，是幻影旋钮；全包武装点唯一由 `tests/transport/test_async_sweeper_arm.py` 现扫核对。

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

client = get_client("stock")  # TdxClient（缺省 kind，可不传）
client = get_client("goods")  # GoodsClient
client = get_client("ex")     # ExMarketClient
client = get_client("mac")    # MacClient
client = get_client("f10")    # F10Client
```

`get_client` 只认这 5 个 kind——判据是运行期读 `_CLIENT_REGISTRY`，不是抄一份名单：未知
kind 显式抛 `ValueError`（消息里带合法值全集），既不做大小写/空白归一，也不静默回落到
`TdxClient`，所以把 `"std"` 或 `"async"` 写进调用点会当场炸而不是拿到一个能用的对象。
**异步面不在这张表里**：异步传输客户端 `AsyncTdxClient` 由 `tstdx.client` 直接导出，
`tstdx.AsyncClient` 则是 `Client` 那套语义/运行时契约的异步门面（它内部持有一份 `Client`），
两者都不经本工厂构造。其余 kwargs 原样透传给对应类的 `__init__`。

### 交易面（TradeClient，只有库面且仅模拟）

`tstdx.trade` 是 0x1000 交易协议的独立面：帧编解码、密码混淆、查询类别词表和一台模拟券商
都在库里，但**只有库面**——CLI 的 31 支子命令、HTTP 的 10 路由、WS 的 10 方法、MCP 的 9 工具
都不挂它。这不是漏接线，是红线：本库不接真实券商，`SocketTransport.connect()` 一律抛
`TradingUnavailable` `[E4030]`。

```python
from tstdx.trade import TradeClient

with TradeClient() as c:                # 缺省 transport 是 SimTransport
    c.login("100001", "123456")         # DEFAULT_ACCOUNTS 里的演示账密
    c.buy("600519", 1000, quantity=100)  # 价格单位是「分」
    c.query_stocks()                    # 持仓
    c.query_shareholders()              # 股东代码（登录账号 + 账户名）
    c.query_deals()                     # 当日成交——只可能来自显式注入
```

七个查询出口里六个是 `query(category)` 的具名包装，与 `QUERY_CATEGORY_*` 常量一一对应：
`query_cash`、`query_stocks`、`query_orders`、`query_deals`、`query_cancelable`、
`query_shareholders`。第 26 轮 F-113 给后三个补上调用侧证据——`tests/trade/test_client.py`
现在既逐条断言各自的返回形状，也逐条断言"包装 == 按类别直查"；此前这三支能 import、
有 docstring，全仓却没有任何一处按名调用过它们。

**成交不在本库的范围内**：撮合发生在交易所，所以模拟券商不会自动把委托变成成交，只下过单时
`query_deals()` 恒为 `[]`（空表在这里是"确实没有成交"，不是查不到）。要看到成交必须显式注入
`c.transport.simulator.fill_order(order_id=1, qty=100, at="09:30:00")`，注入后成交、可撤单与
资金/持仓账本同步变化。类别号没写进模拟器账本（融资融券、新股申购等）时抛 `TradeError` 而不是
返回空表——"这类查不了"与"这类今天没有记录"在调用方看来必须是两个答案。

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
| `minute` | `(symbol, *, provider="tdx", currentness="live")` | 当日分时（**默认 tdx 已下线**，抛 `NotImplementedFeature`；分时改用声明该能力的 Web Provider：`tencent` / `eastmoney` / `baidu`） |
| `trades` | `(symbol, *, provider="tdx", start=0, count=0, currentness="live")` | 逐笔成交（**默认 tdx 已下线**，抛 `NotImplementedFeature`；Web 出路 `tencent` / `baidu` 按页取，写 `start` / `count` 会当场 `ValidationError` 而不是被丢掉） |
| `security_count` | `(*, market=0, provider="tdx", currentness="business")` | 证券数量 |
| `security_list` | `(*, market=0, start=0, provider="tdx", currentness="business")` | 证券列表分页：**已下线**，总是抛 `CommandOffline` |
| `stream` | `(symbols, *, provider="tdx", interval=1.0, diff_only=False, max_queue=1024, on_quote=None, on_error=None) -> StatefulQuoteStream` | 流式订阅 |
| `execute` | `(spec: QuerySpec) -> QueryResult` | 通用面：任何 capability 同一入口 |
| `call` | `(capability, *args, provider=None, channel=None, currentness="business", **kwargs)` | 便捷通用入口 |
| `execute_with_policy` | `(spec, *, policy: FallbackPolicy) -> OrchestratedResult` | 显式跨源编排 |
| `typed` | `(query: CapabilityQuery, **kwargs) -> TypedQueryResult` | 冻结 dataclass 契约 → 强类型记录 |
| `capabilities` | `() -> tuple[str, ...]` | 能力发现面：**只有名字、没有可用性**，172 项的构成与发不出去的那几个见下节「能力发现面：只有名字，没有可用性」 |
| `close` | `()` | 收尾**本实例自建**的内核（`runtime=` 传进来的那份不碰）。默认内核跨调用不持有连接，所以它关的是"借来的东西"这一格所有权，不是连接池——见下节 |

`AsyncClient` 是同名异步镜像（`async with AsyncClient() as client: ...`）；
传 `policy=` 时 `bars/quotes` 返回 `OrchestratedResult` 而非 `QueryResult`。

**`close()` 到底释放什么**（第 31 轮实测，判据 `tests/runtime/test_close_chain_ownership.py`）：
构造一个 `Client()` 之后进程的线程数增量为 0，它手里的执行器只有 `timeout` / `hosts` /
`vipdoc_root` / `config` / `_bindings` 五个值，内置执行器甚至没有 `close()` 这个方法——每次
取数都是现场构造家族客户端、`finally` 释放（第 31 轮 31-B4 让四条分支收成同一形状）。因此
`close()` 真正兑现的是两件事：**注入执行器**（自带池或外设的那份）的收尾口子，以及
「谁造谁关」这条协议在 HTTP/WS/MCP 三张服务面上的统一落点。两件它**不**做的事：
不关你传进来的 `runtime`，也**不停止由本客户端 `stream()` 出来并已 `start()` 的流**——那条
worker 线程归持有流的人管，`stream.stop()` 才是它的收尾（实测：`close()` 之后线程照跑，
`stop()` 毫秒级返回；它是 daemon 线程，不会挂住进程退出，但订阅会在你看不见的地方继续跑）。

`currentness` 的缺省只有上表这一处：`Client.<方法>` 的签名与 `UnifiedRuntime.<方法>` 的缺省
逐字相等，`call`/`_call_core` 不再另立一份"谁能转、谁不能转"的名单（判据见
`tests/architecture/test_face_exposure_projection.py` 的
`test_core_dispatch_is_derived_not_recopied`）。四张服务面的泛型入口（`POST /v13/query/{capability}`、
WS `query`、MCP `query_capability`、CLI `query`）缺省都是 `business`，它的含义是"调用方没表态"，
此时内核看到的是这条能力自己的缺省口径（`bars`→`historical`、`security_*`→`business`、
其余→`live`）；要指定口径就直接点名它——显式写 `business` 与不写，在核心集上是同一个值。

`provider` / `channel` / `currentness` 是 `call` 自己的**关键字形参**（路由字段），不是能力入参。
四张泛型入口都把它们从各自的顶层位置取出后显式递交，因此它们**不允许**再出现在 `kwargs` 里：
同一个键两处都出现时 Python 会在调用表达式求值处抛裸 `TypeError`，四张面统一在这一步之前判死
（E1010 / HTTP 422 / JSON-RPC -32602），错误 `context.reserved_fields` 点出冲突的键名。要在泛型
入口上指定路由，写顶层字段（HTTP body 的 `provider`/`channel`/`currentness`、WS `params` 与 MCP
`arguments` 的同名键、CLI 的 `--provider`/`--channel`/`--currentness`）。

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
`QueryPlan` 字段：`spec, provider, channel, fingerprint, budget`——`fingerprint` 是
`QueryFingerprint`（一次请求的稳定语义身份：`value` 为摘要、`canonical` 为参与摘要的那份
规范化载荷；内核把每个结果绑回产出它的那份 plan，并在序列化里原样发出它，但**请求路径上没有任何
地方读它去跳过一次 Provider 调用**——本库无缓存，它不是缓存键），`budget`
（`ExecutionBudget`）是 `deadline_ms` 的唯一载体，逐跳约束传输层超时；plan 上不再
另存 deadline、批量上限或 channel 布尔位，那些字段曾经无人读取。参数在规划期按
Provider 实现的**真实签名**校验，不合法即 `ValidationError` 且不发请求。

`deadline_ms` 不是硬取消：它约束的是**每一跳建立时**的超时上界（取配置值与剩余预算的
小者，预算见底则在建连之前抛 `ReadTimeout`），一跳已经发出请求后只能等它自己的 socket
超时回来，而 `[core] max_retries` 的重试在传输池内不再重读预算。所以总墙钟的上界是
"deadline + 最后一跳的容忍"，不是 deadline 本身。预算的作用域是**一次 `execute()`**：
`stream` 的每一拍轮询是一次独立请求，各拿各的预算，不共用一条总墙钟。

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

三样都由内核那条循环**当场喂满**（第 29 轮）：`errors[symbol]` 取自逐只失败的原异常
（`to_error_envelope`），`requested` 是本次去重后的符号元组（含失败与缺失的那些），
`partial` 因此能表达"要了 3 只、只成 2 只"。此前那条循环只递 `items` 一样，于是
`errors` 恒 `{}`、`requested` 恒 `()`、`partial` 恒 `False`——声明在 `BatchResult` 上、
真源却不存在。

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
from tstdx import Provenance, ProvenanceKind                       # 结果溯源载荷
```

`validate_runtime_provenance` 在结果 provenance 与请求身份不一致时抛错（换源即抛）；
`audit_runtime()` 启动时对账 registry / catalog / `DIRECT_BINDINGS` 三方事实。

`ProvenanceKind` 只有一个成员 `DIRECT`：运行期不做结果缓存，也不回放、不合成，因此不存在
第二种出处可标。这不是注释而是门禁——`test_every_declared_provenance_kind_has_a_producer`
拿枚举成员名当分母、拿 `tstdx/` 里的具名构造点当分子做双向差集，加一个没有生产点的成员
当场变红。公开构造入口也只有 `Provenance.direct(plan)` 一个：它写死 `kind=DIRECT`、
`requested_provider=plan.provider`、`fallback=False`，而 `cache_tier` 停在 `None`
（8 个字段里唯一无生产者的一位，wire 上照原样输出，正因为调用方断言它恒为 `None`）。
`fallback=True` 是另一件事：它只可能由显式 `FallbackPolicy` 走到
`ProviderOrchestrator`（§ 上文）之后 `replace` 出来，与出处种类无关。

### Web 源登记（`tstdx.web.sources.KNOWN_SOURCES`，31 个）

```python
from tstdx.web.sources import KNOWN_SOURCES, SourceSpec   # 源名 → SourceSpec
```

这一张表是内核之外那张 **HTTP 适配器命名面**的唯一读者可见登记。三个地方吃的源名以它为准：
`docs/configuration.md` 的 `[web] enabled_sources` 与 `[web] rate_limit` 都要求键属于
`KNOWN_SOURCES`（文档过去只写了这句约束、从没列出合法取值），`WebQuoteSession` 的方法按
`summary` 那一列对应到源名，`tstdx.web.normalize` 按 `SourceSpec` 的三个 scale 字段换算单位。

「接口告诫」一列是 `SourceSpec.notes` 原文，逐格对齐，不做二次抄写：单位坑（哪些源要 ×100、
哪些量是手/万元）、鉴权要求（Referer / cookie）、以及**已下线的端点**。下表与运行期登记表由
`tests/architecture/test_web_source_table.py` 双向对账（源名集合相等 + 摘要/能力/告诫三列逐格
相等），任何一格改代码不改文档即变红；生成方法见该文件。

| 源名 | 摘要 | 能力 | 接口告诫（`SourceSpec.notes`） |
|---|---|---|---|
| `sina` | 新浪财经实时行情（hq.sinajs.cn） | `quote` / `rank` / `all_market` | 必须带 Referer: https://finance.sina.com.cn；无 Referer 返回 403 |
| `tencent` | 腾讯财经实时行情（qt.gtimg.cn） | `quote` / `kline` / `minute_kline` / `minute` / `all_market` | 成交量单位为手、成交额单位为万元，必须归一化 |
| `eastmoney` | 东方财富行情（push2.eastmoney.com） | `quote` / `kline` / `rank` / `stock_boards` | 价格字段 f2/f3/f4 均为 ×100 整数；高频易触发 IP 封禁 |
| `jsl` | 集思录（可转债 / 分级基金 / ETF） | `bond` / `etf` | 部分接口需登录 cookie；限速严格 |
| `hk` | 港股实时行情（qt.gtimg.cn/q=hkXXXXX） | `quote` | 港股通标的；成交量单位为股；价格/成交额以港元(HKD)计，extra.currency 标记原生币种，库不做汇率换算 |
| `hk_sina` | 新浪港股实时行情（hq.sinajs.cn hk 前缀） | `quote` | SinaHkSource 为 SinaSource 子类，复用新浪港股 hq_str 解析；其 source_name 返回 'hk'（归一化走 HK 的 identity），量为股、额为元(HKD)，extra.currency='HKD'。需带 Referer 同 SINA。 |
| `us` | 美股实时行情（qt.gtimg.cn/q=usXXXXX） | `quote` | 美股（NYSE/NASDAQ）；成交量单位为股；价格/成交额以美元(USD)计，extra.currency 标记原生币种，库不做汇率换算 |
| `kline` | 日 K 线（腾讯 ifzq.gtimg.cn / 东财 push2his） | `kline` | 日 K 线历史数据 |
| `boc` | 中国银行外汇牌价 | `fx` | 汇率数据，非行情 |
| `news` | 新浪个股新闻（vCB_AllNewsStock.php） | `news` | 个股新闻列表（标题/链接/时间）；A 股覆盖最全，港股/美股可能为空；新浪新闻 JSON 接口已失效，改采稳定 HTML 资讯页 |
| `minute_kline` | 分钟 K 线（腾讯 ifzq.gtimg.cn mkline） | `kline` / `minute_kline` | 1/5/15/30/60 分钟 K 线，**仅 A 股**（港股/美股 mkline 返回空，直连会抛错）。港股/美股分钟 K 线走东财 push2his（EastmoneyHistoryKlineSource），WebQuoteSession.klines/minute_klines 已按市场自动路由。 |
| `minute` | 当日分时（腾讯 web.ifzq.gtimg.cn minute/query） | `minute` | 当日 1 分钟分时（价格/均价/成交量）。接口累计量为「手」，解析层 MinuteSource 已 ×100 到股；量口径统一由解析层内联处理，normalizer 为 identity（不二次缩放）。 |
| `suggest` | 证券代码联想搜索（新浪 smartbox） | `suggest` | 输入拼音/汉字/代码片段 → 候选证券列表 |
| `ticks` | 逐笔成交明细（腾讯 stock.gtimg.cn detail） | `tick` | 每页条数以 tstdx.web.ticks.TICKS_PER_PAGE 为单一事实源（勿在此复写具体数字）；p 为页码（0 起）。量单位手、额单位元 |
| `trends` | 当日分时成交（东财 push2his trends2） | `tick` / `minute` | 1 分钟粒度，含均价；iscr=0 不复权、ndays=1 当日 |
| `global` | 外盘期货 / 外汇（腾讯 qt.gtimg.cn hf_ 前缀） | `quote` / `global` | 纽约原油/黄金、伦铜、布伦特原油等；字段为定长 14 列 |
| `market_stat` | 大盘统计（腾讯 qt.gtimg.cn s_ 前缀） | `quote` / `stat` | s_sh000001 形式；返回点数、涨跌额、涨跌幅、成交量(手)、成交额(万元) |
| `rank` | 通用排行（东财 push2 clist） | `rank` | fs 决定市场（m:0+t:6 深A 等），fid 决定排序字段；高频易断连 |
| `fund_flow` | 资金流（东财 ulist.np 实时 / fflow/kline 历史） | `fund_flow` | 主力=超大单+大单；占比字段 f184/f69/f75/f81/f87 为 ×100 整数 |
| `limit_pool` | 涨停 / 跌停 / 炸板池（东财 push2ex） | `limit_pool` | date 为 YYYYMMDD；非交易日返回空池。本源输出为 dict 列表，解析层已按 1/1000 直接换算，不调用 normalize_quote |
| `northbound` | 沪深港通资金（东财 kamt） | `fund_flow` / `northbound` | 金额单位为万元；status=3 表示已收盘 |
| `corporate` | 基本面与公司行为（东财 datacenter-web 报表族） | `corporate` / `ipo` | F10/公告/研报/股东/大宗/解禁/业绩/IPO 申购日历；报表名错误返回 code=9501 |
| `longhu` | 龙虎榜每日个股榜（东财 datacenter-web RPT_DMSK_TS_STOCKNEW） | `longhu` | 主力净流入=超大单净+大单净(元)；TRADE_DATE 过滤须用完整'YYYY-MM-DD 00:00:00' 字面值，否则 9201 空数据；机构席位/营业部买卖子报表名已失效(9501)，本源仅取每日上榜个股汇总 |
| `sina_fund_flow` | 新浪资金流历史（个股 ssl_qsfx_zjlrqs / 板块 ssl_bkzj_zjlrqs） | `fund_flow` / `history` | 净额(元)/换手率(%)等均为原生单位，normalizer 为 identity；必须带 page/num/sort 三参数，缺省会返回全历史(约 1MB)；板块代码用 SinaIndustryBoardSource 的 new_xxx 体系（旧 hangye_ZLxx 体系已停更，最新数据停留在 2020 年） |
| `wencai` | i问财自然语言选股（www.iwencai.com load-data） | `wencai` | 自然语言选股（如“连板3板以上”）；hexin-v cookie 由调用方注入（参数 cookie= 或环境变量 TSTDX_WENCAI_COOKIE），tstdx 不依赖任何第三方 cookie 中继服务；缺 cookie 时在 fetch 阶段抛 WebSourceError（构造不拦截，便于罐头测试）；返回 title+rows zip 后的 list[dict] |
| `stock_changes` | 盘中异动池（东财 push2ex getAllStockChanges） | `stock_changes` | 20 类异动枚举（火箭发射/大笔买入/60日新高…，2026-09 实测验证）；输出为 dict 列表（time/code/name/change_type/metrics），metrics 为异动指标数值列表（含义随类型不同，不强行归一）；非交易时段返回空 allstock 为合法状态 |
| `hot_rank` | 股吧个股人气榜（东财 emappdata stockrank，POST JSON） | `hot_rank` | 人气排名榜（rk 当前名次 + rc 较上期变动）；榜单仅含排名与代码，不含行情字段，如需行情请以返回 symbol 回查 quotes；globalId 由本库生成（uuid4），appId 沿用页面公开参数 |
| `baidu` | 百度财经（finance.pae.baidu.com selfselect） | `kline` / `minute` / `tick` / `quote` | 仅 A 股（stockType=ab）。坑：K 线 kline.volume 实为成交额(元)、kline.amount 实为成交量(手)，解析层须交换映射（volume=amount×100, amount=volume）；分时 amount 为含'万'字符串，优先用 oriAmount(元)。非官方接口，随时可能改版/下线；不提供 fund_flow（2026-05 起下线）。 |
| `fund` | 东财基金（天天基金：历史净值 / 实时估值 / 基金列表） | `fund_nav_history` / `fund_estimate` / `fund_list` | 数据型源（list[dict]/dict），不参与行情降级链；历史净值需 Referer http://fundf10.eastmoney.com/（适配器内置）；基金列表为全量 js 数组（约 2.7 万条，数 MB）。**实时估值已下线**：fundgz jsonp 接口 2026 年实测返回 404 页面，fetch_estimate 显式抛 SourceDeprecated。 |
| `margin` | 东财融资融券个股明细（datacenter-web RPTA_WEB_RZRQ_GGMX） | `margin` | 数据型源（list[dict]），不参与行情降级链；仅两融标的（非标的个股空列表）；DATE 倒序单页/翻页均可；金额单位元（RZYE 融资余额/RZMRE 融资买入/RZJME 融资净买/RQYE 融券余额/RZRQYE 两融余额），RZYEZB 为融资余额占流通市值比(%)。 |
| `index_cons` | 东财指数成分股（datacenter-web RPT_INDEX_TS_COMPONENT） | `index_constituents` | 数据型源（list[dict]），不参与行情降级链；TYPE 为指数族过滤（2026-09 与中证官网 XLS 交叉验证 5 族 jaccard=1.0）；单页上限 500，中证1000/中证2000 等大指数自动分页拉全量；weight 仅部分指数族提供（沪深300/上证50/中证500/科创50 有值）。 |

---

## 3. 服务面（Integration）

四个服务面的数据命令全部委托同一个 `Client`（含 `stream`：CLI 不自己构造流）。CLI 另有
6 个直连传输层命令（`probe`/`goods`/`f10`/`blocks`/`list`/`quotes-snapshot`，落点口径见 §3 CLI 表），
不经内核——它们是协议诊断面，不是第二套能力执行路径（口径见 `tstdx/cli/runtime_commands.py`
的模块 docstring，守卫是 `test_service_faces_never_import_the_web_layer` 与
`test_service_faces_never_build_a_stream_themselves`）。

"不经内核"不等于"不看配置"：这 6 支命令的连接参数出自
`tstdx.cli._common._transport_kwargs`，而它调的就是内核那一条
`tstdx.transport.pool.pool_settings_from_config`，所以 `[hosts] servers` / `[hosts]
slots_per_host` / `[core] timeout` / `[core] max_retries` / `[core]
heartbeat_interval` / `[rate_limit]` / `[security] use_tls` 在直连命令与主链路上是同一套
读数；显式 `--host` / `--timeout` 只赢它自己那一格（第 31 轮 31-C4 之前，这里只手抄了
`hosts` 与 `timeout` 两键，其余五个键在直连命令上当场蒸发）。六支直连命令一律用
`get_client(...)` 构造、再把客户端整体交进 `tstdx.cli._common.family_client` 那道保护区：
客户端交进去时池与心跳线程已经起跑，块体无论正常返回还是抛错都 `close()` 一次。保护区只
管释放、不管构造，为的是让 `with family_client(get_client("goods", …)) as c` 之后的 `c`
仍是 `GoodsClient`——把 kind 传进保护区里再查注册表，返回类型就塌成 `Any`，工厂那五道
`@overload` 在 CLI 面等于白设。两格判据分别是
`tests/architecture/test_cli_connection_contract.py` 与
`tests/architecture/test_client_family_transport.py` 的第 5 件事（射程是整个 `tstdx/`，
不只执行器）。

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

### 整数入参的六张面口径（第 25 轮 G34）

`count` / `start` 这类整数请求字段过去在每张面上各治各的：WS 自己 `int()`（打错一个字符，
服务端回 `-32603 / E9000`「内部错误」；`1.5` 被静默截成 `1` 并真的按截断值发出请求），MCP 用一个
钳位函数**换值**（`"abc"`→320、`0`→1、`99999999`→2000，于是 `inputSchema` 自己声明的
`minimum`/`maximum` 成了假告示），库面最深的两道域闸抛的是 `ParseError`（对外 `E3040 / 502 /
retryable=True`，把调用方写错的数字登记成上游故障并建议换主机重试），只有 HTTP 一直是对的。
现在六个入口对同一件事只有一种回答：

* **形状不合或越界 = 调用方的错**：`ValidationError` ⇒ 错误码 `E1010`、HTTP `422`、JSON-RPC
  `-32602`（MCP 同）、`retryable=False`。既不替换成别的数，也不报成内部错误。
* 真整数原样通过；纯数字串按整数解析（查询串与 JSON 都天然承载字符串）；`bool` / `float` /
  列表 / 非数字串一律拒——注意 `count=1.5` 现在是**拒**，不是截断。
* 唯一实现是 `tstdx/integration/wire_fields.py::as_request_int`；两张机器面不再自己转换
  （判据用 AST 扫源码钉住这一点）。库面那侧是 `tstdx/client/core.py::_require_int`，
  绑定处 `QuerySpec.normalized` 前另有真整数闸，其字段名单从 `dataclass` 的 `int` 注解现扫。

各面的**边界仍然不同**，这是有意的，而且从本轮起每一张都真的按自己的声明执行（下表与代码现值
同源，逐格量自 `route.dependant` 与 `inputSchema`）：

| 面 | 整数的边界来自 | 缺席 / `null` | 坏形状、越界的回答 |
|---|---|---|---|
| 库面 `Client.bars(...)` | `0xFFFF` 单值 + `start + count ≤ 0x10000`（16-bit 分页地址空间，协议事实） | 各形参自己的 Python 缺省 | `E1010`（不可重试） |
| CLI `--count/--start` | `argparse type=int`，非整数在门口 `rc=2` | 旗标缺省（如 `--count 320`） | `E1010` + 退出码 2 |
| HTTP 查询串 | FastAPI `Query(ge/le)`：`/v13/bars/{symbol}` 的 `count` 是 1..10000（缺省 320）、`start ≥ 0`；`/v13/trades/{symbol}` 的 `start`/`count` 只设 `≥ 0`；`/v13/security/list` 的 `start ≥ 0` | 查询串里没有 `null` 这个 token：`?count=` 空串与非数字串同样拒 | `422 / E1010`，且在 handler 之前 |
| WS JSON-RPC `params` | 线层只治形状（`_int_param` 不设区间），区间交给上面的内核域闸 | 缺席与显式 `null` 都用声明缺省（`bars.count` 320、`start` 0、`trades.count` 0） | `-32602 / E1010` |
| MCP `arguments` | **该工具自己的 `inputSchema`**：`get_bars.count` 1..2000（缺省 320）、`get_bars.start` 0..10000、`get_trades.start` 0..10000、`get_trades.count` 0..2000、`get_security_list.start` 0..10000 | 同 WS：`default` 即声明值 | `-32602 / E1010` |

MCP 的 `count` 上限（2000）比 HTTP（10000）小，是给 LLM 上下文预算留的；两个数现在都是
各自声明的真边界，不再由一份抄件决定。**协议事实仍是协议错误**，不因本轮归位：离线指令、
未核验市场、脏字节、`split_symbol` 的 BJ 边界保留 `E2xxx`/`E3xxx`——那条分界线写在
`tstdx/client/core.py::_require_int` 的 docstring 里。判据见
`tests/architecture/test_wire_numeric_domain.py`（8 项：AST 禁自己转换、MCP 按 schema 边界拒并含
"改 schema 数字断言跟着翻"的正控、HTTP 越界在 handler 前 422、内核整数格全表、分页上限四面同口径）。

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

**三份服务面的 `Client` 所有权是同一口径**（第 26 轮补；在那之前 HTTP 面只是 `return app`，
自己造的那份内核没有任何收尾入口）：

| 面 | 造出 `Client` 的一方 | 收尾的地方 |
|----|--------------------|-----------|
| HTTP `create_runtime_app(client=None)` | 不传时工厂按 `owns_client` 自己造一份 | `_lifespan` 把 `yield` 包进 `try`，在 `finally` 里按 `owns_client` 调 `api.close()`——写在 `yield` 之后不算收尾，生命周期体一抛错那两行就永远走不到（第 31 轮 31-C；判据 `tests/runtime/test_runtime_http_client_release.py`）|
| WS `serve_runtime_ws(handler=None)` | 不传时函数按 `owns_handler` 自己造，登记在 `server.tstdx_handler` | 宿主的 `finally` 调 `RuntimeJsonRpcHandler.close()`；`__main__` 入口即如此 |
| MCP `MCPServer(client=None)` | 不传时构造器自己造（`_owns_client`）| `stop()` 幂等：`_stopped` 事件 + 关掉后置回 `_owns_client`，`shutdown` 与 `serve` 收尾抢着停也只关一次 |

三处都遵守同一句话：**传进来的那份归调用方，本库不关**；只有自己造的那份才由自己收尾。
这条收尾在默认内核上目前**不释放任何 socket 或线程**（§2「`close()` 到底释放什么」量过：
`Client` 跨调用不持有连接，内置执行器连 `close()` 都没有），它兑现的是所有权协议与注入执行器
的口子——第 31 轮之前本节把这条链写成"释放连接池"，那是一句没人量过的过头主张。

`/v13/capabilities` 交付两份纯名字列表，没有状态字段：口径与那些发不出去的名字见
§2「能力发现面：只有名字，没有可用性」。

### WebSocket JSON-RPC（13 方法）

```python
from tstdx.integration.runtime_ws_server import serve_runtime_ws
```

方法：`quotes`、`bars`、`snapshot`、`minute`、`trades`、`security.count`、`security.list`、
`query`、`runtime.capabilities`、`runtime.health`、`subscribe`、`unsubscribe`、`list`。

监听地址与路径由 `RuntimeWsConfig` 决定，默认 127.0.0.1:8765 上的 `/v13/ws`；连到别的路径
会被以 1008 状态码关闭（reason 为 "unsupported path"）。`serve_runtime_ws` 是协程，返回 websockets
库的 server 对象；要一条命令拉起来就用它的包入口 `python -m tstdx.integration.runtime_ws_server`
（第 23 轮补：在那之前这条命令只会静默导入后退出，什么都不监听）。它仍然没有 tstdx 子命令
（§3 CLI 表那一格里没有它）。

**并发与所有权**（第 26 轮补，两条都是宿主必须知道的）：

* 处理方法跑在**工作线程**上。`handle_message` 是同步函数（里面是真实 socket 取数 +
  `json.dumps()`），服务现在通过 `asyncio.to_thread` 调它，所以一个慢请求不会占住事件循环、
  拖死同一进程里其他连接；同一连接上的请求仍按到达顺序逐条应答。宿主自己实现连接循环时
  要付同样的代价，直接把 `handle_message` 在协程里调用就是退回那个形状。
* 谁负责收尾由**是否传入 handler** 决定。**传入**的那份归调用方，`serve_runtime_ws` 不碰它的
  生命周期；**不传**时函数自己造一份，并把它登记在返回对象的 `server.tstdx_handler` 上。那条
  `Client` 由 `RuntimeJsonRpcHandler.close()` 收尾，宿主停机时不调它就把这条所有权链断在
  自己手里（`python -m tstdx.integration.runtime_ws_server` 这个入口就是在 finally
  收尾块里做了这件事的）。它今天释放的是所有权而非连接池，见 §2「`close()` 到底释放什么」。

`runtime.capabilities` 与 HTTP 同一份两份纯名字列表，同样**只有名字、没有可用性**
（§2「能力发现面：只有名字，没有可用性」）。`query` 是泛型查询入口
（`{"method":"query","params":{"capability":..., "args":[...], "kwargs":{...}}}`），与 HTTP
`POST /v13/query/{capability}` 同义。`subscribe` / `unsubscribe` / `list` 不是 capability 投影，
而是实时订阅控制面，见下一节「WebSocket 实时订阅」。

### WebSocket 实时订阅（subscribe / unsubscribe / list + push）

第 31 轮补齐：WS 面此前只有 req/res，实时行情只能靠客户端轮询。现在本连接可以订阅一条
进程内实时流，由服务端主动推送 `snapshot` / `tick` / `error` 帧，不用轮询。订阅表**每条连接私有**
（`serve_runtime_ws` 为每条连接造一份 handler），两条连接不会串台；连接断开时本连接拥有的流被
`stop_all_subscriptions` 收掉，不泄漏。

控制方法（白名单字段见 `tstdx.integration.wire_fields.WS_PARAMS_FIELDS`）：

* `subscribe`：`{"symbols":[...], "provider":"tdx", "interval":1, "diff_only":false, "max_queue":1024}`。
  回 `{"subscription_id":"subN","status":"subscribed"}`；随后连接收到 `push` 帧：
  `{"method":"push","params":{"type":"snapshot","code":...,"data":{...}}}`。未声明字段当场 `-32602`。
* `unsubscribe`：`{"id":"subN"}` → `{"status":"unsubscribed","id":"subN","found":true}`；停掉对应流。
* `list`：`{}` → `{"subscriptions":[{"id":"subN","state":"running"}]}`，只读本连接的订阅与状态。

推送帧的 `params.type` 取值：`snapshot`（首帧/每次轮询快照）、`tick`（增量，需 `diff_only:true`）、
`error`（`params.error` 为规范化错误信封）。订阅/查询共用同一套错误信封挂在 `error.data`，
客户端读法与其它三面一致。

```python
import asyncio, json
from websockets.asyncio.client import connect
from tstdx.integration.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

async def main() -> None:
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=8765))
    try:
        async with connect("ws://127.0.0.1:8765/v13/ws") as ws:
            await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"subscribe",
                "params":{"symbols":["600519"],"provider":"tdx","interval":1}}))
            ack = json.loads(await ws.recv())
            assert ack["result"]["status"] == "subscribed"
            # 推送帧没有 id，是通知而非请求/响应。
            frame = json.loads(await ws.recv())
            assert frame["method"] == "push" and frame["params"]["type"] == "snapshot"
    finally:
        server.close()
        await server.wait_closed()

asyncio.run(main())
```

### MCP stdio（9 工具）

```python
from tstdx.integration.mcp import create_mcp_server, TOOLS
```

工具：`query_capability`（通用入口）+ `get_bars`、`get_quote`、`get_quotes`、
`get_snapshot`、`get_minute_today`、`get_trades`、`get_security_count`、
`get_security_list`。

拉起方式是包入口（第 23 轮补；在那之前 `python -m tstdx.integration.mcp` 当场
`No module named ...mcp.__main__`，能跑的只有私有模块 `...mcp._server`，而文档一处都没写）：

```bash
python -m tstdx.integration.mcp
```

MCP 客户端的配置就按这一条拼：`{"command": "python", "args": ["-m", "tstdx.integration.mcp"]}`。
这张面**不需要任何 extra**——`tstdx/integration/mcp/` 只 import 标准库（见 README「Optional
Extras」那一节里被删掉的 `mcp` 假 extra）。它在 stdin/stdout 上说 JSON-RPC：`initialize` →
初始化通知 → `tools/list` → `tools/call`，未声明的入参回 `-32602`，不认识的方法名回 `-32601`。
这四格加两条反向口径在装好的包上逐条量过（`scratch_v18b22/probe23/mcp_ship23.log`，7 项全过）：
9 项工具全在 `tools/list` 里、`get_quote` 真回数据、塞进未声明的 `nope` 得 `-32602`、调不存在的
工具名得的是 `isError=True` 的 **result**（MCP 把工具级失败放在 result 里，不塌成 JSON-RPC 错误）、
`bogus/method` 得 `-32601`，而**不带 id 的通知不配得到任何回应**——这条最容易写反：把初始化通知当
请求发（带 id）就会真的收到 `-32601`，那不是缺陷。

### CLI（31 子命令 / 36 个叶子命令）

```bash
tstdx --help
```

下表是 CLI 的**接口面本身**，不是一句"见 `--help`"：命令名、位置参数、旗标集合与落点全部
由 `tests/architecture/test_cli_reference_table.py` 从 `argparse` 现值与处理器源码重新派生，
逐格核对。改一个旗标、加一支命令、或把一支命令从内核挪到直连传输层而不改这张表，门禁就红。

**落点**只有七种，含义是"这条命令的数据从哪儿来"：

| 落点 | 含义 |
|------|------|
| 内核·typed | `Client` 的类型化方法或 `client.call`，stdout 打 `serialize_result` 信封（带 `provenance`/`currentness`） |
| 内核·rows | 同一内核，经 `tstdx/cli/runtime_commands.py::_ClientRows` 把信封拆成裸行；`--json` 决定行数组还是人读表格 |
| 直连传输层 | 不经内核，经 `tstdx.cli._common.family_client` → `get_client(...)`——协议诊断面，不是第二套能力执行路径 |
| 服务面宿主 | 拉起 HTTP 应用本身 |
| 传输·诊断 | 主站解析与测速（`tstdx.transport.*` / `tstdx.tools.host_audit`） |
| 反馈 | `tstdx.feedback`，不发行情请求 |
| 元信息 | 只打印随包发布的静态信息（包版本、`Client.capabilities()` 那份能力名清单），不构造 `Client`、不发请求也不建连接 |

| 命令 | 位置参数 | 旗标 | 落点 |
|------|----------|------|------|
| `adjusted-bars` | `symbol` | `--method` `--period` `--count` `--timeout` `--json` | 内核·rows |
| `all-market` | — | `--node` `--source` `--page-size` `--max-pages` `--timeout` `--json` | 内核·rows |
| `baidu` | `symbol` | `--kind` `--period` `--count` `--end-time` `--limit` `--timeout` `--json` | 内核·rows |
| `bars` | `symbol` | `--provider` `--fallback` `--host` `--period` `--count` `--start` `--adjustment` | 内核·typed |
| `blocks` | `block_type` | `--count` `--timeout` `--json` | 直连传输层 |
| `capabilities` | — | — | 元信息 |
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

下面那 37 行是上表 36 支叶子命令各一条可直接照抄的示例（`f10` 有目录与正文两条）：示例里的每一个
参数都会被真实 parser 解析（`test_every_documented_cli_example_parses`），
`test_every_leaf_command_has_an_example` 再要求 36 支叶子一支不缺。

```bash
tstdx version                                              # 包版本
tstdx capabilities                                         # 内核能力名清单（只有名字，无可用性）
tstdx query stock_changes --args '[[8201]]' --kwargs '{"size": 5}'   # 任意能力的通用入口
tstdx quotes sh600519 sz000001                            # 实时行情
tstdx bars sh600519 --period day --count 80               # 日 K 线
tstdx snapshot sh600519                                    # 规范快照
tstdx minute sh600519 --provider tencent                     # 今日分时（tdx 面已下线，见下表）
tstdx trades sh600519 --provider tencent                     # 逐笔成交（同上；--count 在 Web 源不服务）
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
tstdx blocks 1 --count 50                                   # 板块行情：不服务（0x07E5 多主站无响应，客户端 fail-fast）
tstdx goods AU2412 --kind quote                             # 商品行情：不服务（符号语法只认 5–6 位数字代码）
tstdx f10 sh600519                                          # F10 栏目目录
tstdx f10 sh600519 --file 公司概况                          # F10 正文下载并解析
tstdx list 0 --count 100                                    # 代码表（直连传输层）
tstdx quotes-snapshot sh600519 sz000001 --json              # 批量快照（直连传输层）
```

### 36 支叶子、37 行示例的真机口径（第 23 轮装好的包，第 25 轮同面重跑过一遍）

跑法先说清楚，因为它自己就是一只坑。清单不是手抄的，是本轮的普查脚本从上面那段围栏里**现读**的
（取"命令行数最多"那个围栏块，绕开 §3 的 `tstdx --help` 与 §7 的巡检示例；脚本与逐行日志都在
`scratch_v18b22` 下面，完整路径记在方案台账第 23 轮），一行一条进程打在装进独立环境的控制台脚本
上——不是仓库源码树。跳过条件**只精确匹配 `serve` 这一支子命令**：上一版普查
（`scratch_v18b22/cli_face23.log`）写的是"整行以 `tstdx serve` 开头就跳过"，
于是 `tstdx server-test` 跟着长驻服务一起被吞——一行从没跑过的示例被记进"每一行都跑过"，那份旧日志
里 `server-test` 一次都没出现（它只在 PHASE A 的 `--help` 里露过面）。

37 行 = 36 支叶子命令（`f10` 有两条示例）；`serve` 长驻，由本节末那一段单独量，
所以真跑的是 36 行。装好包那遍（22:08–22:20，`scratch_v18b22/probe23/census_ship23.log`）读数是
**21 行当场出数据 / 15 行按下表口径失败 / 0 行 traceback**；改前那遍（21:01，同一份围栏）是
20 出数据 / 15 失败。两遍差的三行：`query`（按修好的加引号写法）与 `all-market` 进入出数据行列、
`server-test` 本轮第一次真跑；同一次还有两行从出数据滑进失败——`minute-klines`（`E7000`，东财
`push2delay` 断连）与 `baidu`（`E7010`，403 疑似反爬），改前它们各给 51/43 行，两遍隔着 67 分钟、
本库判据没碰过，按上游可用性抖动记（下表最后两行），不替上游圆场。失败到底是"环境/上游"还是
"本库不服务"，逐条记在这里——读的人不必自己踩。取证日志与逐条 rc 记在
``docs/REFACTOR_PLAN_V18_RESTRUCTURE.md`` 第 23 轮的执行记录里。

| 命令 | 装好包那次真机结果 | 口径 |
|------|----------|------|
| `probe 0x052D …` | rc=1，171.5s，12 行完整探测报告；`E2030`×8 读超时收口成 `E2040` 所有主站不可达 | **是缺陷并已修**：`--archive-dir` 不给时把库默认值覆盖成 `None`，`Prober` 无条件 `Path(None)` 当场 `TypeError`。这支命令的默认用法从未通过。那 171.5 秒的构成同 `quotes-snapshot`（8 台 × 读超时 + 梯子 sleep，G38 已修） |
| `blocks 1` | rc=2，`E3035`（改前是 rc=2 `E3040 block_type 必须是整数`） | 半修：`type=int` 补上后才会走到真判据——`0x07E5` 多主站实测无响应，客户端 fail-fast，**本库不发板块行情** |
| `goods AU2412` | rc=2，`E4040 无法解析证券代码` | **不服务**：符号语法（`tstdx/domain/symbol.py::SYMBOL_PATTERN`）只认 5–6 位数字，任何真实商品代码都进不去；商品族没有 live golden，不扩语法去猜字节 |
| `minute` / `trades` | rc=2，`E9010` | **tdx 面不服务**：`0x0537` / `0x0FC5` 是 inferred 命令，真实记录布局仍待 golden。出路是显式换 Web 源（第 30 轮接通并实测）：`tstdx minute 000001 --provider tencent` 与 `tstdx trades 000001 --provider tencent` 均 rc=0 出数据；`--provider baidu` 撞 `E7010`（上游 403 反爬）、`--provider eastmoney` 撞 `E7000`（上游断连）——链路通、上游可用性另计。给 `trades` 写 `--count` 在 Web 源上是 `E1010`，因为那一格没有落脚点（见本文 §2 的 `Client` 方法表中 `trades` 一行的 caveat） |
| `security-list` / `list` | rc=2，`E3035` | **不服务**：`0x044D` 已下线；要一张带代码的清单改用 `tstdx all-market`（Web 侧全市场快照）|
| `f10 sh600519`（目录） | rc=2，159.0s，`E2030`→`E2040` | **不服务**：远端已停止 F10 内容分发（2026-09 实测）。那 159 秒原先记作"慢在主站"，第 25 轮按时间戳改判：**约 121 秒是重试梯子的 sleep**（见本节末 `--timeout` 那段，已修） |
| `f10 … --file 公司概况` | rc=2，`E4000` 下载结果为空 | 同上，那一格的判据是显式写的"远端已停止 F10 内容分发" |
| `adjusted-bars` | rc=2，`E1010 … requires vipdoc_root` | 前置条件：复权要有本地 vipdoc 原始 K 线，指到 `vipdoc_root` 才有数据 |
| `fund estimate` | rc=2，`E7030` | 上游已下线（`fundgz` 返回 404）；改取 `fund nav` 历史净值 |
| `sector-flow` | rc=2，`E7000 Server disconnected` | 上游可用性：东财端当晚断连，本库判据正确（不假装成功） |
| `minute-klines` | rc=2，`E7000`（同一支东财端点；改前给 51 行） | 上游可用性抖动，跨两遍普查的唯一两处读数翻转之一 |
| `baidu` | rc=2，`E7010` 403 疑似反爬（改前给 43 行） | 同上：本库把 403 译成可读的 `E7010` 而不是回空表 |
| `feedback submit` | rc=1，"默认禁用" | 需要 `TSTDX_FEEDBACK=1`（或 `dry-run`），设计如此 |

另有两条改前失败、本轮出数据的，单独记（它们不在上面那 15 行里）：`query stock_changes --args …`
给 88 行（文档示例原先**没加引号**，粘进 shell 会被拆成两个词、当场 exit 2；现已整体加引号，并被门禁
按 shell 口径解析）、`all-market --source sina` 给 83 行（改前 rc=2，`E1010 capability 'all_market'
参数不符合 v13 contract`——`--source` 被当成 capability 入参递下去，而 `WebQuoteSession.all_market`
没有这个参数；它选的其实是 Provider）。

本轮出数据的 21 行：`version`、`capabilities`、`query`、`quotes`、`bars`、`snapshot`、
`security-count`、`stream`、`hosts audit`、`hosts list`、`hosts scan`、`server-test`、
`feedback stats`、`changes`、`hot`、`margin`、`all-market`、`fund nav`、`fund list`、
`index constituents`、`quotes-snapshot`。其中三条慢的是主站而不是循环：`stream --seconds 30`
30.4s（它按 `--seconds` 跑满就停）、`quotes-snapshot` 181.0s、`hosts audit` 9.9s——8 个主站逐个试到
有一个应答为止。第 21 轮把这类循环的终止条件逐处量过，本轮把它们的时间花名册记在
`scratch_v18b22/probe23/census_ship23.log` 每行的秒数列。

**第 25 轮在同一份围栏、装好的包上又跑了两遍**，两遍的读数都是 **22 行出数据 / 14 行按上表口径可读失败 /
0 行 traceback**，37 行逐行判类一行不差（`scratch_v18b25/probe25/face25_ship25e.log` 是最终那份安装包
`dist25_pass3c` 的读数，`face25_ship25d.log` 是它之前一次构建 `dist25_pass3b` 的；逐行 rc 与秒数在行首）。
相对第 23 轮
有三格变化，逐条说清是谁动的：`minute-klines` 与 `baidu` 从失败滑回出数据——这两行的判据本库一个字
没改，是上游端点当晚可达（它们在第 23 轮那遍各给 51/43 行的读数，形状相同）；`quotes-snapshot`
与 `f10` 两行的秒数从 181.0s / 159.0s 降到本轮能跑完的量级，这一格是**本库动的**——G38 那把没上界的
退避梯子（见下面 `--timeout` 那段）。

这一遍也先把普查脚本自己坑了一次：`scratch_v18b25/probe25/face25_ship_run.log` 那遍报"36 行全部
rc=2、零 traceback"，看起来像"全部失败但都可读"，实际是探针把行尾的中文注释连同 `#` 一起当成参数
喂给了进程——一次注释被当成入参的读数不属于任何接口面。改成按 shell 口径切词（带 `comments=True`）
之后才是上面那份 22/14/0。作废的那份日志**没有删**，它现在是尺子缺陷的证据。同一遍还发现 WebSocket
那一格连的是别的进程起在 8765 上的服务（探针自己起的那个当场因端口占用退出），所以本轮把它换到空闲
端口重跑并让服务端自己打印包落点（当时那支脚本的读数在 `scratch_v18b25/probe25/ws25_port.log`）；
这条钉法现在装进了探针本身——上面那两遍的 WS 格都是自己拉起的服务在 OS 分配的空闲端口上应答，
服务端加载的包落点印在同一份日志里。

**上面那句"慢的是主站而不是循环"有一处是错的**，第 25 轮同一形状重跑时量到：`quotes-snapshot`
那 181 秒（本轮复测 158 秒）里绝大部分是重试梯子自己的 `time.sleep`，不是主站响应慢——详见本节末
`--timeout` 那段与台账 G38。`stream` 与 `hosts audit` 两行的归因不受影响（前者按 `--seconds` 跑满，
后者是并发测速）。

`feedback submit` / `fund estimate` / `index constituents` 的 `--timeout` 现在两种位置都吃：
`tstdx fund estimate 000001 --timeout 5` 与 `tstdx fund --timeout 5 estimate 000001` 等价
（第 22 轮 G26 之前只有后一种能解析，前一种当场 exit 2）。

`--timeout` 约束的是**单次尝试**的套接字超时，不是整条命令的墙钟：一次请求失败时连接池会换主站
重拨，最坏情况把池子里每台都拨一遍。上一段那句"慢的是主站而不是循环"在第 25 轮被自己的时间戳
推翻——8 台主站那次 159.4 秒里有 **121.143 秒是纯 `time.sleep`**（同一份日志里七条
`退避 …s` 逐条相加；退避梯子按 `2**已试主站数` 放大，`attempt` 在换主站时也增长），
而批量帧放弃之后回退路径首台 131 毫秒就取回了数据。本轮把它改成
两条口径：退避只兑现给"回到刚失败过的那台"，且单步封顶 8 秒（`tstdx/transport/pool.py` 的
`MAX_RETRY_BACKOFF_SECONDS` 与 `retry_backoff_delay`，同步与异步池共用同一处声明）。同一台机器、
同一份围栏、同一条 `quotes-snapshot --timeout 5` 的源码树对照：改前 158.0 秒 / 改后 38.1 秒
（`scratch_v18b25/probe25/timing25_snapshot.log` 与 `timing25_snapshot_postfix.log`，逐次请求的
毫秒时间戳在每行行首）。判据 `tests/transport/test_retry_backoff_cap.py`（30 项；两条变异各自
当场红：去掉封顶 16 红、把 sleep 装回每次换主站 1 红）。**因此这条命令的墙钟上界是
`主站数 × timeout`**——要压它就把 `--timeout` 调小，或先用 `tstdx hosts scan` 把可达主站排到前面。

那句"上界 = 主站数 × timeout"在第 31 轮之前只是**声明**：同步传输的 `timeout` 量的是单次
recv 的**空档**，每收到一个字节就重新武装，于是逐字节吐数据的对端永远撞不到它——而一帧要读
多少字节是**对端**在响应头里写的（`zip_size`，单帧上限 32768）。现在一次读帧按墙钟算：进循环
前算一次截止，每轮按剩余预算收紧 socket 超时，见底即 `ReadTimeout`，读满或抛错后把 socket
超时还回声明值（连接接下来还要复用）——与异步孪生同强，判据
`tests/transport/test_recv_wall_clock_deadline.py`（三条变异：拆掉收紧、拆掉抛错前的复原、
拆掉读满后的复原，各自当场红）。

`serve` 那一行的读法与读数：用装好的包在回环上起一份服务，按 11 个请求逐条打（上面 10 支业务路由
各一次，外加一支带未声明字段 `_=170` 的 `/v13/quotes`、一次带多余 key 的 POST），11/11 有应答、
零次超时，每条请求后再打一次健康路由都是 200、进程始终活着
（`scratch_v18b22/probe23/http_ship23.log` 第 1~21 行，逐条状态码与耗时都在行首）。三支回 **501**：
`/v13/minute/{symbol}` 与 `/v13/trades/{symbol}` 是 `E9010`、`/v13/security/list` 是 `E3035`——与上面
CLI 那三行同一判据、同一形状（发不出去就明说不发，不给空表）；两支 422 是未声明入参的 `E1010` 口径。
同一张面在本轮更早那遍（21:07，`scratch_v18b22/http_isolated23.log`）曾量到健康路由之外连排 8 次
`ReadTimeout`——那一遍同时还有另一套外部探针在打主站池；本轮把两套改成串行后 11/11 干净，所以那 8 次
记为**并发污染**而不是网关卡死，判据本身没有为此改动。

`serve` 只承载上面那张 HTTP 表（10 路由），**不承载 WebSocket**：`create_runtime_app()` 里没有任何
`websocket` 路由，JSON-RPC 面要另外跑 `python -m tstdx.integration.runtime_ws_server`（或在自己的
程序里 `await serve_runtime_ws()`；`tstdx/integration/runtime_ws_server.py`，默认 `127.0.0.1:8765`、
路径 `/v13/ws`，见 `RuntimeWsConfig`）。二者不是同一个端口上的两个协议。两张面都是**同步取数、当场
应答**：服务面不驻留后台任务，也没有任务句柄可查——要并发就在调用方自己起了算。

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

### 本地文件读取面（vipdoc 落地文件）

网络协议之外，`tstdx` 还直接读通达信客户端的落地文件。这一族挂在包的顶层出口上，下表第一列
**不是手抄名单**：它是 `tstdx._LAZY`（顶层惰性导入表）里落在 `tstdx.reader` 的那些名字，判据
`test_reader_surface_table_matches_the_lazy_map` 把它与代码锁死，`test_every_root_export_is_named`
再反向保证顶层 45 个名字没有一个在用户文档里查不到（第 25 轮第 2 遍登记：当时 `FinanceReader`
是唯一一个"包上挂着、测试在用、文档一个字没提"的名字）。

| 顶层名 | 读什么 | 典型入口 |
|---|---|---|
| `DayBarReader` | `.day` 日线，32 字节/条 | `read(path, output=...)` |
| `MinBarReader` | `.lc1` / `.lc5` 分钟线，32 字节/条，`interval=` 决定换算 | `read(path, output=...)` |
| `BlockReader` | `block_*.dat` 板块文件（概念/指数/风格），`flat` 与嵌套两种模式 | `read(path, output=...)`，分组结构在构造处给 `group=True` |
| `FinanceReader` | `gpcw*.dat` 财务数据，float32 扁平序列，每记录字段数随版本而异 | `read_indicators(path)` |
| `DataProfile` | 一份数据规格档案：影响数值正确性的差异在此显式声明 | 由 `detect_profile` / `get_profile` 产出 |

`read_day_file`、`read_min_file`、`resolve_vipdoc_path`、`Period`、`Market` 等名字只在
`tstdx.reader` 模块面上，不经顶层导出（顶层这一族就上面 5 个）。档案词表本身的门禁见
`tests/architecture/test_profile_vocabulary_gates.py`（第 17 轮 G12：一张没人查的词表比没有更坏）。

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

CLI 那一支的订阅窗口是 `--seconds`，**默认 10 秒**：起流后跑满这个时长就自停（要中途停按
Ctrl+C，`stop()` 一定执行）。给 0 或负数在 CLI 门口就判死（`E1010` / 退出码 2）——过去它的
默认值是 `0.0`，于是文档里 `tstdx stream sh600519` 这种照抄写法必然以"0.0s 内未收到任何行情"
收场，窗口长度本身就没给过任何数据机会（第 24 轮 G33）。

`interval` 只挡下界（`validate_subscription` 判 `<= 0`），**没有上界**，所以异步流的
`stop()` 不能靠"等这一次睡眠走完"收口：`AsyncQuoteStream` 的四处睡眠腿都按
`_STOP_POLL_SECONDS`（0.05 秒）轮询停机位，`stop()` 的上界因此是"在飞的那一次取数 + 0.05 秒"，
与 `interval` 无关。第 31 轮之前写成裸 `asyncio.sleep`，`interval=3600` 能把一次停机拖成
一个小时（判据 `tests/streaming/test_async_stop_wake.py`，同步孪生核对同一张睡眠腿表）。

### StatefulQuoteStream / AsyncStatefulQuoteStream（生命周期对象）

```python
from tstdx.streaming import AsyncStatefulQuoteStream, StatefulQuoteStream
```

`Client.stream` / `AsyncClient.stream` 返回的对象，轮询走调用方的 `UnifiedRuntime`；
`state` 与 `failure_reason` 给出显式生命周期状态（`StreamState`）。

两张面都支持上下文写法，进出各对应一次 `start()` / `stop()`：同步
`with client.stream(...) as s:`，异步 `async with aclient.stream(...) as s:`。异步那半边
是第 31 轮补上的——`AsyncQuoteStream` 此前没有 `__aenter__`/`__aexit__`，而同步孪生一直有，
于是"同名异步镜像"在起止停这一格不同答案（判据
`tests/streaming/test_stream_context_parity.py`，含块体抛错时照样收尾那一格）。块体抛错
不会跳过收尾。

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

### 交易日历（TradingCalendar）

内置表只有三个年度：`CALENDAR_2024` / `CALENDAR_2025` / `CALENDAR_2026`（合起来就是
`BUILTIN_CALENDARS`，键即年份）。`get_calendar()` 给全年的共享单例，
`TradingCalendar(years=[2025])` 给自选年度的私有实例。

```python
from tstdx.domain.calendar import get_calendar

cal = get_calendar()
cal.is_trading_day("2026-10-01")                       # False
cal.next_trading_day("2026-10-01")                     # datetime.date(2026, 10, 9)
cal.count_trading_days("2026-09-28", "2026-10-09")    # 4（含两端）
cal.trading_days_between("2026-09-28", "2026-10-09")  # 同一区间的日期列表
```

`count_trading_days` 与 `trading_days_between` 是同一件事的两种出口（一个给计数、一个给日期），
`start > end` 一律抛 `CalendarError` 而不是返回 0 或空表。三条读表口径：

- **没有在线校准**。日历数据是仓库内置的静态表，本库不提供任何联网取数的入口。第 26 轮 F-110
  删掉了曾挂在 `TradingCalendar` 上的 `update_from_web()`：它对每个调用方都只抛错，而 docstring
  指向的"在线日历适配器"在本仓从未存在过。改数据只有 `set_holidays(year, days)` 与
  `add_holiday("YYYY-MM-DD")` 两条路，二者只改内存里的那个实例，进程重启即回落到内置表。
- **估算年份必须读得出来**。2024/2025 是实采表，2026 是估算表（`ESTIMATED_YEARS` 现值即
  `{2026}`）。用 `is_estimated(d)` 判、用 `warnings_for(d)` 取那句校准提示；`set_holidays`
  覆盖过的年份即视为权威、`warnings_for` 随之返回空表。把日历当硬事实写进结算逻辑之前，
  这两格必须先看过——估算表里 2026 全年只有 242 个交易日，与实采的 2024 同数，这本身就是
  "估算表看起来很像真数据"的一个例子。
- **未覆盖的年份不报错**。表外的年份按"只有周末、没有节假日"处理并一次性告警，所以
  `next_trading_day` 一定终止；要把这条口径变成硬失败，自己在外面查 `is_estimated` 与年份。

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

### 未知命令探测（Prober）

CLI 的 `probe` 子命令（§3 表里那支直连传输层命令）背后是 `tstdx.protocol.prober.Prober`：单条命令
走 `probe_command`，一段区间走 `probe_range`，把结构分析结论落成 YAML 草稿走 `archive`。

```python
from tstdx.protocol.prober import Prober

prober = Prober(client, rate_limit=1.0, archive_dir="probe_out")
result = prober.probe_command(0x052D, market=0, code="sh600000")
result.ok            # False 时原因在 result.notes 里，不抛异常
prober.total_probes  # 真正发出去的探测次数
prober.total_failures  # 其中发送侧抛过异常的次数
```

两个计数器是只读属性（`total_probes` / `total_failures`），口径要在读表时记住：它们只在
`_enforce_rate()` 之后与发送异常处自增，因此**被盘中守卫拒掉的探测两个都不计数**（那种情况
在发送之前就返回失败结果），dry-run（构造时不给 `client`）只计 `total_probes`。于是
`total_failures <= total_probes <= probe_command 的调用次数`，两个都不等于调用次数。第 26 轮
F-116 把这条口径写在这里：这两个名字此前用户文档一个字没提，`probe` 那支命令的报告也不打印它们，
仓内唯一的读者是 `tests/protocol/test_f2_protocol_correctness.py` 里"未发出任何请求 ⇒
`total_probes == 0`"那一条断言——它们是给人自己接监控用的库面计数，不是 CLI 输出的一部分。

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

## 8. 可观测性与反馈（Observability / Feedback）

**这一族只有库面，而且是部署方自选的接入面**：CLI、HTTP 网关、WS、MCP 四张面都不挂载导出器，
`tstdx serve` 起来之后不会有人来抓 `/metrics`，指标写入只发生在库内部那几处埋点。第 26 轮
F-115 把整族写进接口文档，是因为四个导出器与指标门面此前只出现在模块 docstring 和 FAQ 里，
`docs/api/` 一个字没提——能被 import 却读不到说明的对外面，正是 G37 那一串裁决的靶子。

### 指标门面（`tstdx.observability.metrics`）

指标门面上的每一格都由模块级单例 `metrics` 持有，`render()` / `render_prometheus()` 出文本、
`snapshot()` 出 dict。下表第一列与注册表现值双向锁死
（`test_interfaces_metric_series_table_matches_the_registry`），第二列是类型与标签，第三列是
**当前真的在写它的位置**——第三列的存在才是本轮的产物：只有"有人写"的读数才是测量值。

| 序列 | 类型（标签） | 写入方 |
|---|---|---|
| `tstdx_protocol_parse_total` | counter（tier / family / command） | 三层解析分派出口 `tstdx/protocol/registry.py`（F-118 接上，此前恒为空序列） |
| `tstdx_protocol_parse_confidence` | histogram（family / command） | 同上，只记 L1/L2 两档 |
| `tstdx_request_total` | counter（command / status） | 传输层两条路径（同步池 + 异步客户端） |
| `tstdx_request_duration_seconds` | histogram（command） | 同上 |
| `tstdx_stream_events_total` | counter（kind） | 流式背压丢弃钩子 `tstdx/streaming/engine.py` |
| `tstdx_stream_reconnects_total` | counter | 断线恢复那一轮 tick（F-117 接上） |
| `tstdx_stream_backpressure` | gauge | 同上，取丢弃时的队列长度 |
| `tstdx_errors_total` | counter（error_type） | 传输层两条路径的异常出口 |

便捷函数 `record_parse` / `record_request` / `record_stream_event` / `record_reconnect` /
`record_error` 与 `set_backpressure` 都写到这个单例上，全部**吞掉自身异常**：指标失败绝不许把
一次成功的请求变成失败。`instrument_client(client)` 给调用方自己的客户端套一层上报壳，
不需要改业务代码。

F-112 撤下了一格：`tstdx_active_connections`。它曾被注册、被 `/metrics` 渲染（HELP/TYPE 两行
就是一次声称）、还被 statsd 的线格式示例当示范用过，可全仓没有任何连接生命周期事件写它。
一根恒为初值的对外仪表会把"运行时没有活连接"说成实测值，而池里此刻可能正挂着好几条——
按「宁跳不假绿」撤下仪表连同它的 setter。要这个数，得先在 transport 层补一条与 retire/drain
同刻度的连接钩子（两条池路径都要），而不是先注册再等人喂。

### 导出器

```python
from tstdx.observability import PrometheusExporter, StatsdExporter, OtelExporter, start_exporter
```

| 导出器 | 公开出口 | 落点与口径 |
|---|---|---|
| `PrometheusExporter` | `render()`、`write_to_file(path)`、`serve(host="127.0.0.1", port=9090)`、`stop()` | 文本快照 / 文件快照 / HTTP 端点。`write_to_file` 写失败只记日志不抛；`serve()` 起 `ThreadingHTTPServer` 并阻塞，默认只监听回环，`0.0.0.0` 是显式 opt-in，鉴权与防火墙由部署方负责 |
| `StatsdExporter` | `push()`、`push_all()`、`flush()`、`start_pushing(interval=…)`、`stop_pushing()`、`close()` | UDP 逐指标推送。`start_pushing()` 起后台 daemon 线程，重复调用先停旧线程再起新的；`interval <= 0` 记一条 info 后**不启动**；`stop_pushing()` 幂等且线程安全 |
| `OtelExporter` | `export_metrics()`、`export_spans(spans=None)`、`send(payload, kind=…)`、`last_error` | 构造 OTLP JSON 并按 HTTP 上报。本库不产 span、也没有 OTel SDK 依赖，`export_spans()` 的输入是调用方自己的追踪数据；不给 `endpoint` 时 `send()` 返回 `{"error": …}` 而不是抛异常 |
| `start_exporter` | `(kind, metrics=None, **kwargs)` | 上面三类的构造糖，`kind` 取 `prom` / `prometheus` / `statsd` / `otel`，其余关键字参数原样透传给对应类 |

三个导出器都只**读**指标门面（`extra_headers=` 除外，那是给 `/metrics` 响应加头的旋钮），
反向接线一概没有：库不会替你起线程、不会替你落盘。线程与文件的生命周期归创建方，
这一条由 `tests/architecture/test_resource_lifecycle_gates.py` 的 G41 判据盯住
（`StatsdExporter.start_pushing` 在它的归属表里）。

### 反馈上报（`tstdx.feedback`）

| 名字 | 出口 | 口径 |
|---|---|---|
| `FeedbackReporter` | `report_error`、`report_usage`、`report_profile`、`enabled`、`dry_run` | 开关是环境变量而不是构造参数：`TSTDX_FEEDBACK=1` 才真发、取 dry-run 值才只构造不发送，两者都不是时三支 report 直接返回 `False`。上报前经过 7 步脱敏，`store_dir=` 可改成本地落盘 |
| `TelemetryCollector` | `enable`、`disable`、`record_event`、`flush`、`events`、`count`、`clear` | 独立的事件缓冲，opt-in：`enable()` 之后 `record_event` 才写入，`disable()` 之后的调用静默忽略。本库运行时不会自己 enable 它——它是给调用方攒自己那侧事件用的 |
| `UserStats` | `record_command`、`record_error`、`total_commands`、`total_errors`、`average_latency`、`latency_stddev`、`commands_per_second`、`errors_by_type`、`snapshot`、`reset` | 纯本地统计，不发网络 |

---

## 完整模块索引

详见 [API 参考索引](README.md)。
