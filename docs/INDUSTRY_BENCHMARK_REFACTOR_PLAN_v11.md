# tstdx 统一行业对标重构与优化执行方案 v11

> Branch: `refactor/industry-benchmark-v11`  
> Main baseline: `f927e7faf49e77352b531addc49ecc6e44abcf81` (`v1.4.0`)  
> Updated: 2026-09-08  
> Status: **Current execution SSOT / 当前唯一执行基线**
>
> 本文融合并取代以下文档作为“当前实施计划”的职责：
> `ARCHITECTURE_AND_OPTIMIZATION_PLAN_v6.md`、`INDUSTRIAL_OPTIMIZATION_PLAN.md`、
> `REFACTOR_PLAN_v10.md`、`POTENTIAL_ISSUES_AND_PLAN.md` 以及 v11 前两版草案。
> 历史文档继续保留作为审计证据、已完成批次记录和协议事实来源，不再与本文并行排期。

---

## 0. 执行结论

`tstdx` 当前已经不是单一 TDX reader/client，而是一套包含 **TDX 二进制协议、
连接池、多数据源、vipdoc、本地/远端缓存、Streaming、REST/WS/MCP、输出、配置、
可观测性** 的市场数据基础设施。

现阶段最重要的工作不是继续增加接口，而是把已经存在的能力收敛成 **一条可证明、
可测量、可扩展的执行主干**：

```text
Python / Async / CLI / REST / WS / MCP
                    |
                    v
          UnifiedMarketDataService
                    |
        QuerySpec / ContractGuard
                    |
                    v
        QueryPlanner / RoutePolicy
                    |
                    v
              SourceManager
        /        |        |        \
      TDX       Web     Vipdoc    Replay
        \        |        |        /
                    v
      Canonical Domain + QueryResult
                    |
              Cache / Output
```

最终只允许保留：

1. **一个能力事实源**：`CapabilityRegistry`；
2. **一个查询契约**：`QuerySpec`；
3. **一个执行入口**：`UnifiedMarketDataService`；
4. **一个路由规划器**：`QueryPlanner/CanonicalRouter`；
5. **一个 Source 生命周期所有者**：`SourceManager`；
6. **现有 E1-E9 错误树作为唯一异常事实源**；
7. **一个结果/来源/失败轨迹契约**：`QueryResult`；
8. **一个缓存语义模型**：Cache 只能优化执行，不能改变业务结果；
9. **一个实时调度模型**：请求和订阅共享 Source 生命周期与数据契约；
10. **所有外部入口只做协议适配，不再自行决定数据源和 fallback**。

核心验收标准：

> **一条主干、一个事实源、多个 Provider/Source、所有入口一致；无隐式跨源、
> 无缓存口径漂移、无请求间状态污染、无语义孤儿逻辑。**

---

# 1. 不同历史方案的融合结论

## 1.1 历史方案如何处理

| 文档 | 当前定位 | 本文处理 |
|---|---|---|
| `INDUSTRIAL_OPTIMIZATION_PLAN.md` | v1.1.0 深审历史证据 | 只保留仍成立的并发、协议定标、SSOT 思想；已修问题不重新排期 |
| `ARCHITECTURE_AND_OPTIMIZATION_PLAN_v6.md` | v1.2-v1.3 结构治理方案 | 吸收连接复用、命令账本 SSOT、Web 生命周期、文档门禁、不过度工程原则 |
| `REFACTOR_PLAN_v10.md` | 已完成结构收敛记录 | v10/P11 已完成事项视为稳定资产，不重复“再重构一次” |
| `POTENTIAL_ISSUES_AND_PLAN.md` | v1.4.0 健康状态与外部悬案 | 吸收 Windows 矩阵、性能基线、真机定标、主站池巡检等仍开放事项 |
| v11 前两版 | 最新源码审计 | 保留 P0/P1/P2 真实问题，并进一步加入行业对标和迁移设计 |

## 1.2 冲突裁决原则

出现历史方案冲突时按以下优先级裁决：

```text
最新源码事实
  > 当前协议 Command Ledger / golden 事实
  > 当前公开 API 兼容契约
  > 当前 CI/测试事实
  > 历史方案建议
```

因此：

- 历史方案建议“重做错误树”，**不执行**。当前 `tstdx/errors.py` 已形成 E1-E9、
  `RetryAdvice`、`http_status_for()`、结构化 `to_dict()`，应作为稳定公开契约。
- 历史方案中已经完成的 async 桥接修复、httpx `post`、parser/web 拆分、CredentialStore
  删除、命令 offline fail-fast 等，不重新列入活跃 P0。
- “可达性孤儿=0”只代表 AST/module reachability；**不代表语义孤儿=0**。
  当前仍存在“配置有字段但无消费者”“模块可达但主服务链未接入”“并行实时路径”等
  semantic orphan，本文单独治理。
- 协议层真实 `offline/degraded` 命令不得因高层重构被改写。

---

# 2. 行业主流项目对标

本轮不做“照抄”，只抽取与 tstdx 当前阶段直接相关的工程模式。

## 2.1 对标矩阵

| 项目 | 值得借鉴 | tstdx 采用方式 | 明确不照搬 |
|---|---|---|---|
| **CCXT** | `describe()/has` 能力声明；能力可区分 supported / unsupported / emulated；稳定异常树；provider 级 rate limit | `CapabilityRegistry` + `exact/approximate`；Source 级限流/健康；稳定错误码 | 不引入交易/订单大模型，不扩大 tstdx 实盘交易边界 |
| **OpenBB** | 标准 `QueryParams` / `Data`，Provider `Fetcher` 做 transform-query → extract → transform-data | `QuerySpec` + Source Adapter + canonical result；Provider 只负责外部协议到统一模型转换 | 不把 Pydantic 等变成核心硬依赖，继续保持 tstdx 零硬依赖优势 |
| **AKShare** | 构建期接口 registry；schema version；代码导出与文档双向 ratchet；确定性生成 | 构建 `capabilities.json/API registry`；CI `--check`；接口/文档/registry 三方一致 | 不复制“每数据源一个公开函数”的大平面 API，避免继续扩大 surface |
| **NautilusTrader** | DataClient/adapter 与 DataEngine 分离；业务入口使用统一 engine，不直接碰 transport；request/subscription 共享 adapter 生命周期 | `UnifiedMarketDataService` + `SourceManager`；历史请求和 Streaming 共享 Source Adapter | 不引入完整交易 kernel / event bus / portfolio 复杂度 |
| **mootdx / pytdx 类** | TDX 协议、reader、便捷 API 边界简单直接 | 保留 `TdxClient` 与 reader 作为低层专业 API | 不让高层 Query Engine 侵入低层协议研究入口 |

