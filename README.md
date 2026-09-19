# tstdx

> **TongDaXin Standard Data eXchange** — 通达信行情数据通用协议基础设施
>
> 类比 HTTP 世界的 `requests` 库：稳定、标准、可组合，专注协议层，不做应用层业务。

- 当前 Draft 开发版本：`1.0.0`
- 最新已发布稳定版：`v1.0.0`（2026-09-09 发布） · [发布说明](docs/releases/v1.0.0.md) · [CHANGELOG](CHANGELOG.md)

---

## 项目简介

### 是什么

tstdx 是通达信（TDX）行情数据的**通用底层协议基础设施**。它完整覆盖 TDX 的 5 套协议族，提供从原始二进制帧解析到统一查询内核（`Client`）的全链路数据接入能力。

### 解决什么问题

| 痛点 | tstdx 的解法 |
|---|---|
| TDX 协议封闭、逆向工程门槛高 | 85 命令账本 + 61 精确解析器 + 三级分派 + YAML 协议规范 |
| 单一数据源不可靠 | 11 个 Provider 注册表 + 172 capability 声明；**provider-first**：一次请求绑定一个 Provider，跨源只在显式 `FallbackPolicy` 下发生 |
| 数据来源不可追溯 | 每个结果携带 `Provenance`（provider/channel/capability/命令），溯源不符即抛，杜绝静默换源 |
| 数据请求被隐式缓存污染 | 执行路径**零缓存**：每次请求直达绑定 Provider |
| 同步/异步 API 分裂 | 签名镜像双客户端（`TdxClient` / `AsyncTdxClient`）+ 奇偶门禁 |
| 协议命令持续演进 | YAML 规范驱动 + codegen 自动生成 + golden_audit 三旗标防漂移 |
| 主站可用性差 | 主站池治理 + 三级路由降级 + 后台测速排序 + 社区候选注入 |

### 谁在用

量化交易开发者、金融数据工程师、行情中间件开发者、AI Agent（量化策略 / 选股 / 回测 / 因子计算）。

### 不是

