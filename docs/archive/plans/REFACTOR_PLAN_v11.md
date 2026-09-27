# atst 架构审计与重构方案 v11（2026-09-12）

> 状态：**审计完成 / 待按批次实施**
>
> 审计基线：`main@7720c04952d9092fc24bf63a2225c381618e151c`
>
> 对照重构分支：PR #1 `refactor/industry-benchmark-v12@e252d2ec7807af5be25f6191ab18ef09ecee6fef`
>
> 本文承接 `REFACTOR_PLAN_v10.md`，但不是对 v10 的简单增量。2026-09-09 之后 `main` 又连续增加 Web/Fund/基本面/期货/债券/期权等能力，而 PR #1 同时形成 Provider-first runtime。两条线已经在**运行时语义、缓存语义、错误边界和流式生命周期**上分叉，因此 v11 重点从“拆大文件”升级为“统一运行时契约并重新贯通主体链路”。

---

## 0. 结论先行

### 0.1 项目主体功能是否已经实现？

结论：**大部分业务能力已经有真实实现，但不能判定为“核心功能全部完成”。**

已经比较成熟的部分：

- TDX 5 套协议族、命令账本、L1/L2/L3 解析分派；
- 同步/异步客户端、连接池、主站治理与协议请求；
- A 股行情/K 线/分时/逐笔/F10/财务/除权等核心数据；
- Web 多数据源与大量扩展域；
- vipdoc 本地读取、输出、配置、观测、CLI；
- HTTP / WS / MCP 服务面；
- QuoteStream / AsyncQuoteStream 轮询流；
- 基础缓存与多源路由。

仍不能视为“完成”的核心工程能力：

1. `main` 与 PR #1 对“Provider 是否允许跨源自动降级”存在根本冲突；
2. `main` 尚未拥有统一 `QuerySpec -> QueryPlan -> Provider -> QueryResult` 执行契约；
3. 缓存没有强制 Provider/Channel/Capability/Freshness provenance，存在“缓存值看起来像实时直连值”的语义风险；
4. Streaming 在 `main` 没有显式 `StreamState` 状态机，对 worker 异常退出、部分启动失败、停止超时后的重启约束仍弱；
5. CLI / REST / WS / MCP / background task 尚未统一到同一个安全 `ErrorEnvelope`；
6. Batch / SingleFlight / negative cache 等运行时能力仅在 PR #1 体系内形成，未进入当前主线；
7. 最新 `main` Web 能力扩张速度很快，很多新增路径主要由 FakeHttpClient 离线测试覆盖，真实 Provider contract / live smoke 证据不足；
8. 5 个 TDX 真机定标悬案尚未清零；
9. 当前最新主线 GitHub Actions CI 失败发生在 runner/step 执行之前，缺少远端可证明的绿色基线。

因此更准确的状态是：**业务功能面很宽，协议底座较成熟，但统一运行时与端到端契约尚未最终收口。**

### 0.2 主体链路是否全部贯通？

结论：**“旧架构主链路”基本贯通；“目标统一运行时链路”没有全部贯通。**

当前可以认为贯通的旧链：

```text
UnifiedQuoteAPI
  -> RouteSelector / DataSourceRouter
  -> TDX / Web / reader / golden cache
  -> Client / WebSource
  -> transport / HTTP
  -> protocol parser / web parser
  -> domain model
  -> list / ApiResponse / output
```

但目标链：

```text
API/CLI/REST/WS/MCP
  -> QuerySpec
  -> Planner
  -> exactly-one Provider + Channel
  -> Executor
  -> Freshness / Health / Cache / SingleFlight
  -> canonical QueryResult + Provenance
  -> ErrorEnvelope
  -> Surface adapter
```

目前主要存在于 PR #1，而不是最新 `main`。这就是 v11 的核心重构对象。

### 0.3 是否应该直接合并 PR #1？

**不建议。**

PR #1 从 `66734fb` 分叉，当前包含 737 commits / 234 changed files；与此同时 `main` 在共同祖先之后又新增 7 个提交，包含大量新的 Web/Fund/基本面/衍生品能力。两支已是 `diverged` 状态。

推荐：

- 保持 PR #1 Draft、不合并、不降低门禁；
- 把 PR #1 视作“Provider-first 参考实现与测试资产”；
- 从最新 `main` 新建 v11 integration branch；
- 按契约层切成多个小 PR，逐段移植 PR #1 的实现与门禁；
- 每个小 PR 都要求当前 `main` 新增业务域不丢失、不回退。