行业对标后的关键结论：

> tstdx 应采用“**标准请求/结果 + Provider Adapter + 中央执行引擎 + 机器可读能力表**”，
> 而不是继续在 Facade、Router、HTTP、WS、MCP 各自写一套路由判断。

## 2.2 能力声明必须区分“静态能力”和“动态可用性”

这一点在当前 v11 草案里需要进一步修正。

不能把以下两件事混在同一个字段：

```text
Capability: 这个 Source 理论上能否提供 bars/day/raw？
Availability: 这个 Source 此刻是否健康、被熔断、被限流？
```

建议拆为：

```text
CapabilityRegistry       # 静态事实，可生成文档，可做 CI
SourceHealthRegistry     # 动态事实，运行时变化
CommandLedger            # TDX 命令真实 online/offline/degraded 事实
```

Planner 组合三者做决策，而不是修改 capability 本身。

---

# 3. 当前稳定资产：默认不推倒重写

以下模块已经经过多轮 golden / adversarial / regression，不应为了“架构漂亮”而重写：

- `tstdx.codec`；
- `tstdx.protocol` 已验证 Command Ledger；
- L1/L2/L3 parser 分派机制；
- 已验证协议 parser 与 golden 样本；
- `TdxClient` 的低层协议 API；
- transport Host/ConnectionPool 已有的主站轮换、排名和协议事实；
- E1-E9 错误码体系；
- `RetryAdvice`；
- reader `.day/.lc1/.lc5/.dat/gpcw` 基础解析能力；
- 当前输出模型与零硬依赖原则；
- trade 模块“模拟/研究，不做实盘”的边界。

禁止通过以下方式完成重构：

- 降低测试、coverage、golden、adversarial、reachability 门禁；
- 把已标记 offline 的命令写成 online；
- `except Exception: pass`；
- Web 近似数据静默冒充精确 TDX 数据；
- synthetic 静默进入生产 fallback；
- 为兼容继续增加第三、第四套路由器；
- HTTP/WS/MCP 再实现一遍 source 选择；
- 引入 Redis/Kafka/Pydantic/Rust 等新的核心硬依赖来“解决架构问题”。

---

# 4. 最新源码审计后的活跃问题清单

## 4.1 P0：数据正确性 / 明确断链

| ID | 问题 | 风险 | 本轮动作 |
|---|---|---|---|
| P0-01 | 本地 `M5` reader 可能走 `DayBarReader` | `.lc5` 错布局解析 | `M1/M5 -> MinBarReader` 明确分派 + fixture |
| P0-02 | Facade local bars 只允许 day | `.lc1/.lc5` 能力存在但主入口断链 | 接入 local minute bars |
| P0-03 | KlineCache 未保留 `start` | cache hit 返回错误窗口 | Phase 0 先 bypass 非安全查询，Phase 3 Cache V2 |
| P0-04 | KlineCache 未保留 `adjust` | raw/qfq/hfq 互相污染 | 同上 |
| P0-05 | cache 先于 explicit route 命中 | `route=tdx` 可能拿到别源数据 | 显式 source 必须约束 cache provenance |
| P0-06 | `_get_router()` 未透传 `timeout/web_sources` | 用户配置看似生效实际被旁路 | 统一 composition root |
| P0-07 | Facade 绕过 configured router factory | 全局 source/cache 配置出现两套语义 | 统一 Service factory |
| P0-08 | Facade 与 SourcesConfig 存在两套 auto 顺序 | 同一请求不同入口结果可能不同 | 见 §8 兼容迁移 |
| P0-09 | `security_list()` 违反 explicit route | 显式 tdx 仍可能偷偷走 web | explicit source fail-closed |
| P0-10 | `sources.SourceUnavailable` 与 `errors.SourceUnavailable` 重复 | catch/code/status 不一致 | 旧路径 alias 到 canonical E7050 |
| P0-11 | REST snapshot 参数与 client 单 symbol 签名错位 | 运行时断链 | service contract test + 正确 batch API |
| P0-12 | REST auction snapshot 同类问题 | 运行时断链 | 同上 |
| P0-13 | HTTP 默认实例只覆盖 `TdxClient` | Goods/Ex/Mac/F10 注册路由默认不可用 | RuntimeContext 管理多族 source |
| P0-14 | HTTP 默认绕过统一 facade/router | fallback/capability 不一致 | HTTP 只依赖 Service |
| P0-15 | Task cancel 不终止 worker 且可绕 active limit | 资源耗尽 | bounded executor + cancellation state |
| P0-16 | running task 可从 registry 删除 | orphan execution | running 不可 purge |
| P0-17 | `adjusted_bars(events=[])` 被当成“未提供” | 意外联网、语义错误 | `events is not None` |

## 4.2 P1：架构 / 生命周期 / 并发

| ID | 问题 | 风险 | 目标 |
|---|---|---|---|
| P1-01 | Router 每请求创建/关闭 TdxClient | ConnectionPool 复用失效 | SourceManager 长生命周期 |
| P1-02 | Router 每请求创建/关闭 Web client | HTTP keep-alive 失效 | persistent session |
| P1-03 | Facade client 与 Router client 并存 | 双连接池、双 health state | 单一所有权 |
| P1-04 | `last_errors/last_source` 是 router mutable state | 并发/请求串扰 | QueryResult attempts |
| P1-05 | broad `Exception` 被当 source failure | TypeError/AttributeError 被隐藏 | recoverable whitelist |
| P1-06 | Async facade 共享 sync facade + thread bridge | init/circuit/close 竞态 | 短期锁+bounded executor，中期 async adapter |
| P1-07 | route circuit 状态并发保护不统一 | 状态不确定 | SourceHealthRegistry |
| P1-08 | Web-only 方法自行 new Session | config/lifecycle 再次旁路 | WebSource 统一持有 |
| P1-09 | 同能力不同方法错误转换不同 | API 不稳定 | ErrorEnvelope + policy |
| P1-10 | HTTP/WS/MCP 各自决定 client/facade | 服务行为漂移 | Service-only integrations |
| P1-11 | MCP 部分工具直接碰 offline TDX 命令 | 已有 fallback 无法复用 | MCP -> Service |
| P1-12 | WS 多方法仍直连 TdxClient/Web | 同上 | WS -> Service |