回测引擎、选股系统、交易终端、Web 可视化平台 —— 专注协议层与数据接入层，向上可组合到任意应用。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                      服务面（可选部署，只翻译不执行）                   │
│  CLI 31 子命令 · HTTP REST 10 端点 · WebSocket JSON-RPC · MCP 9 工具   │
├─────────────────────────────────────────────────────────────────────┤
│                  Client / AsyncClient（唯一业务入口）                  │
│  15 便捷方法（bars/quotes/snapshot/minute/trades/stream/call…）        │
│  execute(QuerySpec) · typed(CapabilityQuery) · execute_with_policy    │
├─────────────────────────────────────────────────────────────────────┤
│              UnifiedRuntime（唯一执行内核，零缓存）                     │
│  QueryPlanner.compile → QueryPlan（单 Provider / 单 Channel）          │
│  DirectProviderExecutor：DIRECT_BINDINGS 251 条精确绑定                │
│  流式：StreamSpec/StreamPlanner → StatefulQuoteStream                 │
├─────────────────────────────────────────────────────────────────────┤
│              catalog/（静态声明与一致性审计，无执行）                   │
│  capability 目录 + 规划期签名校验（fail-closed）                        │
│  Provider channel→adapter 绑定表 · Provider 隔离契约/守卫/审计          │
├─────────────────────────────────────────────────────────────────────┤
│              providers/（11 Provider · 172 capability 唯一事实源）      │
│  tdx(85 命令) · tencent/sina/eastmoney/baidu/jsl/boc/iwencai(web 多源)│
│  local_vipdoc(reader 本地二进制) · builtin · derived(显式聚合)          │
├─────────────────────────────────────────────────────────────────────┤
│                      协议核心层                                        │
│  commands(85 账本) · registry(三级分派 + 异常收口) · parsers(61 × 5 族) │
│  codec(帧/原语) · transport(池/心跳/测速) · client/(TdxClient 同步异步)  │
├─────────────────────────────────────────────────────────────────────┤
│                      基础设施层                                        │
│  错误体系(E1-E9 九域) · 可观测性(Prometheus/StatsD/OTLP)               │
│  配置(6 源合并) · 安全(TLS/脱敏) · 输出(DataFrame/Parquet/DuckDB)       │
│  反馈(遥测/统计) · 工具链(capture/codegen/golden_audit/spec_audit)      │
└─────────────────────────────────────────────────────────────────────┘
```

**设计原则**：每层可独立使用，零强制上层依赖。底层协议库可用，上层内核可选。
**唯一执行路径**：请求只能沿 `Client → UnifiedRuntime → DirectProviderExecutor → Provider`
下行；不存在第二条内核、聚合降级路由或隐式缓存层（由 `tests/architecture/` 守卫锁定）。

---

## 核心特性

### 协议层

| 特性 | 说明 |
|---|---|
| **5 套协议族** | 7709 标准 / 7727 扩展市场 / MAC 专属 / F10 资料 / 商品语义 |
| **85 命令账本 · 61 精确解析器** | L1 精确 → L2 通用启发 → L3 原始透传 三级分派；解析器逃逸原生异常统一收口为 `ParseError` |
| **YAML 协议规范** | `PROTOCOL_SPEC/` 规范驱动 + `codegen` 自动生成 + `spec_audit` 双向漂移检查 |
| **协议探测** | `tstdx probe 0x052D` 探测未知命令的二进制帧结构 |

### 客户端层

| 特性 | 说明 |
|---|---|
| **同步/异步双 API** | `TdxClient` + `AsyncTdxClient`（签名镜像、奇偶门禁） |
| **多协议族客户端** | GoodsClient / ExMarketClient / MacClient / F10Client |
| **主站池治理** | `DEFAULT_HOST_POOL` / `POOL_BY_FAMILY` / `RankingStore` + 三级路由降级 + 社区候选注入 |

### 数据源层

| 特性 | 说明 |
|---|---|
| **HTTP Web 45+ 源类** | 东财/新浪/腾讯/集思录/港股/中行等，`httpx` / `urllib` 双栈，28 模块 |
| **本地 vipdoc 解析** | `reader/` 解析通达信本地 `.day` / `.min` / 板块 / 财务二进制文件 |
| **Provider 注册表** | `providers/` 声明 11 Provider × 172 capability × channel，是唯一事实源；`catalog/provider_bindings.py` 声明 channel→adapter 绑定 |
| **流式订阅** | `Client.stream` → `StatefulQuoteStream`（轮询基类 QuoteStream/AsyncQuoteStream；engine 内核：ReconnectPolicy + BackpressureQueue + DeltaMerger + GapFiller + StreamEngine） |

### 输出与服务层

| 特性 | 说明 |
|---|---|
| **3 Sink 策略** | DataFrame / Parquet / DuckDB（原子写） |
| **统一业务入口** | `Client` / `AsyncClient`（15 便捷方法 + `execute`/`typed`/`call` 通用面），永不隐式换源、永不缓存 |
| **HTTP REST 网关** | 10 端点（capability 白名单 + TaskStore 钳制），只翻译为 `Client` 调用 |
| **WebSocket JSON-RPC** | 长连接实时推送 |
| **MCP 工具服务** | 9 工具，AI Agent 可直接调用 |

### 单一执行内核（`tstdx/runtime/`）

| 特性 | 说明 |
|---|---|
| **唯一内核** | `UnifiedRuntime`：`QuerySpec → QueryPlan → 绑定执行 → QueryResult`，零缓存、无请求合并 |
| **精确绑定执行** | `DirectProviderExecutor` 按 `DIRECT_BINDINGS[(provider, channel, capability)]`（251 条）直调实现 |
| **规划期 fail-closed** | `catalog/capability.py::validate_call` 用**真实方法签名**绑定参数，参数错误在 I/O 前抛 |
| **执行身份与溯源** | `runtime/identity.py` + `runtime/provenance.py`：结果 provenance 与计划身份不符即抛 |
| **显式跨源编排** | `runtime/orchestration.py`：仅当调用方给出 `FallbackPolicy` 时按序尝试，逐次记入 `OrchestratedResult` |
| **启动三方对账** | `runtime/audit.py::audit_runtime`：registry / catalog / bindings 不一致即报错 |
| **60+ Typed Query 契约** | 10 领域基类 + 9 Domain Record 族，字段名与内核方法签名一一对应（`Client.typed`） |
| **批量执行** | `Client.quotes_batch()` → `BatchResult`：逐 symbol 三态（ok/missing/failed）、保序、串行直连单一 Provider（无隐藏换源） |
| **流式生命周期** | `StreamSpec`/`StreamPlanner` + `StatefulQuoteStream`（订阅/退订/状态查询） |

### 基础设施

| 特性 | 说明 |
|---|---|
| **零硬依赖** | 所有第三方库均为可选 extra |
| **错误分类树** | 九域 code 段（E1–E9）+ 每个异常自带 `RetryAdvice`；类清单以 `tstdx.errors` 现读为准，文档不抄录会过期的数字（`docs/errors.md`） |
| **可观测性** | zero-dep 指标注册表 + Prometheus/StatsD/OTLP 三导出器 |
| **传输与错误卫生** | TDX 连接可按 `security.use_tls` 走 TLS（默认关，`ssl.create_default_context()` 校验主机名）；错误上下文按关键字脱敏后才可外发。凭据存储**不在本库范围内**（ADR-007-010 已删除三级 CredentialStore）；Provider 绑定的 HTTP 主机白名单守卫已实现但尚未接入 web 链路（`docs/REFACTOR_PLAN_V17_CLOSURE.md` F-18） |
| **原创合规** | 洁净室工程规范：规格驱动 + License 隔离 + AST 相似度审计 + Golden 数据自采集 |

---

## 协议覆盖矩阵

| 协议族 | 端口 | 覆盖范围 | 命令账本 | 精确解析器 | 通用/透传 |
|---|---|---|---|---|---|
| **7709 标准**（`quotation`） | 7709 | A 股行情（K 线/实时/证券计数；分时与逐笔在结构化面已下线，见 [`docs/providers/tdx.md`](docs/providers/tdx.md)） | 39 | 18 | 是 |
| **7727 扩展市场**（`ex_quotation`） | 7727 | 港股/美股/期货/外汇/期权 | 17 | 15 | 是 |
| **MAC 专属**（`mac_quotation`） | 7709 | 板块/指数/概念/财务 | 16 | 16 | 是 |
| **F10 资料**（`f10`） | 7709 | 公司概况/股东/财务/交易信息 | 2 | 1 | 是 |
| **商品语义**（`goods`） | 7727 | 商品品种/合约/行情 | 11 | 11 | 是 |

> 未知命令自动归档至 `PROTOCOL_SPEC/UNKNOWN/`，工具链支持 `capture` 采集 → `codegen` 生成 → `spec_audit` 验证闭环。

---

## 功能对比

| 能力 | tstdx | mootdx | easy_tdx | easyquotation |
|---|---|---|---|---|
| TDX 协议覆盖 | 85 命令 / 61 精确解析器 / 5 协议族 | ~20 命令 | ~15 命令 | ❌（HTTP only） |
| 三级分派 | L1 精确 → L2 启发 → L3 透传 | 仅 L1 | 仅 L1 | ❌ |
| 同步 + 异步 | ✅ 双客户端 | ❌（仅同步） | ❌（仅同步） | ❌ |
| HTTP Web 多源 | 45+ 源 / 11 Provider 注册表 | ❌ | ❌ | ✅（单一源） |
| 本地 vipdoc 解析 | ✅ 多格式 | ❌ | ❌ | ❌ |
| 流式订阅 | ✅ engine 内核 | ❌ | ❌ | ❌ |
| 主站池治理 | ✅ 多主站 + 测速 + 社区注入 | 基础 | 基础 | ❌ |
| 统一业务入口 | ✅ `Client` 15 方法 + 通用 `execute`/`typed` | ❌ | ❌ | ✅（仅行情） |
| HTTP/WS/MCP 服务 | ✅ 三模式 | ❌ | ❌ | ❌ |
| 溯源与 provider-first | ✅ Provenance 强制 + 显式 FallbackPolicy | ❌ | ❌ | ❌ |
| 可观测性 | ✅ Prometheus/StatsD/OTLP | ❌ | ❌ | ❌ |
| YAML 协议规范 | ✅ + codegen + spec_audit | ❌ | ❌ | ❌ |
| 零硬依赖 | ✅ 所有 extra 可选 | 有硬依赖 | 有硬依赖 | 有硬依赖 |

---

## 安装

```bash
pip install tstdx                    # 零依赖基础安装
pip install "tstdx[all]"             # 完整功能
pip install "tstdx[dataframe,parquet,duckdb,web,metrics,server,mcp]"
pip install -e ".[dev]"              # 开发体验（pytest/ruff/mypy/pytest-asyncio/hatchling）
```

> **P14-D2 起**：`[project.optional-dependencies].dev` 已声明，本地与 CI 使用同一
> 门禁口径（覆盖率阈值只在 `pyproject.toml [tool.coverage.report] fail_under` 写一次，
> Makefile/CI 不再各传 `--cov-fail-under`），不再有「装了依赖却跑不出 `--cov`」的漂移。

### Optional Extras

| Extra       | 依赖                        | 功能               |
|-------------|---------------------------|--------------------|
| `config`    | pydantic                  | 严格配置校验       |
| `dataframe` | pandas                    | DataFrame 输出     |
| `parquet`   | pyarrow                   | ParquetSink        |
| `duckdb`    | duckdb                    | DuckDBSink         |
| `web`       | httpx                     | HTTP Web 行情源    |
| `metrics`   | prometheus-client         | Prometheus 导出    |
| `server`    | fastapi, uvicorn, websockets | HTTP REST 网关 + WebSocket RPC |
| `mcp`       | mcp                       | MCP 工具服务       |
| `tools`     | tzdata (win32 only)       | capture 时区工具链 |
| `dev`       | pytest/pytest-cov/pytest-asyncio/ruff/mypy/hatchling | 开发体验 |
| `all`       | 以上全部                   | 完整功能           |

---

## 兼容性

| 维度 | 支持范围 |
|---|---|
| **Python** | 3.10+（使用了 `X \| Y` 类型语法与 `zoneinfo`） |
| **操作系统** | Windows 10/11 · macOS 12+ · Linux（主流发行版） |
| **CI 矩阵** | Windows 3.11 + 3.12 |
| **网络** | TCP 7709/7727（TDX 主站）+ HTTPS（Web 源） |
| **存储** | 文件系统（`~/.tstdx/` 配置/主站排名/反馈）+ Parquet/DuckDB |

---

## 快速开始

### 协议层直连（`TdxClient`，可脱离内核使用）

```python
from tstdx.client import TdxClient