---

## 1. 当前主要功能地图

### 1.1 协议与连接层

| 模块 | 主要能力 | 当前评价 |
|---|---|---|
| `codec/` | TDX framing、primitive、zlib、数值编码 | 成熟，属于稳定内核 |
| `protocol/` | 命令账本、注册表、L1/L2/L3 dispatch、5 协议族解析 | 成熟，但仍有真机定标悬案 |
| `transport/` | sync/async transport、pool、lease、限流、主站测速/嗅探/健康 | 主体成熟，应保持稳定边界 |
| `client/` + `client_core.py` | `TdxClient` / `AsyncTdxClient`、多协议族客户端、模型转换 | 主体成熟，但需要接入统一 Provider contract |

当前 TDX 请求主链大致为：

```text
Client method
 -> request builder
 -> pool.acquire / connection lease
 -> transport.request
 -> frame decode / zlib / seq validation
 -> protocol.registry.dispatch
 -> parser
 -> rows
 -> client_core model conversion
```

这一层应该继续作为 **TDX Provider 内部实现**，而不是被新的 Provider runtime 重写。

### 1.2 Domain 层

主要包括：

- symbol 标准化与市场识别；
- Quote / Bar / Tick / MinutePoint 等模型；
- 复权；
- 交易日历；
- 财务/公司行为相关辅助能力。

Domain 层总体方向正确，但未来需要补充两个真正跨 Provider 的核心对象：

- `Provenance`：数据来自哪个 Provider / Channel / endpoint / cache tier；
- `Freshness`：当前值的采集时间、有效时间、是否 direct/live/replay/synthetic。

### 1.3 Web 数据源层

`web/` 已从“TDX 的补充来源”发展成独立的大型数据平台，覆盖：

- 腾讯 / 新浪 / 东财行情；
- 资金流、板块、龙虎榜、异动、热度；
- 港股、美股、外汇；
- 基金；
- 期货、债券；
- 财务、估值、公告、股东变化；
- 资讯、研报、调研；
- ETF/股指期权等最新扩展。

2026-09-09 ~ 09-12 的 `main` 又增加了几十个 API。此时继续把 Web 视为单个 `web` route 已不合理：**Web 是传输形态，不是 Provider 身份。**

未来应明确区分：

```text
Provider: eastmoney / tencent / sina / jsfund / boc / tdx / local_vipdoc ...
Channel: http_push2 / http_datacenter / http_mobile / tdx_7709 / vipdoc ...
Capability: quotes / bars / fund_holdings / option_snapshot / ...
```

### 1.4 Facade 与路由层

当前：

- `facade/api.py` 约 106 KB；
- `facade/routing.py` 提供 `auto/local/tdx/web` 路由与熔断；
- `sources/__init__.py` 约 25 KB，提供 `tdx -> web -> reader -> cache -> synthetic` 五级降级；
- quotes / bars 已将部分取数委托给 DataSourceRouter；
- 其他大量业务方法继续直接在 facade 内编排具体 Source。

这说明 v9/v10 虽然消除过一轮重复路由，但随着 Web 能力增长，**Facade 又开始承担 capability orchestration**。

根因不是“文件太大”，而是缺少稳定的 Provider/Capability Registry 与 Execution Runtime。

### 1.5 Streaming

当前 Streaming 已真实接入：

- `DeltaMerger`；
- `BackpressureQueue`；
- `ReconnectPolicy`；
- 缺失检测；
- sync thread / async coroutine 两套入口；
- 回调错误隔离；
- stop timeout 防止直接丢 thread 引用；
- TDX 轮询式 quote stream。

但 `main` 的生命周期仍是 `_thread + _stop Event` 隐式状态，而不是显式状态机。需要覆盖：

```text
CREATED -> RUNNING -> STOPPING -> CLOSED
                    \-> FAILED
```

并锁定：部分 worker 启动失败、worker 意外退出、stop timeout、FAILED 后 restart、重复 start、subscribe after failed 等行为。

### 1.6 Cache

当前有：

- `KlineCache`：SQLite；
- `QuoteCache`：进程内 TTL；
- golden replay；
- reader/local 等离线数据。

当前 Cache 是“数据结构缓存”，不是“语义缓存”。key 主要围绕 symbol/period/symbol-list；没有强制携带：