## 4.3 P1：Streaming / Task

| ID | 问题 | 目标 |
|---|---|---|
| P1-13 | 所有订阅按最小 interval 拉取 | 每订阅独立 `next_due` |
| P1-14 | 多订阅 symbol 不去重 | due-set union + dedup + fan-out |
| P1-15 | callback 与 fetch loop 同步耦合 | producer/consumer 解耦 |
| P1-16 | PushChannel 与 QuoteStream 两套实时体系 | backend abstraction；push 仍 experimental |
| P1-17 | backpressure 更接近批内钳制 | bounded queue + drop/error policy |

## 4.4 P2：语义孤儿 / 兼容面

这里必须区分：当前 strict reachability 可以是 0，但仍存在以下“语义孤儿”：

- `SecurityConfig.credential_backend`：CredentialStore 已删除后仍像正式能力；
- `CompatibilityConfig` 部分字段缺乏运行时消费者证明；
- `BinaryClient / BridgeClient / HqClient` 等旧门面继续与主入口平级；
- Golden Replay 与 Performance Cache 都使用“cache/source”词汇，概念混淆；
- Synthetic source 如果进入默认 source order，有生产语义风险；
- PushChannel 是可达模块，但不在主 Streaming backend 中；
- 某些 integration 直接 client 调用使已有 Service/Facade 能力成为“旁路半孤儿”。

## 4.5 并行外部依赖项，不阻塞主重构

从 `POTENTIAL_ISSUES_AND_PLAN.md` 继续保留：

1. 真机协议定标：0x0530 反转族、0x0547 BJ、0xFC5 记录长、0x1300 diff、std7727 base 等；
2. golden 真实样本继续扩充；
3. 主站池外部候选发现与巡检；
4. Windows CI 完整矩阵；
5. 性能基线恢复；
6. native v1.5 强告警 / v1.6 删除既有时间线。

这些属于 **Calibration/Operations Track**，不应让 Facade/Router/Cache 的确定性问题继续等待。

---

# 5. 目标架构：增加 Composition Root，而不是继续增加 Facade

当前 v11 进一步加入一个缺失的关键抽象：`RuntimeContext`（名称可在实现时调整）。

```text
build_market_data_service(config)
          |
          v
+-------------------------------------+
| RuntimeContext                      |
| - immutable Config snapshot         |
| - SourceManager                     |
| - CacheManager / CachePolicy        |
| - SourceHealthRegistry              |
| - QueryExecutor / budgets           |
| - TaskManager                       |
| - Metrics/Events                    |
| - bounded executors                 |
+-------------------+-----------------+
                    |
                    v
          UnifiedMarketDataService
                    |
                    v
             QueryPlanner
                    |
          +---------+---------+
          |         |         |
          v         v         v
       TdxSource  WebSource  VipdocSource
          |
   protocol family adapters
  quotation/ex/goods/mac/f10
```

关键规则：

- Config 只在 composition root 解析一次，Source 不自行读取“另一份全局配置”；
- Service 拥有所有资源生命周期；
- Router/Planner 不 `new TdxClient()`、不 `new WebQuoteSession()`；
- Integration 不 `new TdxClient()`；
- 同一进程允许创建多个彼此隔离的 Service 实例，避免全局 singleton 测试污染；
- `close()` 必须有明确状态机，并等待/取消 in-flight 工作；
- 低层 `TdxClient` 仍可以独立直接使用，不强迫协议研究用户经过 Service。

建议目录：

```text
tstdx/application/
  capability.py
  query.py
  result.py
  policy.py
  planner.py
  service.py
  runtime.py

tstdx/sources/
  base.py
  manager.py
  health.py
  router.py
  tdx.py
  web.py
  vipdoc.py
  replay.py
```

不新建 `application/errors.py`：继续使用 `tstdx/errors.py`，避免第二套错误事实源。

---

# 6. Capability Registry：机器可读能力事实源

## 6.1 CapabilitySpec

```python
CapabilitySpec(
    name="bars",
    query_type="BarsQuery",
    result_type="Bar",
    sources={
        "tdx": SourceCapability(
            periods=("day", "1min", "5min", ...),
            adjustments=("raw",),
            supports_start=True,
            quality="exact",
        ),
        "web": SourceCapability(
            periods=(...),
            adjustments=("raw", "qfq", "hfq"),
            supports_start=False,
            quality="exact",
        ),
        "local": SourceCapability(
            periods=("day", "1min", "5min"),
            adjustments=("raw",),
            supports_start=True,
            quality="exact",
        ),
    },
)
```

必须声明：

- 输入参数 Schema；
- 输出域模型；
- source 支持情况；
- period / market / start/count / adjustment；
- `exact | approximate | emulated`；
- batch 上限或 provider 限制；
- 是否支持 Streaming；
- 与 Command Ledger 的映射；
- replacement/alternative（当能力不可用时）。

## 6.2 不把运行时健康写入 Capability

```text
CapabilityRegistry = 静态、可版本化、可生成文档
SourceHealthRegistry = 动态、可熔断、可恢复
```

这样可以防止“某 source 暂时超时”导致 API 文档突然认为它“不支持 bars”。

## 6.3 采用 AKShare 式 registry ratchet

建议新增：

```text
scripts/build_capability_registry.py
```

产生确定性文件，例如：

```text
tstdx/data/capabilities.json
```

包含：

```json
{
  "schema_version": 1,
  "capabilities": []
}
```

CI `--check` 必须验证：

```text
public high-level API - registry = 0
registry - public high-level API = 0
registry documented=false = 0
orphan docs = 0
service endpoints without capability = 0
```

允许在迁移期用 baseline 记录历史债务，但必须是**双向棘轮**：