client = TdxClient()

bars = client.bars("sh600519", period="day", count=80)  # K 线
quotes = client.quotes(["sh600519", "sz000001"])  # 实时行情
count = client.security_count(market=1)  # 1=上海, 0=深圳
```

### 协议层异步（`AsyncTdxClient`）

```python
import asyncio
from tstdx.client import AsyncTdxClient


async def main():
    client = AsyncTdxClient()
    bars, quotes = await asyncio.gather(
        client.bars("sh600519", period="day", count=80),
        client.quotes(["sh600519", "sz000001"]),
    )
    print(len(bars), len(quotes))


asyncio.run(main())
```

### 统一查询内核（`Client`，172 项 capability）

```python
from tstdx import Client, FallbackPolicy, QuerySpec
from tstdx.typed_query import FundHoldingsQuery

client = Client()

# 1) 便捷方法：每个结果自带 provenance，零缓存、不静默换源
bars = client.bars("sh600519", period="day", count=80)
print(len(bars.data), bars.meta.provider, bars.meta.channel)

quotes = client.quotes(["sh600519", "sz000001"])

# 2) 通用面：任何 capability 走同一入口，参数在规划期按真实签名校验
spec = QuerySpec.build("bars", symbols="sh600519", period="day", count=30)
result = client.execute(spec)