```text
provider
channel
capability
request fingerprint
currentness/freshness mode
observed_at / valid_at
provenance kind: direct/cache/replay/synthetic/fallback
```

这会阻碍 Provider-first 语义建立。

### 1.7 服务与工具层

已有：

- CLI；
- HTTP REST；
- WebSocket JSON-RPC；
- MCP；
- observability；
- capture / codegen / spec audit / golden audit / reachability / originality；
- output/sink；
- feedback。

功能存在不等于边界一致：目前这些入口对异常、安全错误输出、后台任务错误保存等处理并非同一个 SSOT。

---

## 2. 当前架构中不合理的地方

### P0-1：运行时语义冲突——自动跨源降级 vs Provider fail-closed

`main` 当前公开设计明确允许：

```text
local -> tdx -> web
TDX -> web -> reader -> cache -> synthetic
```

PR #1 则明确要求：

- 一个 QueryPlan 只执行一个 Provider；
- `provider=` / `source=` 解析成唯一 Provider；
- 不允许跨 Provider 静默 fallback；
- cache/replay/synthetic 不能冒充 direct/live。

这两者不能同时作为默认语义。

**v11 推荐决策**：

- Provider execution 永远 fail-closed；
- “多 Provider fallback”如果保留，升级为**显式 OrchestrationPolicy**，调用者主动选择，例如 `policy="fallback"`；
- fallback 每一步必须返回/记录完整 provenance 与 `attempts`，不可静默；
- 默认 `policy="strict"`，TDX 作为默认 Provider；
- TDX Provider 内部 host failover 继续允许，因为它没有跨 Provider。

这既保留当前用户需要的高可用能力，也不会破坏 Provider 身份和数据可信度。

> **需要最终产品拍板的唯一重大语义项**：默认是否采用 `strict`。本文按推荐值 `strict` 制定后续方案；如果最终决定默认 fallback，只调整默认 Policy，不改变 Provider runtime 的 fail-closed 内核。

### P0-2：PR #1 体量过大，已经不适合作为最终合并单元

现状：

- 737 commits；
- 234 changed files；
- 与最新 main diverged；
- main 还有 7 个 PR #1 不包含的新提交。

继续 rebase 一个 700+ commit 的长期 Draft PR，会把“架构迁移问题”和“业务功能冲突”混成一次不可审计的大合并。

**解决**：PR #1 保留为参考与行为规范；从最新 main 开始切新 stacked PR。

### P0-3：没有可靠的远端绿色基线

最新 `main@7720c049` 的 CI run `34664281919` conclusion=failure，但现象是确定性 jobs 在真正执行 source steps 前就失败/未分配 runner。

因此当前状态必须区分：

- “本地/提交说明中 pytest 2400+ 通过”；
- “GitHub Actions 对同一 SHA 的确定性门禁已实际运行并全部绿色”。

第二项目前没有成立。

任何重构开始前必须先得到**同一 SHA、实际有 steps 的绿色基线**。

### P0-4：缓存 provenance 不够强

当前 KlineCache / QuoteCache 只保证缓存数据结构与 TTL，不保证：

- 它是否来自用户指定 Provider；
- 是否来自 direct/live；
- 是否经过 fallback；
- 是否是 replay/synthetic；
- 是否满足当前 freshness contract；
- 是否与 Query capability/channel 完全相同。

这是数据系统比“缓存命中正确”更严重的问题：**值可能数值正确，但语义错误。**

### P0-5：Streaming 缺显式生命周期状态机

main 的 `start()` 主要检查 thread alive，`stop()` 主要依靠 Event + join。已经修过孤儿双跑问题，但仍无法清晰表达：

- worker 初始化失败后实例是什么状态；
- unexpected exit 后能不能 restart；
- dispatcher 与 poll worker 谁先失败时如何收口；
- FAILED 实例是否还能订阅；
- 部分启动成功时 stop 应 join 哪些 worker。

这是典型需要状态机而不是继续打补丁的问题。

### P0-6：错误模型有“异常树”，但没有真正统一“边界 Envelope”

`errors.py` 的 E1-E9 分类是好基础；问题在于外部入口不只是需要异常类，还需要稳定、安全、可序列化的边界协议：

```text
code
message
safe context
retryability
request/query identity
provider/channel
cause class (safe)
http/jsonrpc mapping
```

CLI/REST/WS/MCP/TaskStore 必须调用同一个 builder，不应各自转换。

### P1-1：Facade 重新变成 God Object