- 不允许新增债务；
- 旧债修复后必须从 baseline 删除；
- baseline 自身过期也应使 CI 红灯。

---

# 7. QuerySpec / QueryResult / Canonical Contract

## 7.1 QuerySpec

所有入口最终只产生一个标准请求：

```python
QuerySpec(
    capability="bars",
    symbols=("sh600519",),
    period="day",
    count=320,
    start=0,
    adjustment="raw",
    route_policy="fresh",
    source=None,
    allow_approximate=False,
    max_age=None,
    deadline=None,
)
```

核心字段：

```text
query_id
capability
symbols
period/count/start
adjustment
route_policy
source                # explicit source；与 policy 分离
allow_approximate
freshness/max_age
strict/partial policy
deadline
metadata
```

不要继续用一个 `route` 字段同时表达：

- “只能走 tdx”；
- “优先本地”；
- “自动 fallback”。

这是三种完全不同的意图。

## 7.2 QueryResult

```python
QueryResult(
    data=...,
    meta=ResultMeta(
        source="tdx",
        quality="exact",
        cache_status="miss",
        stale=False,
        attempts=(...),
        warnings=(...),
        provenance={...},
    ),
)
```

替代：

```text
router.last_source
router.last_errors
```

实现收益：

- 请求级信息没有共享 mutable state；
- fallback 轨迹可审计；
- cache provenance 可见；
- 服务面可以统一暴露 diagnostics；
- 并发安全。

## 7.3 BatchResult：明确 partial failure

行情批量接口不能只有“全成功/全失败”。

建议：

```python
BatchResult(
    data={"sh600519": Quote(...), ...},
    errors={"sz000001": ErrorEnvelope(...)},
    partial=True,
    meta=...
)
```

契约：

- 保留调用方输入顺序；
- 内部可以 symbol dedup，但结果 fan-out 后语义不变；
- 单 symbol 失败不能被静默丢掉；
- `strict=True` 时 partial 可以提升为最终错误；
- REST/WS/MCP/Python 的 partial 语义完全一致。

## 7.4 Canonical Data Contract

所有 Source Adapter 必须在进入 Service 结果层前完成统一：

```text
Symbol / Market
Timestamp / timezone
Period
Price unit
Volume unit
Amount unit
Adjustment
Missing field semantics
Quality / provenance
```

避免 Source-specific dict 一路流到 API 层。

重要原则：

> “字段缺失”与“字段真实为 0”必须可区分。

对上游不存在的字段应使用 `None/metadata warning` 或明确 capability，而不是为了形状统一
静默填 0。

---

# 8. 路由策略：解决 auto 冲突而不是静默改变用户行为

当前 Facade 与 SourcesConfig 已存在不同 auto 顺序。不能在重构中直接选择一种然后悄悄改变
旧用户结果。

## 8.1 新 Service 使用命名策略

### `fresh`

```text
tdx -> web -> local -> replay
```

目标：优先在线新鲜数据。

### `prefer_local`

```text
local -> tdx -> web -> replay
```

目标：历史/离线文件优先。

### `offline`

```text
local -> replay
```

### explicit source

```python
source="tdx"
source="web"
source="local"
source="replay"
```

硬约束：

> explicit source 失败就失败，不允许跨 source 隐式 fallback。

## 8.2 旧 `auto` 的兼容迁移

不能直接把旧 `auto` 改成 `fresh`。

迁移：

1. Phase 0 先用 contract test 冻结现有 `UnifiedQuoteAPI(auto)` 行为；
2. 新 `MarketDataService` 使用明确 `fresh/prefer_local/offline`；
3. 旧 facade 的 `auto` 作为 `legacy_auto` 适配；
4. 文档逐步停止推荐 `auto`；
5. major release 才决定是否把 `auto` 明确 alias 到 `fresh`。

这样既能统一未来模型，也不在 minor 版本制造隐式行为变更。

## 8.3 exact / approximate / emulated

借鉴 CCXT 的 `emulated` 思想，tstdx 应显式区分：

```text
exact        原 source 同义结果
approximate  业务近似，但不是同一数据定义
emulated     通过其他能力组合计算得到
```

默认：

```python
allow_approximate=False
```

例如 TDX 板块行情与 Web 板块排行不能默认互相冒充。

---

# 9. ExecutionBudget：防止多层重试放大

当前系统存在 transport、Web adapter、Router、Facade 多层都可能重试/fallback 的条件。
如果每层独立使用自己的 timeout/retry，上层一个 3 秒请求可能被放大成几十秒。

新设计必须有**整次 Query 的总预算**：

```python
ExecutionBudget(
    deadline=monotonic() + 3.0,
    max_source_attempts=4,
    max_host_attempts=...,
)
```

规则：

- deadline 从 API 边界向下传；
- 每次 retry/fallback 消耗同一预算；
- transport timeout 不得超过 remaining deadline；
- Router 不在 transport 已重试后再无限重复同 Source；
- cancellation token 与 deadline 同步传播；
- 超出预算统一返回已有 timeout/availability 错误，不继续“最后再试一次”。

Policy 优先级：

```text
1. explicit source / ContractGuard
2. Capability support
3. ExecutionBudget
4. CommandLedger / SourceHealth
5. RetryAdvice
6. RoutePolicy fallback
```

`RetryAdvice` 是决策输入，不得覆盖 explicit source 的 fail-closed 规则。

---

# 10. 错误体系：保留 E1-E9，只统一错误信息和传播契约

## 10.1 不重写现有错误树

现有稳定资产：

```text
E1 Config/Input
E2 Transport
E3 Protocol
E4 Data/Domain
E5 File
E6 Stream
E7 Web/Source
E8 Compatibility
E9 Internal
```

并已有：

- 唯一 `code`；
- `http_status`；
- `RetryAdvice`；
- `context`；
- `cause`；
- `to_dict()`。

因此本轮不重新编号、不整体换父类、不让已有调用方重新 catch。

## 10.2 只处理真实重复类

当前 `sources` 内另有一个不同的 `SourceUnavailable`。

安全迁移：

```python
# tstdx/sources legacy import path
from tstdx.errors import SourceUnavailable
```