# 3) 类型化糖衣：冻结 dataclass 契约 → 内核 → 强类型 Domain Record
typed = client.typed(FundHoldingsQuery(code="000001"))
print(typed.capability, len(typed.data))

# 4) 批量：逐 symbol 三态（ok/missing/failed），保序
batch = client.quotes_batch(["sh600519", "sz000001"])

# 5) 跨源只在显式策略下发生（默认永不发生）
orchestrated = client.quotes(["sh600519"], policy=FallbackPolicy(providers=("tdx", "tencent")))

client.close()
```

### 内核异步（`AsyncClient`，同一入口）

```python
import asyncio
from tstdx import AsyncClient


async def main():
    async with AsyncClient() as client:
        bars, quotes = await asyncio.gather(
            client.bars("sh600519", period="day", count=80),
            client.quotes(["sh600519", "sz000001"]),
        )
        print(len(bars.data), len(quotes.data))


asyncio.run(main())
```

> `TdxClient` / `AsyncTdxClient`（`tstdx.client`）是协议层客户端，可脱离内核单独使用；
> 服务面（CLI/HTTP/WS/MCP）全部只翻译为 `Client` 调用，不自行选源、不缓存。

### CLI（31 子命令）

```bash
tstdx bars sh600519 --period day --count 80     # K 线
tstdx quotes sh600519 sz000001                  # 实时行情
tstdx server-test                               # 主站测速
tstdx serve --bind 0.0.0.0 --port 8000           # HTTP 服务
tstdx probe 0x052D                              # 协议探测
tstdx feedback stats                            # 使用统计