`facade/api.py` 虽已拆 `routing.py`，但当前文件约 106 KB，并持续增加跨域方法。

问题不是 LOC 本身，而是：

- API surface；
- capability 选择；
- Provider 创建；
- 业务参数转换；
- fallback；
- 资源关闭；
- 一些 Domain orchestration

仍可能同时出现在 Facade。

目标：Facade 只做参数兼容、QuerySpec 构造与返回形态适配。

### P1-2：DataSourceRouter 职责混杂

`sources/__init__.py` 同时承担：

- source 顺序；
- source enable；
- 构建 TdxClient/WebQuoteClient；
- golden replay；
- cache read/write；
- model conversion；
- exception classification；
- fallback；
- last_source/last_errors mutable state。

应该拆成：Provider Adapter + Policy + Executor + Cache，而不是继续扩展 Router。

### P1-3：`web` 作为单一 route 已失真

Eastmoney/Tencent/Sina/JSL/BOC 等之间：

- 数据口径不同；
- capability 不同；
- 限流/反爬不同；
- freshness 不同；
- endpoint 风险不同。

将它们都压成 `route="web"` 会丢掉对生产环境最重要的 provenance。

### P1-4：Async facade 以 `asyncio.to_thread` 镜像同步门面，长期不宜继续扩大

作为兼容层可以保留，但 Provider runtime 应有真正的 sync/async execution contract，避免：

- 线程池行为不可见；
- cancellation 无法正确传递到底层；
- deadline/backpressure 难统一；
- sync/async 入口产生不同错误时序。

### P1-5：最新 Web 能力存在“接口已实现 ≠ 生产 Provider 已验证”

最新 main 的期权提交说明中，22 个新增测试使用 FakeHttpClient；提交同时明确探测到部分 push2 分笔/委托/L2 endpoint 为 404，无法扩展。

因此能力矩阵必须至少分四态：

- `implemented`：代码存在；
- `contract-tested`：Provider contract 测试通过；
- `live-smoke`：真实 provider smoke 通过；
- `production-ready`：错误、限流、schema drift、freshness 均有门禁。

不能再用单一 ✅ 表示全部状态。

### P1-6：真机定标悬案仍在

历史审计仍保留 5 个需真机数据确认的协议项。它们属于“帧可以合法解析但字段可能错误”的高风险类别，不能被单元测试数量掩盖。

### P1-7：Batch/SingleFlight/negative cache 未成为 main 的稳定运行时能力

PR #1 已形成较完整的：

- BatchResult status；
- deep-copy isolation；
- SingleFlight follower isolation；
- terminal-only negative cache；
- full fingerprint key。

主线目前没有统一接入这些能力，应作为 Executor 的组成部分迁回，而不是零散放到 Facade。

### P2-1：文档版本和状态标签容易造成误判

README 当前仍以 v1.0.0 为稳定发布版本，同时历史计划正文包含 v1.4.0/v1.5.0 批次标记；最新 main 又有未发布能力。建议后续将文档分为：

- `released`；
- `main-unreleased`；
- `experimental/draft`。

所有 feature matrix 都显示 `release_since` 与 readiness。

---

## 3. 核心链路贯通审计

| 链路 | main | PR #1 | v11 判断 |
|---|---|---|---|
| TDX sync 请求/解析 | ✅ | ✅ | 保留 main 稳定实现 |
| TDX async | ✅ | ✅ | 保留，并接统一 runtime |
| 连接池/host failover | ✅ | ✅ | 只允许在 TDX Provider 内部 |
| Facade quotes/bars | ✅ | ✅ 已计划化 | 需迁到 QueryPlan |
| 多 Web Source | ✅ | Provider 化 | 需拆真实 Provider identity |
| local vipdoc | ✅ | local-only 约束更强 | 作为 Local Provider |
| cache | ✅ 基础缓存 | ✅ provenance/cache_v2 | 迁移 PR #1 语义 |
| streaming | ✅ 可用 | ✅ 显式 StreamState 强化 | main 尚未最终收口 |
| batch | 零散/接口级 | ✅ Batch runtime | 未贯通 |
| SingleFlight | 无统一 SSOT | ✅ | 未贯通 |
| negative cache | 无统一 SSOT | ✅ | 未贯通 |
| Direct Provider API | 无稳定 SSOT | ✅ | 未贯通 |
| ErrorEnvelope | `TdxError.to_dict` 等局部能力 | ✅ 多入口统一 | 未贯通 |
| REST | ✅ | ✅ envelope 强化 | 需统一边界 |
| WS | ✅ | ✅ envelope 强化 | 需统一边界 |
| MCP | ✅ | ✅ envelope 强化 | 需统一边界 |
| background task | ✅ 基础能力 | ✅ envelope/memory clamp | 需统一边界 |
| live provider contract | 部分 | 部分 | 不足以证明全部生产就绪 |
| CI deterministic gates | 当前 run 未实际执行到 source step | 同类 runner provisioning 问题 | 不能判绿 |