- canonical 类保持 `tstdx.errors.SourceUnavailable` / E7050；
- 旧 import path 可以先 re-export；
- 禁止新增第二个 code；
- contract test 断言两条 import path 是同一个 class object。

## 10.3 ErrorEnvelope：统一跨接口错误信息

在“不改变异常树”的前提下增加一个序列化视图：

```json
{
  "code": "E7050",
  "type": "SourceUnavailable",
  "message": "...",
  "http_status": 503,
  "retryable": false,
  "source": "tdx",
  "capability": "security_list",
  "query_id": "...",
  "request_id": "...",
  "attempts": [],
  "alternatives": [],
  "context": {}
}
```

Python：

- 低层 API 继续抛 `TdxError`；
- 高层 `query()` 可继续提供 `ApiResponse` 兼容面；
- 新高级 API 提供 `QueryResult/ErrorEnvelope`。

REST：

```json
{"ok": false, "error": { ...ErrorEnvelope... }}
```

WS/MCP：保留同一个 `code/type/message/context`，不能各自造字符串。

CLI：默认人类可读；`--json` 输出同一 envelope。

## 10.4 可 fallback 异常白名单

Router 只允许捕获**明确可恢复的 TdxError**，例如：

```text
ConnectionFailed / ConnectionClosed
ReadTimeout / WriteTimeout
AllHostsUnreachable
RateLimitedLocal / WebRateLimited
AntiSpiderBlocked
SourceUnavailable / SourceDeprecated
CommandOffline（仅当 policy 允许改 source）
DataFileNotFound（仅 auto/prefer_local 场景）
```

不得把以下异常当成 source unavailable：

```text
TypeError
AttributeError
AssertionError
unexpected KeyError
unexpected ValueError
```

边界层如果遇到未预期异常：

- 不进行 fallback；
- 记录完整 cause/trace；
- 对外转换成 `InternalError(E9000)` + request_id；
- 对外 context 做脱敏，禁止泄漏 token、cookie、本地隐私路径。

## 10.5 Error contract CI

必须验证：

```text
error code 唯一
每个 TdxError 有稳定 code
REST/WS/MCP/Python 同一错误 code 相同
http_status 映射确定
fallback policy 有显式测试
unexpected exception 不被降级
ErrorEnvelope 不泄漏敏感 context
```

错误文档应由 `errors.py`/registry 生成主要表格，减少手写漂移。

---

# 11. SourceManager：真正复用连接和运行状态

Router 以后只选择 Source，不负责创建 Source。

```text
SourceManager
 |
 +-- TdxSource
 |     +-- quotation client/pool
 |     +-- extended client/pool
 |     +-- goods client/pool
 |     +-- mac client/pool
 |     +-- f10 client/pool
 |
 +-- WebSource
 |     +-- persistent HTTP session(s)
 |     +-- provider rate/concurrency budget
 |
 +-- VipdocSource
 |
 +-- GoldenReplaySource
```

### 生命周期要求

```text
create -> lazy/open -> reuse -> draining -> closed
```

- 同一 Service 多次 query 复用 ConnectionPool；
- Web keep-alive 跨 query 复用；
- Source health/circuit 跨 query 保留；
- `close()` 后拒绝新请求；
- close 与 in-flight request 不发生“关闭一半连接又重新创建”的竞态；
- family client 由 SourceManager 管，不由 HTTP endpoint 管。

### 两层 circuit 不得混淆

```text
Source circuit = TDX/Web/Vipdoc 整体能力层健康
Host circuit   = TDX 某个 IP:port 的传输层健康
```

两层独立，Source Planner 不复制 ConnectionPool 的 host 选择逻辑。

---

# 12. Cache V2 + Freshness Policy

## 12.1 分离 Performance Cache 与 Replay Source

```text
QuoteCache/KlineCache = 执行优化
GoldenReplaySource    = 离线真实数据 Source
Synthetic             = test/benchmark only
```

不要再把三者放在同一默认 source list 中。

## 12.2 Cache Key

至少保留：

```text
capability
normalized symbol
period
adjustment/canonical raw model
source_scope（explicit source 必需）
query schema version
```

`start/count` 对时间序列优先在 canonical series 上切片，而不是把每个窗口都变成独立语义。

## 12.3 复权缓存

长期推荐：

```text
Raw Bars Cache
     +
Corporate Actions Cache
     |
     v
Adjustment Engine
```

而不是把 raw/qfq/hfq 任意写进同一 key 空间。

## 12.4 Freshness

行情 cache 必须显式支持：

```text
max_age
stale flag
allow_stale=False (default)
```

不得因 source 不可达静默返回无限陈旧 Quote。

若未来需要 stale-while-revalidate，也必须显式 opt-in，并在 `ResultMeta.stale=True` 标注。

## 12.5 Cache invariant

任何相同 QuerySpec：

```text
cache disabled
== cache miss
== cache hit
```

业务 data 完全一致；只允许 `meta.cache_status/provenance` 不同。

---

# 13. API 与服务接口优化

## 13.1 保留双层 API，而不是只暴露万能 query

低层协议：

```python
TdxClient
AsyncTdxClient
```

高层市场数据：

```python
service.quotes(...)
service.bars(...)
service.snapshot(...)
service.security_list(...)
service.query(QuerySpec(...))   # 高级/通用入口
```

`query()` 不是让普通用户手写字符串字典，而是作为所有便捷 API 的统一内核。

## 13.2 参数命名统一

所有入口统一：

```text
symbols
period
count
start
adjustment
source
route_policy
allow_approximate
strict
```

REST/WS/MCP 不再出现同一概念不同名字或不同默认值。

## 13.3 Batch API First

对 quote/snapshot/bars 批量请求：

1. API 层先 normalize；
2. dedup；
3. Planner 按 provider batch limit 分片；
4. 并发受 Source concurrency budget 控制；
5. 原输入顺序 fan-out；
6. 返回 `BatchResult` partial errors。

避免用户循环调用单 symbol 导致 N 次建连/路由/normalize。

## 13.4 REST 二进制内容

例如 file download 不应把任意 bytes 塞进 JSON。

明确为：

```text
application/octet-stream / StreamingResponse
```

如果业务必须 JSON，则使用显式 base64 schema，不能依赖 JSON 对 bytes 的隐式处理。