# 主站池巡检
tstdx hosts audit --family quotation            # 仅 7709 标准族
tstdx hosts audit                               # 全 5 族并发巡检
tstdx hosts --hosts-file extra_hosts.json audit  # 注入社区贡献主站候选
tstdx hosts audit --report /tmp/audit.json --markdown /tmp/audit.md
```

---

## 核心代码文件地图

```
tstdx/
├── client/         # api.py —— Client / AsyncClient 唯一业务入口（15 便捷方法 + execute/typed/call）
│                   # core/sync/async_/factory —— TdxClient 传输层与共享纯协议 SSOT
├── runtime/        # 唯一执行内核：kernel(零缓存)/executor(251 绑定)/orchestration(显式跨源)
│                   #   /audit(启动三方对账)/identity/provenance(溯源守卫)
├── catalog/        # 静态声明与一致性审计：capability(目录+规划期签名校验)/provider_bindings
│                   #   /provider_contract/provider_guard/*_audit —— 无执行、无选源
├── providers/      # 11 Provider × 172 capability × channel 注册表（唯一事实源）
├── query.py        # QuerySpec/QueryPlan/QueryPlanner + 指纹
├── result.py       # QueryResult + ResultMeta + Provenance
├── batch.py        # BatchResult/BatchItem 三态批量契约
├── typed_query.py  # 60+ CapabilityQuery 冻结契约 + TypedQueryResult
├── stream_contract.py  # StreamSpec/StreamPlanner 流式契约
├── errors.py       # 错误分类树（E1-E9 九域）+ RetryAdvice
├── diagnostics.py  # 结果侧数据瑕疵的唯一发射口（WarningCode + 收集器，strict 的判据来源）
├── error_envelope.py  deprecation.py
├── protocol/       # commands(85 账本)/registry(三级分派+异常收口)/parsers(61 × 5 族)
├── codec/          # framing(帧)/primitive(原语 + count_guard + zlib strict)
├── transport/      # base(RLock 租约)/async_/pool(4 槽)/ratelimit/speedtest/hosts/sniff
├── client/         # core.py(同步/异步共享纯协议 SSOT) + sync/async_/factory + 5 族客户端
├── web/            # 45+ HTTP 源类（惰性导入）+ 域 Mixin 会话 + 共享分页器
├── reader/         # vipdoc 本地文件解析（day/min/板块/财务）
├── profile/        # 数据规格探测（帧/文件双探测器 + presets）
├── domain/         # symbol(单一事实源)/models/finance 记录/adjust/calendar
├── streaming/      # QuoteStream/AsyncQuoteStream（轮询+diff）/push + engine 内核
├── output/         # DataFrame/Parquet/CSV/DuckDB 原子写
├── sink/           # LocalDaySink：写回 vipdoc .day 二进制
├── charset/        # 字符集自动探测（GBK/GB18030/Big5/UTF-8）
├── config/         # 6 源合并 + 严格校验 + env 归一
├── integration/    # runtime_http(10 端点)/runtime_ws/runtime_tasks/mcp(9 工具)/serialization
├── observability/  # 指标注册表 + Prometheus/StatsD/OTLP 导出器 + start_exporter
├── feedback/       # 错误/用量上报 + 遥测 + 使用统计
├── tools/          # capture/spec_audit/codegen/golden_audit/golden_expand/check_originality
├── trade/          # 交易协议模拟器（实验性可选模块：SimTransport 纯内存模拟，不接入内核）
├── __main__.py     # python -m tstdx 入口（与 tstdx 控制台脚本等价）
└── cli/            # CLI 入口（31 子命令；数据命令全部经 Client，6 个传输/诊断命令除外，见 runtime_commands.py）
```

---

## Provider 选择与跨源策略

**默认永不跨源**（provider-first 不变量）：

```
QuerySpec(capability, provider=None|显式)
   → QueryPlanner：provider 为 None 时按注册表偏好选唯一 Provider；多 channel 冲突则要求显式指定
   → QueryPlan(单 provider, 单 channel) → DirectProviderExecutor → 该 Provider 的实现
   → QueryResult(meta.provenance 记录实际来源；与计划身份不符 → 抛错)