结论：**不能用“2400+ tests passed”推导“主体目标链全部贯通”。** 测试数量证明回归面较大，但目标 runtime 的 SSOT 是否贯通要靠 contract graph 与入口对拍门禁证明。

---

## 4. v11 目标架构

### 4.1 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ Surface                                                      │
│ Python Facade / Async / CLI / REST / WS / MCP               │
├──────────────────────────────────────────────────────────────┤
│ Query Contract                                               │
│ QuerySpec / QueryFingerprint / Capability / Currentness     │
├──────────────────────────────────────────────────────────────┤
│ Planning                                                     │
│ ProviderRegistry / CapabilityRegistry / Planner / Policy    │
├──────────────────────────────────────────────────────────────┤
│ Execution                                                    │
│ Executor / AsyncExecutor / Batch / SingleFlight / Deadline  │
├──────────────────────────────────────────────────────────────┤
│ Runtime Cross-cutting                                        │
│ Freshness / Provenance / Health / Cache / NegativeCache     │
├──────────────────────────────────────────────────────────────┤
│ Providers                                                    │
│ TDX / Eastmoney / Tencent / Sina / JSL / BOC / LocalVipdoc │
├──────────────────────────────────────────────────────────────┤
│ Provider internals                                           │
│ transport / protocol / web http / reader / domain normalize│
└──────────────────────────────────────────────────────────────┘
```

### 4.2 核心契约

#### QuerySpec

必须是 immutable / hashable，并包含足够生成完整 fingerprint 的字段：

- capability；
- symbols；
- period/range/count；
- adjustment；
- provider；
- channel（可选显式）；
- freshness/currentness；
- provider-specific options 必须进入 fingerprint 或明确禁止影响语义。

#### QueryPlan

硬约束：

- 一个 plan = 一个 Provider；
- 一个 canonical Channel；
- 明确 cache policy；
- 明确 freshness policy；
- 明确 batch limit；
- 明确 deadline；
- 不允许 plan 执行期间偷偷换 Provider。

#### QueryResult

统一返回：

```text
value/data
provider
channel
capability
provenance
observed_at
valid_at/freshness
cache_status
request_fingerprint
attempt metadata
```

Facade 如果为了兼容继续返回 `list[Quote]`，也应从 QueryResult 解包，而不是绕过 runtime。

### 4.3 Provider Registry

Provider Registry 是新的单一事实源，统一回答：

- Provider ID；
- 支持哪些 Capability；
- 每个 Capability 使用哪个 Channel；
- sync/async adapter；
- max batch；
- live/historical/local 属性；
- freshness 能力；
- health key；
- Direct Provider API 映射。

严禁 Facade、CLI、Provider API 各维护一份 Provider 列表。

### 4.4 Explicit fallback Policy

推荐 API：

```python
quotes(..., provider="tdx", policy="strict")
quotes(..., provider="tdx", policy="fallback", fallback=["tencent", "eastmoney"])
```

`policy="strict"`：Provider 不可用直接失败。

`policy="fallback"`：由 Orchestrator 创建**多个独立 QueryPlan**顺序执行；每一个 plan 本身仍 fail-closed。最终结果 provenance 必须显示：

- requested_provider；
- selected_provider；
- fallback_attempts；
- 每次失败安全摘要。

这样把“高可用策略”和“数据身份”彻底分开。

### 4.5 Streaming Runtime

StreamState：

```text
CREATED
  -> RUNNING
  -> STOPPING
  -> CLOSED
  -> FAILED (terminal unless explicitly reconstructed)