## 13.5 Integration 依赖规则

```text
HTTP/WS/MCP/CLI
       |
       v
MarketDataService
```

禁止 import：

```text
TdxClient
WebQuoteSession
GoodsClient
ExMarketClient
MacClient
F10Client
```

例外仅限明确命名并隔离的 raw/debug API，例如：

```text
/raw/tdx/*
```

---

# 14. 执行效率：先消除架构浪费，再优化 parser

## 14.1 连接复用

优先级最高：

- TDX persistent pool；
- Web persistent http client；
- host ranking/circuit 状态复用；
- 避免 query 级 new/close。

这类优化通常比微调 parser Python 指令更有收益。

## 14.2 SingleFlight / in-flight query coalescing

服务模式中，多个客户端可能同时请求完全相同的 Quote/Bars。

新增可选的 in-flight 合并：

```text
same canonical QueryKey
        |
        +--> one provider request
        |
        +--> N waiters receive same immutable result
```

要求：

- 只合并完全等价 Query；
- explicit source/adjustment/freshness 不同不能合并；
- waiter 自己的 cancellation 不取消其他 waiter，除非最后一个 waiter 退出；
- 失败结果短时间内不做永久负缓存。

## 14.3 BatchPlanner

Source capability 声明 provider limit：

```text
max_symbols_per_quote
max_bars_per_request
max_concurrency
rate_cost
```

Planner 自动 chunk，而不是每个 API 方法写一套“800 条/80 symbols”魔数。

## 14.4 Rate / Concurrency Budget

每个 Source 维护：

```text
rate budget
concurrency semaphore
retry budget
circuit state
```

避免：

- HTTP endpoint 自己限一次；
- Web adapter 再限一次；
- Router 再 sleep 一次。

限流必须有 deadline，禁止无上限 `while True` 等待。

## 14.5 Canonical normalize once

Symbol normalize、market infer、period normalize 在 Query 构造阶段只做一次。

Source Adapter 消费 canonical symbol，禁止再各自维护 `_guess_market/_split_code_market` 等平行逻辑。

## 14.6 数据转换

在 profile 证明为瓶颈后再做：

- `memoryview`/zero-copy payload；
- 减少 bytes slice；
- parser hot-path 局部优化；
- DataFrame lazy conversion；
- 避免 `model -> dict -> model -> dataframe` 往返；
- 批量域模型转换。

不把“零拷贝”作为牺牲协议校验或可读性的理由。

---

# 15. Async：共享逻辑，不共享危险 mutable facade

## Phase 0/1 短期安全化

现有 thread bridge 先做到：

- lazy init lock；
- lifecycle lock/state；
- bounded dedicated executor；
- close 等待 in-flight；
- close 后拒绝新请求；
- circuit/health state 线程安全；
- QueryResult 不使用 shared last_errors。

## 中期目标

```text
AsyncMarketDataService
 |
 +-- AsyncTdxSource
 +-- AsyncWebSource
 +-- sync-only VipdocSource -> bounded thread adapter
```

同步/异步共享：

```text
CapabilityRegistry
QuerySpec
QueryResult
RoutePolicy
CachePolicy
ErrorEnvelope
```

只有 IO Adapter 不同。

禁止同步/异步重新复制完整业务方法体。

---

# 16. Streaming v2：请求与订阅共享 Source Backend

## 16.1 Scheduler

```text
SubscriptionState
  - interval
  - next_due
  - symbols
  - subscriber
       |
       v
StreamScheduler
  - select due subscriptions
  - union + dedup symbols
  - batch fetch
  - DeltaMerger
  - fan-out
       |
       v
bounded dispatch queue
```

每个订阅自己的 interval 必须真实生效。

## 16.2 Backend

```text
StreamBackend
 +-- TdxPollingBackend (0x0530)
 +-- TdxPushBackend (0x0547, experimental)
 +-- WebPollingBackend
```

`PushChannel`：

- 要么实现 `TdxPushBackend` 接入同一 Scheduler/Result contract；
- 要么迁入 experimental；
- 不再作为第二套平行“正式 streaming framework”。

## 16.3 Backpressure

真正 producer/consumer：

```text
fetch producer -> bounded queue -> subscriber workers
```

支持明确 policy：

```text
drop_oldest
drop_newest
error
block_with_deadline
```

慢 callback 不得阻塞所有 symbol fetch。

---

# 17. TaskManager v2

替换 thread-per-task：

```text
TaskManager
 +-- ThreadPoolExecutor(max_workers=N)
 +-- bounded pending queue
 +-- Future
 +-- CancellationToken
 +-- TaskRecord
```

状态：

```text
pending
running
cancel_requested
cancelled
done
failed
```

规则：

- `cancel_requested` 在 worker 真正退出前继续计 active；
- running/cancel_requested task 不允许 delete；
- delete 只 purge terminal task；
- pending + running + cancel_requested 全部计入上限；
- Query deadline/cancellation 向 Source 传播；
- shutdown 进入 draining，不接新任务。

---

# 18. 可观测性：可诊断但控制高基数

建议指标：

```text
query_total
query_latency_seconds
source_request_total
source_success_total
source_failure_total
source_fallback_total
source_circuit_state
cache_hit_total
cache_miss_total
cache_bypass_total
singleflight_join_total
batch_request_size
stream_fetch_total
stream_drop_total
task_active
```

**Metrics 标签禁止直接带 symbol/request_id**，避免高基数爆炸。

推荐低基数 labels：

```text
capability
source
result
error_code
period_family
cache_status
```

详细信息放结构化日志/event：

```text
request_id
query_id
symbols/summary
capability
source
route_policy
period
adjustment
cache_status
fallback_count
attempts
error_code
```

可选 OpenTelemetry exporter 只能作为 extra，不增加核心硬依赖。

---

# 19. 文档、接口和架构门禁

## 19.1 Capability completeness

```text
public high-level capability - registry == empty
```

## 19.2 Registry/doc 双向棘轮

```text
registry capability without docs == 0
docs capability without registry == 0
service route without registry == 0
```

## 19.3 Explicit source invariant

```text
source=tdx    -> only TdxSource
source=web    -> only WebSource
source=local  -> only VipdocSource
source=replay -> only ReplaySource
```