```

- Provider 不可用/失败：只抛出该 Provider 的错误，**不自动换源**（历史上"5 级降级 + 熔断 +
  合成数据"路由已随 v12 门面层物理删除）。
- 需要跨源时由调用方显式声明顺序：`client.quotes(symbols, policy=FallbackPolicy(providers=("tdx","tencent")))`
  → `ProviderOrchestrator` 按序尝试，返回 `OrchestratedResult`（逐步记录成败与最终来源）。
- 时效性：`currentness`（`CurrentnessMode`）声明口径，`deadline_ms` 只是执行预算；
  `options` 袋里只有 `tstdx.query.EXECUTED_OPTIONS` 列出的键会被执行面读取，其余键——包括
  `tstdx.query.REJECTED_OPTIONS` 里的策略键（过期容忍、部分放行）——一律当场拒绝：
  每次请求都回源，不存在可容忍的过期副本，partial 也始终是结果事实。

---

## 主站池治理与巡检

主站池由 `DEFAULT_HOST_POOL` / `POOL_BY_FAMILY` 定义，运行时通过 `RankingStore`
（`~/.tstdx/server_ranking.json`）落盘延迟样本并驱动三级路由降级。巡检工具：

```bash
# 通过 CLI（推荐）
tstdx hosts audit --family quotation --timeout 3 --workers 20 --samples 3

# 或调用脚本（支持 --report / --markdown / --strict / --no-save-ranking）
python scripts/audit_hosts.py --family all --report audit.json