```

至少两个 worker 概念：

- poll/provider worker；
- dispatch worker。

必须锁定：

- `start()` 幂等条件；
- 部分启动失败 cleanup；
- worker unexpected exit -> FAILED；
- stop 只 join 实际启动 worker；
- stop timeout 不允许第二个 dispatcher；
- FAILED 拒绝 subscribe/restart；
- callback slow/failure 不改变 provider worker identity；
- sync/async 生命周期行为对拍。

### 4.6 Semantic Cache

缓存内容不再是裸 rows，而是 canonical QueryResult。

L1/L2 persistent cache 写入前后都校验：

- exact fingerprint；
- provider/channel/capability；
- provenance kind；
- currentness/freshness；
- schema/version；
- observed_at；
- live/replay/synthetic eligibility。

negative cache：

- 只缓存 terminal failure；
- `SourceUnavailable` 等 transient error 禁止缓存；
- full fingerprint key；
- TTL 短；
- 命中时 clone，并在 provenance/error metadata 标记 negative-cache-hit。

### 4.7 ErrorEnvelope

所有边界只允许一个构造入口，例如：

```text
build_error_envelope(exc, query_context, surface)
```

约束：

- native unknown exception -> E9000；
- 不泄露内部栈、文件路径、secret；
- TdxError 保留 code + safe context；
- HTTP status / JSON-RPC code 与 domain code 分离；
- KeyboardInterrupt/SystemExit 不吞；
- TaskStore 保存 envelope，不保存任意 Exception；
- WS notification failure 不产生不合法 response。

---

## 5. 重构实施计划

### Phase 0：先恢复可验证基线

目标：在任何 runtime 大迁移前，先证明最新 main 自身可重复构建。

1. 固定 `main@7720c049` 基线；
2. 刷新 CI，直到 runner 实际执行 checkout/ruff/mypy/pytest 等 steps；
3. 不把 `steps=null` 当源码失败；
4. 本地 `make gates` 与 CI command 完全一致；
5. 保存同 SHA 的 gate manifest；
6. 最新 Web 增量必须纳入测试矩阵。

出口：**一个相同 SHA 上的确定性门禁全部实际执行且绿色。**

### Phase 1：建立 Contract SSOT，不改变业务实现

新增/迁移：

- QuerySpec / QueryFingerprint；
- Capability；
- Provider/Channel registry；
- QueryPlan；
- QueryResult / Provenance；
- Direct Provider mapping contract。

暂时让 adapter 调用现有 `TdxClient` / Web Source，不重写底层。

门禁：

- registry uniqueness；
- direct provider set == registry；
- non-local channel exactly-one mapping；
- unsupported capability 在 I/O 前失败；
- vipdoc local-only。

### Phase 2：Streaming 状态机

保持既定执行顺序的第一项：

**Streaming 状态机**。

从 PR #1 提取状态机与对应 tests，适配最新 main Streaming 功能。

门禁新增：

- lifecycle transition table；
- partial-start failure；
- worker unexpected exit；
- stop timeout no duplicate worker；
- FAILED terminal；
- process-control exceptions；
- sync/async parity。

### Phase 3：Cache provenance

保持既定顺序第二项：

**Cache provenance**。

迁移 semantic cache / cache_v2 思路，但不直接复制 legacy duplication。

工作：

- QueryResult-only cache；
- full fingerprint；
- L1/L2 validation；
- freshness；
- provenance poisoning tests；
- replay/synthetic 不得伪装 live；
- 老 KlineCache/QuoteCache 作为 compatibility backend，逐步退场。

### Phase 4：Direct Provider contract 自动化

保持既定顺序第三项。

工作：

- 每个 Provider adapter 注册 capability；
- 将 `web` route 拆成真实 provider identity；
- TDX、Eastmoney、Tencent、Sina、JSL、BOC、LocalVipdoc 首批纳入；
- 最近新增 Fund/Futures/Bond/Options capability 全部注册；
- Facade 不再直接 import 特定 Source。

门禁：registry / direct / facade 三面对拍。

### Phase 5：ErrorEnvelope 全入口统一

保持既定顺序第四项。

覆盖：

- Python query；
- CLI；
- REST；
- WS；
- MCP；
- TaskStore/background worker。

删除各入口重复异常转换逻辑。

### Phase 6：Batch / SingleFlight / negative cache

保持既定顺序第五项。

要求：

- BatchResult status audit；
- request-scoped immutable mapping；
- follower deep-copy isolation；
- independent exception objects；
- capability batch limits；
- terminal-only negative cache；
- memory clamps；
- cancellation/deadline propagation。

### Phase 7：Facade / Router 瘦身

等 Runtime 成为 SSOT 后再拆文件，避免只做“代码搬家”。

目标：

- `UnifiedQuoteAPI`：只做 compatibility + QuerySpec build + result adapt；
- `RouteSelector`：迁移为 Policy adapter，旧 API 保留 deprecated shim；
- `DataSourceRouter`：停止新增功能，最终变成 compatibility shim；
- Web Mixin：只保留领域 API 组合，不再持有 provider selection；
- Async facade：优先走 AsyncExecutor；不能 async 的 Provider 才在 adapter 边界 to_thread。

### Phase 8：协议真实性与 Provider live contract

并行处理外部资源依赖项：

- 5 个 TDX 真机悬案；
- golden >= 目标规模；
- Provider schema drift；
- Eastmoney/Tencent/Sina/JSL/BOC live smoke；
- 限流与 anti-spider；
- endpoint deprecation。

Live Smoke 不作为 deterministic PR gate，但 workflow 自身必须 truthfully red/green。

### Phase 9：刷新 CI 真实红灯并准备合并

保持既定顺序最后一项：

**刷新 CI 真实红灯**。

只有 runner 真正执行 steps 后，才依据失败日志修源码。

最终 merge gate：同一 SHA 上：

- Ruff check + format；
- mypy；
- Linux/Windows Python tests；
- coverage >= 当前阈值且不得降低；
- Bridge；
- Golden；
- Spec；
- Adversarial；
- Reachability；
- Originality；
- Benchmark smoke；
- Docs links；
- Native compatibility（在正式移除前）；
- 新增 Provider contract；
- Runtime chain parity。

全部实际执行且绿色。

---

## 6. PR 拆分建议

不要再用一个 700+ commits 的 PR 完成迁移。建议从最新 main 创建：

1. `refactor/runtime-contracts-v11`
   - QuerySpec/Plan/Result/Registry；
2. `refactor/stream-state-v11`
   - Streaming state machine；
3. `refactor/cache-provenance-v11`
   - Semantic cache；
4. `refactor/provider-contract-v11`
   - Provider Registry + adapters；
5. `refactor/error-envelope-v11`
   - 全入口错误边界；
6. `refactor/batch-runtime-v11`
   - Batch/SingleFlight/negative cache；
7. `refactor/facade-thin-v11`
   - Facade/Router compatibility cleanup；
8. `test/provider-live-contract-v11`
   - live smoke / schema drift；
9. `ci/runtime-gates-v11`
   - 最终 SSOT gate 收口。

每个 PR 必须基于前一个已验证 SHA；PR #1 保持 Draft 作为参考，直到所有能力被逐段迁走并完成 parity audit。

---

## 7. 功能完整性的新判定方法

以后不再使用“有方法/有测试 = 已实现”的二元判断。

每个 Capability 维护：

| 状态 | 定义 |
|---|---|
| declared | Registry 中声明 |
| implemented | Provider adapter 有实现 |
| unit-tested | 纯逻辑测试通过 |
| contract-tested | 统一 Provider contract 通过 |
| fixture-tested | 真实历史 payload/golden 回放通过 |
| live-smoke | 最近周期真实 endpoint 通过 |
| surface-connected | Python/CLI/REST 等公开入口可达 |
| production-ready | error/freshness/provenance/schema drift/CI 全满足 |

只有最后一项可以在对外文档标“完整支持”。

---

## 8. 必须新增的架构门禁

### 8.1 Single execution path gate

对核心 capability（quotes/bars 起步）用 spy/trace 强制证明：

```text
Facade
 -> QuerySpec
 -> Planner
 -> Executor
 -> Provider