失败也不能跨 source。

## 19.4 Cache equivalence

```text
cache off == miss == hit
```

矩阵：

```text
raw/qfq/hfq
start=0/start>0
day/1min/5min
tdx/web/local
explicit source/route policy
```

## 19.5 Service parity

同一 fake Source / QuerySpec：

```text
Python
Async
REST
WS
MCP
```

业务结果、error code、partial semantics 一致。

## 19.6 Architecture import guard

AST gate：

```text
integration/http*
integration/ws*
integration/mcp*
```

不得直接依赖具体 source client，只能依赖 `MarketDataService`/接口协议。

## 19.7 Semantic orphan audit

现有 reachability audit 继续保留，再新增：

```text
config field -> runtime consumer
capability -> service handler
service handler -> registry
deprecated API -> replacement/deprecation schedule
experimental API -> explicit namespace/status
```

解决“模块可达，但业务主链未接”的盲区。

---

# 20. 性能基准：从“有历史数字”变成持续门禁

必须恢复稳定 benchmark suite。

## 20.1 基准项

```text
symbol normalization throughput
parser representative payload throughput
quote single cold/warm
quote batch 10/100/1000
bars 320 cold/warm
bars cache hit/miss
TDX connection reuse ratio
Web connection reuse ratio
source failover latency
singleflight 100 waiters
stream 100/1000 symbols
REST throughput
WS throughput
MCP latency
```

## 20.2 场景维度

```text
cold
warm
cache hit
cache miss
failover
partial failure
```

## 20.3 性能预算

第一轮先建立 baseline，不立即用不现实阈值阻塞开发。

随后为关键指标建立回归预算，例如：

```text
median latency regression <= 10%
p95 regression <= 15%
throughput regression <= 10%
connection reuse must not regress
memory growth bounded
```

具体数字必须以稳定 CI runner 的重复测量为准，不凭文档拍脑袋。

性能优化不能通过：

- 关闭 integrity check；
- 减少结果字段；
- 静默改用 approximate source；
- 降低测试强度。

---

# 21. 并行治理 Track：真机定标 / 主站池 / CI

这些工作与主架构并行推进。

## Track R：Protocol Real-machine Calibration

目标：

- 收盘后/可用窗口采集真实 payload；
- 对 5 个已知悬案定标；
- golden 固化；
- Command Ledger 更新必须有证据；
- 禁止“为了测试绿”猜字段布局。

## Track H：Host Operations

继续：

- `scripts/audit_hosts.py`；
- 5 族主站巡检；
- external candidate injection；
- RankingStore；
- CI 定期 job。

新增主站不直接 hardcode 进入默认池，先经过可用性/协议族/稳定性证据。

## Track Q：Quality/Release

继续：

- Windows 多版本矩阵；
- `scripts/` 也进入 format/lint；
- coverage 向 80% 提升，但不删除低覆盖重要代码来“做数字”；
- capability registry check；
- docs build/link check；
- benchmark gate；
- package install smoke；
- native 既定 deprecation timeline。

---

# 22. 实施路线：主轨 + 并行轨

## Phase 0 — Correctness Freeze（先修确定性问题）

目标：不大改架构，先消除可以明确证明的错误。

完成：

1. `M5 -> MinBarReader`；
2. local `.lc1/.lc5` 接到统一 facade；
3. cache 对 `start/adjust/explicit source` 先安全 bypass；
4. duplicate `SourceUnavailable` alias 到 E7050；
5. explicit source fail-closed；
6. `_get_router`/config propagation 修复；
7. `adjusted_bars(events=[])`；
8. REST snapshot/auction signature；
9. TaskStore cancel/delete resource bug；
10. broad exception fallback 收窄；
11. 为现有 `auto` 行为加 compatibility fixture。

出口：

- 原测试全绿；
- 新 P0 regression 全绿；
- protocol ledger 不变化；
- cache 语义正确优先于 cache hit rate。

## Phase 1 — Application Contract

新增：

```text
CapabilityRegistry
QuerySpec
QueryResult / BatchResult
ErrorEnvelope
RoutePolicy
ExecutionBudget
```

首批迁移：

```text
quotes
bars
snapshot
minute
trades
security_list
```

出口：这些能力不再在 facade 内各自手写 fallback。

## Phase 2 — RuntimeContext + SourceManager + Planner

完成：

- composition root；
- persistent TDX/Web；
- SourceHealthRegistry；
- Canonical Router/Planner；
- family client 生命周期统一；
- 请求级 mutable router state 删除；
- legacy facade 只做 adapter。

出口：循环调用能观测到 TCP/HTTP connection reuse。

## Phase 3 — Cache V2 + Batch Planner + SingleFlight

完成：

- semantic CacheKey；
- provenance；
- freshness；
- raw adjustment model；
- GoldenReplaySource；
- provider batch limits；
- request coalescing。

出口：Cache invariant + batch parity 全绿。

## Phase 4 — Service Surface Unification

完成：

```text
HTTP -> Service
WS   -> Service
MCP  -> Service
CLI  -> Service（高层命令）
```

保留低层 `TdxClient`/raw debug 明确入口。

出口：AST import guard + service parity gate。

## Phase 5 — Async / Streaming / TaskManager

完成：

- async lifecycle；
- bounded executor / native async adapter；
- StreamScheduler；
- symbol dedup；
- polling/push backend；
- backpressure dispatch；
- TaskManager cancellation。

出口：并发/取消/close stress 全绿。

## Phase 6 — Compatibility / Orphan / Docs

完成：

- 旧 Facade 移 `tstdx.compat` 或成为 thin adapter；
- 清理 orphan config；
- Synthetic 隔离到 testing/experimental；
- PushChannel 状态明确；
- capability/API docs 从 registry 生成；
- README/DESIGN/Feature Map 同步；
- 历史优化计划标记 archived/superseded，避免再次出现多个“当前方案”。

并行持续：Track R/H/Q。

---

# 23. 推荐提交拆分

不要一次提交一个巨大“架构重构”。建议：

