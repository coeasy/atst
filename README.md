# tstdx

> **TongDaXin Standard Data eXchange** — 通达信行情数据通用协议基础设施
>
> 类比 HTTP 世界的 `requests` 库：稳定、标准、可组合，专注协议层，不做应用层业务。

**当前版本**：v1.0.0（正式稳定版，2026-09-09 发布） · [发布说明](docs/releases/v1.0.0.md) · [CHANGELOG](CHANGELOG.md)

---

## 项目简介

### 是什么

tstdx 是通达信（TDX）行情数据的**通用底层协议基础设施**。它完整覆盖 TDX 的 5 套协议族，提供从原始二进制帧解析到高级门面 API 的全链路数据接入能力。

### 解决什么问题

| 痛点 | tstdx 的解法 |
|---|---|
| TDX 协议封闭、逆向工程门槛高 | 85 命令账本 + 61 精确解析器 + 三级分派 + YAML 协议规范 |
| 单一数据源不可靠 | 5 级降级路由（tdx → web → reader → cache → synthetic），45 个 HTTP Web 源 |
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
│                      v14 Runtime 编排内核                             │
│  Runtime.execute() · execute_batch() · subscribe() · RuntimeGateway  │
│  60+ Typed Query 契约 · 9 Domain Record 族 · 语义缓存 L1/L2           │
│  ExecutionPlanner DAG 编排 · StreamPlanner + StreamLifecycle         │
├─────────────────────────────────────────────────────────────────────┤
│                        服务面（可选部署）                              │
│  HTTP REST 42 端点 · WebSocket JSON-RPC · MCP stdio 12 工具           │
├─────────────────────────────────────────────────────────────────────┤
│                          门面层                                       │
│  UnifiedQuoteAPI（46 方法，四路由 + 熔断）· AsyncUnifiedQuoteAPI      │
│  ApiResponse{success, error, data, extra} · 惰性 .df                  │
├─────────────────────────────────────────────────────────────────────┤
│                        数据源路由层                                   │
│  5 级降级：tdx → web(45 源) → reader(vipdoc) → cache → synthetic     │
│  DataSourceRouter · SourceUnavailable 熔断 · adjust 口径守卫          │
├─────────────────────────────────────────────────────────────────────┤
│                      客户端层                                         │
│  TdxClient · AsyncTdxClient · GoodsClient · ExMarketClient            │
│  MacClient · F10Client（_mixin 共享骨架 + sync/async/factory）        │
├─────────────────────────────────────────────────────────────────────┤
│                      协议核心层                                       │
│  commands(85 账本) · registry(三级分派 + 异常收口)                     │
│  parsers(61 精确解析器 × 6 族) · codec(帧/原语) · transport(传输)      │
│  stream_contract(流式契约) · stream_planner(流式规划)                  │
├─────────────────────────────────────────────────────────────────────┤
│                      基础设施层                                       │
│  错误体系(E1-E8, 40+ 类) · 可观测性(Prometheus/StatsD/OTLP)          │
│  配置(6 源合并) · 安全(凭据三级存储) · 输出(DataFrame/Parquet/DuckDB)  │
│  反馈(遥测/统计) · 工具链(capture/codegen/golden_audit/spec_audit)    │
└─────────────────────────────────────────────────────────────────────┘
```

**设计原则**：每层可独立使用，零强制上层依赖。底层协议库可用，上层编排可选。

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
| **5 级降级路由** | tdx → web → reader → cache → synthetic，`SourceUnavailable` 熔断 |

### 数据源层

| 特性 | 说明 |
|---|---|
| **HTTP Web 45 源类** | 东财/新浪/腾讯/集思录/港股/中行等，`httpx` / `urllib` 双栈，17 模块 |
| **本地 vipdoc 解析** | `reader/` 解析通达信本地 `.day` / `.min` / 板块 / 财务二进制文件 |
| **流式订阅** | QuoteStream + AsyncQuoteStream（engine 内核：ReconnectPolicy + BackpressureQueue + DeltaMerger + GapFiller + StreamEngine） |

### 输出与服务层

| 特性 | 说明 |
|---|---|
| **3 Sink 策略** | DataFrame / Parquet / DuckDB（原子写） |
| **门面统一 API** | `UnifiedQuoteAPI`（46 方法，auto/tdx/web/local 四路由 + 熔断 + `adjust` 口径守卫）+ `ApiResponse` |
| **异步门面** | `AsyncUnifiedQuoteAPI`（`asyncio.to_thread` 桥接，SourceUnavailable 自动继承） |
| **HTTP REST 网关** | 42 端点（方法白名单 + TaskStore 钳制） |
| **WebSocket JSON-RPC** | 长连接实时推送 |
| **MCP 工具服务** | 12 工具，AI Agent 可直接调用 |

### v14 Runtime 编排内核

| 特性 | 说明 |
|---|---|
| **统一入口** | `Runtime.execute()` / `execute_batch()` / `subscribe()` |
| **RuntimeGateway** | CLI/HTTP/WS 网关适配，禁止独立 Provider 选择/回退/缓存逻辑 |
| **60+ Typed Query 契约** | 9 领域（bars/quotes/minute/finance/holder/ipo/market/exchange/stream） |
| **9 Domain Record 族** | BarRecord / QuoteRecord / MinuteRecord / FinanceRecord 等强类型记录 |
| **语义缓存** | L1 内存（QueryFingerprint）+ L2 持久化，批量执行自动去重 |
| **批量执行** | `execute_batch()` 并发执行 + 语义缓存去重，保序返回 |
| **DAG 编排** | `ExecutionPlanner` DAG 拓扑排序 + 依赖解析 |
| **流式生命周期** | StreamPlanner + StreamLifecycle（订阅/退订/状态查询） |

### 基础设施

| 特性 | 说明 |
|---|---|
| **零硬依赖** | 所有第三方库均为可选 extra |
| **40+ 异常类** | 分类错误树（E1–E8）+ `RetryAdvice`；`SourceUnavailable` 归 E7 域 |
| **可观测性** | zero-dep 指标注册表 + Prometheus/StatsD/OTLP 三导出器 |
| **安全** | 凭据三级存储（keyring/env/file 互斥写 + 损坏隔离） |
| **原创合规** | 洁净室工程规范：规格驱动 + License 隔离 + AST 相似度审计 + Golden 数据自采集 |

---

## 协议覆盖矩阵

| 协议族 | 端口 | 覆盖范围 | 精确解析 | 通用/透传 |
|---|---|---|---|---|
| **7709 标准** | 7709 | A 股行情（K 线/实时/分时/逐笔/证券计数） | 18 命令 | 是 |
| **7727 扩展市场** | 7727 | 港股/美股/期货/外汇/期权 | 12 命令 | 是 |
| **MAC 专属** | 7709 | 板块/指数/概念/财务 | 8 命令 | 是 |
| **F10 资料** | 7709 | 公司概况/股东/财务/交易信息 | 15 命令 | 是 |
| **商品语义** | 7709 | 商品品种/合约/行情 | 8 命令 | 是 |

> 未知命令自动归档至 `PROTOCOL_SPEC/UNKNOWN/`，工具链支持 `capture` 采集 → `codegen` 生成 → `spec_audit` 验证闭环。

---

## 功能对比

| 能力 | tstdx | mootdx | easy_tdx | easyquotation |
|---|---|---|---|---|
| TDX 协议覆盖 | 85 命令 / 61 精确解析器 / 5 协议族 | ~20 命令 | ~15 命令 | ❌（HTTP only） |
| 三级分派 | L1 精确 → L2 启发 → L3 透传 | 仅 L1 | 仅 L1 | ❌ |
| 同步 + 异步 | ✅ 双客户端 | ❌（仅同步） | ❌（仅同步） | ❌ |
| HTTP Web 降级 | 45 源 / 5 级降级 | ❌ | ❌ | ✅（单一源） |
| 本地 vipdoc 解析 | ✅ 多格式 | ❌ | ❌ | ❌ |
| 流式订阅 | ✅ engine 内核 | ❌ | ❌ | ❌ |
| 主站池治理 | ✅ 多主站 + 测速 + 社区注入 | 基础 | 基础 | ❌ |
| 门面统一 API | ✅ 46 方法 + 熔断 | ❌ | ❌ | ❌ |
| HTTP/WS/MCP 服务 | ✅ 三模式 | ❌ | ❌ | ❌ |
| v14 Runtime 编排 | ✅ DAG + 批量 + 缓存 | ❌ | ❌ | ❌ |
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
> 门禁口径（`fail_under=77` / `--cov-fail-under=77`），不再有「装了依赖却跑不出
> `--cov`」的漂移。

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
| **存储** | 文件系统（`~/.tstdx/` 配置/缓存/排名）+ Parquet/DuckDB |

---

## 快速开始

### 基础用法

```python
from tstdx import TdxClient