```

不得存在 Facade 直接绕过 Executor 调 Client/Source 的旁路。

### 8.2 Provider identity gate

任何 QueryResult 必须有 provider/channel；禁止 `source="web"` 这种信息不足的最终 provenance。

### 8.3 No silent fallback gate

strict policy 下强制注入 Provider failure，断言：

- 其他 Provider 没有 I/O；
- 返回指定 Provider 的失败；
- cache/replay/synthetic 不被冒充。

### 8.4 Surface parity gate

同一错误分别从：

- Python；
- CLI；
- REST；
- WS；
- MCP；

触发，断言 ErrorEnvelope domain code/context 一致，只允许 transport-specific wrapper 字段不同。

### 8.5 Cache poisoning gate

构造：

- Provider A payload 写到 Provider B key；
- historical 写到 current；
- replay 写到 live；
- stale 写到 fresh；
- capability A 写到 capability B；

全部必须拒绝。

### 8.6 Streaming lifecycle gate

状态转换采用表驱动测试；每个非法 transition 必须 fail closed。

### 8.7 Latest-main retention gate

PR #1 迁移期间，建立一份 latest-main capability manifest，至少覆盖 2026-09-09 后新增：

- fund；
- futures；
- bond；
- financial/valuation；
- announcements/news/research；
- options。

每个迁移 PR 都必须证明这些 capability 没有被大分支旧代码覆盖掉。

---

## 9. DoD：什么时候才能说“核心功能全部实现、主体链路全部贯通”

必须同时满足：

1. `main` 只有一个 canonical runtime；
2. Facade/Async/CLI/REST/WS/MCP 的核心 query 都进入同一 QuerySpec/Planner/Executor；
3. 每个 QueryPlan exactly-one Provider；
4. fallback 只有显式 Policy 能触发；
5. Provider identity + Channel + Provenance 全程不丢；
6. Cache 按 full fingerprint 与 provenance 校验；
7. Streaming 状态机完整并经故障注入；
8. Direct Provider API 与 Registry 自动对拍；
9. ErrorEnvelope 全入口统一；
10. Batch/SingleFlight/negative cache 具备隔离与审计；
11. 5 个真机协议悬案清零，或明确标为 experimental 并从 production-ready 能力移除；
12. 最新扩展 Web capability 进入 Provider contract matrix；
13. deterministic CI 同一 SHA 全部实际执行并绿色；
14. live smoke 与 deterministic gates 分离且如实报告；
15. PR #1 能力已全部迁移/裁定，不再需要长期双 runtime 分支。

在这些条件之前，项目可以说“业务能力丰富、主要链路可用”，但不宜说“统一核心运行时已全部完成”。

---

## 10. 推荐立即执行顺序

```text
0. 恢复 latest main 的可验证绿色 CI 基线
1. Runtime contract / Registry 骨架
2. Streaming 状态机
3. Cache provenance
4. Direct Provider contract 自动化
5. ErrorEnvelope 全入口统一
6. Batch / SingleFlight / negative cache
7. Facade / DataSourceRouter 瘦身
8. 真机定标 + Provider live contract
9. 刷新 CI 真实红灯并逐项清零
10. 同 SHA 全绿后才考虑合并 runtime convergence
```

其中第 2~6、9 项保持 PR #1 已确定的执行顺序，不降低任何门禁。

---

## 11. 本次审计直接依据

- `README.md`：当前功能面、核心代码地图、5 级降级路由；
- `docs/FEATURE_MAP_AND_ROADMAP.md`：历史九层架构与功能地图；
- `docs/POTENTIAL_ISSUES_AND_PLAN.md`：真机定标/Windows/性能等未完成项；
- `docs/REFACTOR_PLAN_v10.md`：上一轮结构治理完成记录；
- `atst/facade/api.py`：当前大型 UnifiedQuoteAPI 与 DataSourceRouter 委托；
- `atst/facade/routing.py`：auto/local/tdx/web 路由与熔断；
- `atst/sources/__init__.py`：五级 fallback 与 cache/source 编排；
- `atst/cache.py`：当前结构缓存；
- `atst/streaming/__init__.py` / `engine.py`：当前流式实现；
- `atst/errors.py`：E1-E9 + RetryAdvice；
- PR #1：Provider-first runtime、StreamState、semantic cache、ErrorEnvelope、Batch/SingleFlight/negative cache 以及严格 merge gate；
- `main@7720c049` 最新 Actions run `34664281919`：当前远端 CI 基线尚未获得真实 source-step 绿色证据；
- PR #1 与 main compare：共同祖先 `66734fb` 后已 diverged（PR 分支大量 runtime 变更，main 继续增加 7 个业务功能提交）。

---

## 12. 需要最终确认的架构决策

唯一会显著改变公开行为、需要在实际代码迁移前最终确认的决策：

> **统一 API 的默认多源行为是否从当前 `auto fallback` 改为 `provider-first strict`？**

本文推荐：**是**。

兼容策略：

- `provider="tdx", policy="strict"` 作为新默认；
- 老 `route="auto"` 保留一个 deprecation window，映射到显式 `FallbackPolicy`；
- 每次 fallback 提供 provenance/attempts，不再静默；
- 一个 major/minor 兼容周期后，再评估是否移除 `route="auto"` 旧语义。

这样既能获得 PR #1 的数据可信度，也不会一次性切断现有用户的高可用使用方式。