# 外部候选注入（社区贡献主站入口）
cat > extra_hosts.json <<'JSON'
{
  "quotation": [
    {"host": "218.75.126.9", "port": 7709, "name": "custom-1"}
  ],
  "ex_quotation": [
    {"host": "180.153.180.86", "port": 7727, "name": "custom-2"}
  ]
}
JSON
tstdx hosts --hosts-file extra_hosts.json audit
```

输出：每族 healthy/degraded/offline 三态 + JSON/Markdown 报告；候选延迟样本写
`~/.tstdx/server_ranking.json`（可 `--no-save-ranking` 关闭）。CI 中已配置
周三 09:00 UTC 定期巡检（`host-audit` job，非硬门禁）。

---

## 质量与门禁

```bash
pytest tests/                                   # 全量测试（离线，无网络）
make gates                                      # 11 步确定性门禁：lint+format→mypy→全量→bridges→golden→spec→对抗→可达性→originality→benchmark→docs
python -m tstdx.tools.golden_audit --gate       # Golden L1 真实样本门禁（530 payload）
python -m pytest tests/adversarial -q           # 对抗矩阵（9 payload × 85 命令，逃逸=0）
python scripts/audit_reachability.py --strict   # 可达性门禁（孤儿=0）
python scripts/contract_audit.py --ci           # Typed 契约↔注册表↔Domain Record 五段对账 + 内核编译审计（ERROR 级缺口才阻断；契约待补项按 PENDING 报告）
python -m pytest --cov=tstdx           # 覆盖率门禁（阈值单源：pyproject fail_under=77）
```

- CI：11 jobs；Windows 矩阵 3.11 + 3.12；周三 09:00 UTC 定期 `host-audit`
- 架构守卫：`tests/architecture/`（唯一内核、零缓存、无聚合降级路由、根级命名空间白名单、
  已删层不可复活）+ `tests/provider_isolation/`（Provider 隔离与溯源）
- Ruff：`ruff check` 与 `ruff format --check` 均 0 错（待重排文件已在 V17 Phase 5 第 2 步
  以一次纯格式提交清零，dev 依赖钉版 `ruff==0.15.2`）
- mypy：`mypy tstdx/` 0 错（含 `--warn-unused-ignores`；V17 Phase 5 从 47 项清零）
- Pre-commit hooks：`ruff check --fix` + `ruff format --check`

---

## 路线图

### 当前阶段：v1.0.0 稳定版 + v1.x 单内核收口

| 里程碑 | 状态 | 说明 |
|---|---|---|
| Typed Capability 契约 | ✅ | 60+ 契约（10 领域基类），字段名与内核方法签名一一对应 |
| Domain Model | ✅ | 9 Domain Record 族 + 记录归一化 |
| Contract Automation | ✅ | `scripts/contract_audit.py --ci`：Registry/语义/内核编译/Domain Record/往返五段对账，ERROR 级缺口阻断（契约待补面按 PENDING 报告） |
| Streaming | ✅ | StreamSpec/StreamPlanner + StatefulQuoteStream |
| 单内核收敛（v16） | ✅ | 零缓存直调路径；v12 门面/service/sources/全部缓存层物理删除 |
| 断链清偿（v17 Phase 3A/3B/3D） | ✅ | v14 信封运行时 + `execution/` DAG + `provider/` router + registry 三件套删除；typed 全线接通 |
| 命名空间归位（v17 Phase 3C） | ✅ | 根级模块 26→11；`runtime/` `catalog/` `client/` 分层 |
| 文档与对外面统一（v17 Phase 4） | ✅ | README/ARCHITECTURE 已刷新；30 份历史方案入 `docs/archive/plans`；文档-代码一致性门禁上线（导入语句/点号路径/README 数字/结构树/门禁规模逐项对账） |
| 发布硬化（v17 Phase 5） | ◐ | mypy 47→0、ruff format 65 文件清零、三项 strict 门禁转绿、豁免清单与 ghost 门禁审计完成、离线整仓覆盖率 80.84%（本机 Windows+py3.12 仓内 `.venv`，阈值 77 未动）、七格真实网络/服务面冒烟与 wheel 安装冒烟均已执行（6 PASS / 1 FAIL；K 线那一格已在第 34 步归因为本端握手字节并修复）。F-37 已按用户裁决 (c) 执行：下调能力声称、tag `v1.1.0-dev.1` 继续推迟（见下）。仍待：`0x000F`/`0x0010` 字段错位的真机判据、一次工作日盘中复跑、按 CI 环境数字重钉覆盖率 |

### 下一阶段

| 计划 | 方向 |
|---|---|
| **发布硬化** | 已做：mypy 既有告警清零、覆盖率基线按有效代码重校（本机 80.84%，阈值 77 未下调）、wheel 安装冒烟 `SMOKE_RC=0`（第 16 步）。未做：按 CI（ubuntu+py3.11）数字重钉 `fail_under`、拿用户确认打 tag `v1.1.0-dev.1` |
| **Live Smoke** | 已做：七格真实网络/服务面冒烟逐格执行（tdx/web 直连、K 线、stream、CLI/HTTP/MCP 各一发）＝6 PASS / 1 FAIL（第 16 步）。已定：F-37 按裁决 (c) 落地——`0x000F`/`0x0010` 的能力口径已下调为「条数可用、字段语义不保证」，本发布不声称 7709 历史族字段级 live 正确，tag 因此继续推迟。未做：一次工作日盘中复跑（第 16 步落在周六休市）、那两条命令的字段布局判据，以及把 7709 数据面的 live 判据接进门禁（F-38，刻意不在裁决前钉成固定红） |
| **Streaming 增量执行** | 流式数据增量合并 + 补数完整性保证 |
| **可达性收口** | 孤儿=0，且每条豁免记录都被门禁盯着：指向不存在模块的死记录、已接线却未撤销的过期记录、理由过短、重复条目都会让 `--strict` 失败。仍待裁决的一项：`tstdx.providers.http`（Provider 绑定的 HTTP 主机白名单守卫）接线还是删除，属安全面决策 |

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [DESIGN.md](DESIGN.md) | 完整设计方案 v2.0（架构/协议/工程规范，历史版本见 docs/archive/）|
| [docs/quickstart.md](docs/quickstart.md) | 快速入门 |
| [docs/api/README.md](docs/api/README.md) | API 索引（内核/协议/传输/服务面/基础设施）|
| [docs/api/interfaces.md](docs/api/interfaces.md) | **项目接口文档**（全部公开接口面汇总）|
| [docs/cookbook/](docs/cookbook/README.md) | 场景示例（批量 K 线/显式跨源/离线 vipdoc/流式/Sinks/自定义命令/单内核）|
| [docs/migration/](docs/migration/README.md) | 从 mootdx/easy_tdx/easyquotation 迁移 |
| [docs/adr/](docs/adr/README.md) | 架构决策记录（含 ADR-011 流式内核取舍）|
| [docs/FAQ.md](docs/FAQ.md) | 常见问题 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 排障指南 |
| [docs/errors.md](docs/errors.md) | 错误体系与 RetryAdvice 使用指南 |
| [docs/FEATURE_MAP_AND_ROADMAP.md](docs/FEATURE_MAP_AND_ROADMAP.md) | 主体功能地图 + v1.2.0 后路线 |
| [docs/POTENTIAL_ISSUES_AND_PLAN.md](docs/POTENTIAL_ISSUES_AND_PLAN.md) | 当前批次状态表与后续规划 |
| [PROTOCOL_SPEC/](PROTOCOL_SPEC/README.md) | 协议命令 YAML 规范 + codegen/spec_audit 闭环 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录（含 native 弃用时间线 v1.5.0/v1.6.0）|
| [docs/releases/v1.0.0.md](docs/releases/v1.0.0.md) | v1.0.0 正式发布说明、兼容性与验证结果 |
| [docs/archive/](docs/archive/) | 历史计划与设计归档 |

---

## 贡献

欢迎贡献代码、文档、协议规范、主站候选、测试数据。请阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [GOVERNANCE.md](GOVERNANCE.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)

---

## 许可证

MIT License - 详见 [LICENSE](LICENSE)