client = TdxClient()

bars = client.bars("sh600519", period="day", count=80)  # K 线
quotes = client.quotes(["sh600519", "sz000001"])  # 实时行情
count = client.security_count(market=1)  # 1=上海, 0=深圳
```

### 异步用法

```python
import asyncio
from tstdx import AsyncTdxClient


async def main():
    client = AsyncTdxClient()
    bars, quotes = await asyncio.gather(
        client.bars("sh600519", period="day", count=80),
        client.quotes(["sh600519", "sz000001"]),
    )
    print(len(bars), len(quotes))


asyncio.run(main())
```

### 门面统一响应（永不抛异常边界）

```python
from tstdx.facade import quote_api

api = quote_api()
resp = api.query("quotes", ["sh600519", "sz000001"])
if resp:
    print(len(resp.data), "条, 源=", resp.extra.get("source"))
    df = resp.df  # pandas DataFrame（可选）
else:
    print(f"失败: {resp.error} (code={resp.code})")
```

### v14 Runtime（编排内核 + 批量执行）

```python
from tstdx.runtime import RuntimeGateway, create_runtime
from tstdx.cache_semantic import SemanticResultCache
from tstdx.runtime import QueryRequest

# 创建带语义缓存的 Runtime
gateway = RuntimeGateway(create_runtime(semantic_cache=SemanticResultCache()))