```text
1. fix: close local reader and cache semantic gaps
2. fix: enforce explicit source and canonical source errors
3. fix: align REST contracts and bounded task lifecycle
4. refactor: add application query and capability contracts
5. refactor: introduce runtime context and source manager
6. refactor: route core queries through canonical planner
7. refactor: introduce semantic cache and batch planner
8. refactor: unify HTTP WS MCP service execution
9. refactor: harden async streaming and task scheduling
10. chore: enforce registry architecture benchmark and docs gates
```

每个提交都必须可单独测试和回滚。

---

# 24. Definition of Done

## 数据正确性

- [ ] `.day/.lc1/.lc5` Reader/Source/Service 对拍一致；
- [ ] cache 不改变 `start/count`；
- [ ] cache 不混 raw/qfq/hfq；
- [ ] explicit source 不跨源；
- [ ] approximate/emulated 默认不会静默使用；
- [ ] missing field 与真实 0 可区分；
- [ ] batch partial failure 不丢错误。

## 架构

- [ ] 单一 CapabilityRegistry；
- [ ] 单一 QuerySpec；
- [ ] 单一 QueryResult/ErrorEnvelope；
- [ ] 单一 QueryPlanner；
- [ ] 单一 RuntimeContext/SourceManager 生命周期；
- [ ] E1-E9 为唯一错误树；
- [ ] Facade/Router/Integration 不再多套路由。

## 生命周期/效率

- [ ] TDX ConnectionPool 跨 query 复用；
- [ ] Web HTTP session 跨 query 复用；
- [ ] Source health 跨 query 复用；
- [ ] close/in-flight 安全；
- [ ] total deadline 防 retry amplification；
- [ ] batch planner 自动 chunk；
- [ ] singleflight 正确合并等价查询；
- [ ] symbol normalize 只做一次主流程。

## 服务面

- [ ] REST/WS/MCP 高层 API 只依赖 Service；
- [ ] Goods/Ex/Mac/F10 默认服务可用或明确 capability unavailable；
- [ ] Python/Async/REST/WS/MCP error code 一致；
- [ ] REST binary download 契约正确；
- [ ] raw/debug endpoint 明确隔离。

## Async / Streaming / Tasks

- [ ] async first-use/close/circuit 无竞态；
- [ ] subscription interval 独立生效；
- [ ] due symbols 合并去重；
- [ ] push/poll 共用 backend contract；
- [ ] callback 不阻塞 fetch scheduler；
- [ ] cancel 不绕过 task limit；
- [ ] 无 orphan execution。

## 治理

- [ ] public API ↔ registry ↔ docs 双向一致；
- [ ] semantic orphan audit 绿色；
- [ ] architecture import gate 绿色；
- [ ] Windows 矩阵达到既定支持版本；
- [ ] benchmark baseline 和回归预算存在；
- [ ] protocol 实机悬案有独立 track，不用猜测代码“修复”；
- [ ] 历史计划不再与 v11 同时标记 Current。

---

# 25. 明确不做

为避免从“多套路由”走向“过度平台化”，本轮明确不做：

1. 不把 tstdx 改造成实盘交易系统；
2. 不重写已验证 protocol/codec/golden；
3. 不重新设计整棵 E1-E9 错误树；
4. 不引入重型 DI framework；
5. 不引入 Redis/Kafka 作为核心必需组件；
6. 不为“统一”删除低层 `TdxClient`；
7. 不为了性能跳过完整性校验；
8. 不让 Synthetic 进入真实行情默认 fallback；
9. 不让 approximate source 静默替代 exact source；
10. 不在 minor 版本静默改变 legacy `auto` 的数据源顺序。

---

# 26. 最终产品架构定位

重构完成后，tstdx 应明确分成两层：

### Low-level Protocol SDK

```text
TdxClient / AsyncTdxClient
Reader
Protocol / Codec / CommandLedger
```

面向：协议研究、低层调用、调试、兼容。

### Unified Market Data Runtime

```text
UnifiedMarketDataService
CapabilityRegistry
QuerySpec / QueryResult
QueryPlanner
SourceManager
CachePolicy
StreamingScheduler
```

面向：业务应用、CLI、REST、WS、MCP、多数据源统一访问。

新增一个新的 Source 时，正常流程只应：

1. 实现 Source Adapter；
2. 注册静态 Capability；
3. 在 SourceManager 注册生命周期 factory；
4. 提供 canonical transform；
5. 增加 contract fixture / benchmark；
6. 运行 registry/doc check。

**不应该再修改：**

```text
Facade route if/else
HTTP fallback if/else
WS direct client dispatch
MCP use_facade flag
多个独立 retry loop
多个 cache source order
```

这就是本轮融合后的唯一执行标准：

> **协议层保持稳定，高层执行层彻底收敛；错误、接口、数据源、缓存、并发、实时、
> 文档和性能都由同一组机器可验证契约约束。**

---

## Appendix A — 方案来源与保留项

### 从 v6 保留

- 连接/HTTP session 复用；
- Command Ledger SSOT；
- Web provider 生命周期收口；
- 数据 normalize SSOT；
- 文档进入 CI；
- 兼容 Facade 收敛；
- 不过度引入 native/重型依赖。

### 从 INDUSTRIAL 深审保留

- 并发竞态必须用 stress test 验证；
- 真机协议定标不能靠猜；
- symbol/market 必须单一事实源；
- retry/timeout 必须有上限；
- 可观测性必须真正接到主请求链。

其中已在后续版本修复的旧问题仅保留历史证据，不进入活跃 backlog。

### 从 v10 保留

- 已完成的模块拆分和错误树审计视为稳定成果；
- CredentialStore 删除决议继续有效；
- 已完成 ruff/mypy/可达性治理不回退。

### 从 POTENTIAL_ISSUES 保留

- 真机定标 Track；
- host audit Track；
- Windows CI；
- coverage/benchmark；
- native deprecation timeline。

### v11 新增

- RuntimeContext/composition root；
- Capability 与 Health 分离；
- QueryResult/BatchResult；
- ErrorEnvelope（不重写错误树）；
- named route policies + legacy auto migration；
- ExecutionBudget 防重试放大；
- FreshnessPolicy；
- BatchPlanner；
- SingleFlight；
- metrics cardinality guard；
- semantic orphan audit；
- AKShare 式 registry/docs 双向 ratchet；
- 主轨 + 真机/主站/质量并行轨道。