# 单次查询
resp = gateway.bars("sh600519", count=30)
if resp.success:
    print(resp.data)

# 批量执行（语义缓存去重 + 并发）
reqs = [
    QueryRequest(operation="bars", args=("sh600000",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh600519",), params={"count": 30}),
    QueryRequest(operation="bars", args=("sh000001",), params={"count": 30}),
]
results = gateway.execute_batch(reqs, max_concurrent=4)

# 缓存诊断
print(gateway.semantic_cache_stats())  # {'enabled': True, 'tier': 'l1', 'size': 3}
```

### CLI（19+ 子命令）

```bash
tstdx bars sh600519 --period day --count 80     # K 线
tstdx quotes sh600519 sz000001                  # 实时行情
tstdx server-test                               # 主站测速
tstdx serve --host 0.0.0.0 --port 8000          # HTTP 服务
tstdx probe 0x052D                              # 协议探测
tstdx feedback stats                            # 使用统计

# 主站池巡检
tstdx hosts audit --family quotation            # 仅 7709 标准族
tstdx hosts audit                               # 全 5 族并发巡检
tstdx hosts audit --hosts-file extra_hosts.json # 注入社区贡献主站候选
tstdx hosts audit --report /tmp/audit.json --markdown /tmp/audit.md
```

---

## 核心代码文件地图

```
tstdx/
├── protocol/       # 协议核心：commands(85 账本)/registry(三级分派+异常收口)
│   └── parsers/    #   6 族 61 解析器（std7709/std7709_extra/std7727/mac/goods/f10）
├── codec/          # 编解码：framing(帧)/primitive(原语+count_guard+zlib strict)
├── transport/      # 传输：base(RLock 租约)/async_/pool(4 槽)/ratelimit/speedtest/hosts/sniff
├── client/         # TdxClient/AsyncTdxClient + 5 族客户端（_mixin 共享骨架 + sync/async_/factory）
├── facade/         # 门面：UnifiedQuoteAPI(四路由+熔断) / response / async_api / 三兼容门面
├── web/            # 50 Source 类（惰性导入）+ 域 Mixin 会话 + _paginate 共享分页器
├── sources/        # 5 级降级路由 DataSourceRouter + golden 回放
├── domain/         # symbol(单一事实源)/models/adjust/calendar
├── streaming/      # QuoteStream/AsyncQuoteStream（轮询+diff）/push
├── reader/         # vipdoc 本地文件解析（day/min/板块/财务）
├── output/         # DataFrame/Parquet/CSV/DuckDB 原子写（v9 自 sinks/ 更名，旧名 shim 兼容）
├── sink/           # LocalDaySink：写回 vipdoc .day 二进制（与 sinks/ 职责不同）
├── charset/        # 字符集自动探测（GBK/GB18030/Big5/UTF-8）
├── profile/        # 数据规格探测（帧/文件双探测器 + presets）
├── config/         # 6 源合并 + 严格校验 + env 归一
├── errors.py       # 错误分类树（E1-E8，40+ 类）+ RetryAdvice
├── integration/    # http_server(42 端点白名单)/ws_server/mcp_server
├── observability/  # 指标注册表 + Prometheus/StatsD/OTLP 导出器 + start_exporter
├── feedback/       # 错误/用量上报 + 遥测 + 使用统计
├── security/       # 凭据三级存储（keyring/env/file 互斥写 + 损坏隔离）
├── tools/          # capture/spec_audit/codegen/golden_audit/golden_expand/check_originality
├── trade/          # 交易协议模拟器（独立可选：SimTransport 纯内存模拟）
├── runtime/        # v14 Runtime 编排内核（Runtime/Gateway/Planner/Query/Stream）
├── cache_semantic/ # 语义缓存（L1 内存/L2 持久化）
├── cli/            # CLI 入口（19 子命令）
```

---

## 数据源降级

```
TDX 主站 → HTTP Web 源(45) → 本地 vipdoc → golden 缓存 → 合成数据
```

每级失败时自动降级到下一级，`SourceUnavailable` 异常触发熔断器。可通过 `DataSourceRouter` 配置降级顺序和熔断阈值。

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
tstdx hosts audit --hosts-file extra_hosts.json
```

输出：每族 healthy/degraded/offline 三态 + JSON/Markdown 报告；候选延迟样本写
`~/.tstdx/server_ranking.json`（可 `--no-save-ranking` 关闭）。CI 中已配置
周三 09:00 UTC 定期巡检（`host-audit` job，非硬门禁）。

---

## 质量与门禁

```bash
pytest tests/                                   # 全量测试
make gates                                      # 六步门禁：lint→format→全量→对抗矩阵→golden 三旗标→可达性
python -m tstdx.tools.golden_audit --gate       # Golden L1 真实样本门禁（530 payload）
python -m pytest tests/adversarial -q           # 对抗矩阵（9 payload × 85 命令，逃逸=0）
python scripts/audit_reachability.py --strict   # 可达性门禁（孤儿=0）
python -m pytest --cov=tstdx --cov-fail-under=77  # 覆盖率门禁（CI 与本地一致）
```

- CI：9 jobs；Windows 矩阵 3.11 + 3.12；周三 09:00 UTC 定期 `host-audit`
- 覆盖率门禁：**≥ 77%**（P14-D2 与 CI 对齐）
- Pre-commit hooks：`ruff check --fix` + `ruff format --check`
- Ruff + mypy：全量清洁（0 errors / 0 warnings）

---

## 路线图

### 当前阶段：v1.0.0 稳定版

| 里程碑 | 状态 | 说明 |
|---|---|---|
| Phase 1 Typed Capability | ✅ | 60+ 类型化查询契约（9 领域） |
| Phase 2 Domain Model | ✅ | 9 Domain Record 族 |
| Phase 3 Provider Adapter | ✅ | 语义执行桥接 |
| Phase 4 Contract Automation | ✅ | 契约自动化测试 |
| Phase 5 Streaming v14 | ✅ | StreamPlanner + StreamLifecycle 集成 |
| Phase 6 Gateway convergence | ✅ | RuntimeGateway 桥接 CLI/HTTP/WS |
| Phase 7 Optimizer | ✅ | execute_batch() 批量执行 + 语义缓存去重 |
| Phase 8 Release Hardening | ✅ | Ruff + mypy 清洁 + 测试覆盖率 ≥ 77% |

### 下一阶段

| 计划 | 方向 |
|---|---|
| **CLI 迁移** | 从 TdxClient 迁移到 RuntimeGateway 统一入口 |
| **DAG CSE** | 公共子表达式消除 + 并行执行优化 |
| **Streaming 增量执行** | 流式数据增量合并 + 补数完整性保证 |
| **覆盖率提升** | AST 可达性门禁 + golden/spec 门禁 |
| **Wheel Smoke** | wheel 与 sdist 构建 + 安装冒烟测试 |

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [DESIGN.md](DESIGN.md) | 完整设计方案 v2.0（架构/协议/工程规范，历史版本见 docs/archive/）|
| [docs/quickstart.md](docs/quickstart.md) | 快速入门 |
| [docs/api/README.md](docs/api/README.md) | API 索引（客户端/门面/Runtime/服务面/工具）|
| [docs/api/v14-runtime.md](docs/api/v14-runtime.md) | **v14 Runtime 编排内核**完整 API 参考 |
| [docs/api/interfaces.md](docs/api/interfaces.md) | **项目接口文档**（全部公开接口面汇总）|
| [docs/cookbook/](docs/cookbook/README.md) | 场景示例（批量 K 线/离线 vipdoc/流式/Sinks/自定义命令/v14 Runtime）|
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
