# atst v14 深度升级进度

> 更新: 2026-09-14
> 说明: 已合并 PR #7 (v14-runtime-phase1)、PR #6 的独立工程原语提取；已完成 Typed Capability 扩展与 Domain Model Phase 2。

## 目标

按照 `REFACTOR_PLAN_v14_FULL_UPGRADE.md`，继续把 v13 Provider-first Runtime 升级为完整领域化 Runtime。

最终目标：

```
Typed Query
    -> QueryPlanner
    -> UnifiedRuntime
    -> Provider Adapter
    -> Domain Result Model
    -> QueryResult[T]
    -> Client / HTTP / WS / MCP / CLI
```

## 已完成

### Runtime 基础

- Provider-first Runtime
- QuerySpec
- QueryPlanner
- DirectBinding
- Capability Catalog
- Provenance
- ErrorEnvelope（`atst.error_envelope`，自 PR #6 提取）
- Semantic Cache（L1: `atst.cache_semantic`）
- SingleFlight（`atst.batch.SingleFlight`，自 PR #6 提取）
- Negative Cache（`atst.batch.NegativeCache`，自 PR #6 提取）
- BatchSpec / BatchItem / BatchResult（自 PR #6 提取）
- PersistentSemanticCache L2（`atst.cache_persistent`，自 PR #6 提取）
- StreamSpec / StreamPlanner（`atst.stream_contract`，自 PR #6 提取）

### v14 Runtime 内核（PR #7 已合并）

已建立：

- `atst.runtime/` 包：`Runtime` / `QueryRequest` / `QueryResponse` / `create_runtime` / `request_from_typed`
- `atst.execution/` 包：`ExecutionPlanner` / `ExecutionGraph` / `ExecutionNode` / `ExecutionPlan` / `SemanticExecutionAdapter`
- `atst.provider/` 包：Provider 基础契约 / `ProviderRouter` / TDX / Local / Web 适配器
- `atst.facade.runtime_adapter`：Facade → Runtime 兼容适配器
- `tests/v14/`：5 个契约测试套件（bootstrap / execution / semantic / typed_query / facade adapter）

### Typed Query 第一阶段

已建立：

- CapabilityQuery
- SymbolQuery
- BatchCapabilityQuery
- BalanceSheetQuery
- FundRankQuery
- OptionSnapshotQuery
- TypedQueryResult
- IncomeStatementQuery / CashFlowQuery / FundHoldingsQuery / BondKlineQuery
- FuturesKlineQuery / NewsQuery / ResearchReportQuery / F10Query

（扩展类在 main 的 `atst/typed_query.py`，共 15 个类）

### Typed Capability 扩展（2026-09-14 完成）

按 9 大领域补齐 **49 个** Typed Query 契约，总数 11 → 60：

- **Financial (10)**: financial_abstract / dividend_history / stock_valuation /
  holder_changes / holder_num / free_holders / capital_changes /
  corporate_action / announcements / ipo_review
- **Fund (7+2)**: fund_base_info(_multi) / fund_manager / fund_asset_allocation /
  fund_period_change / fund_industry_distribution / fund_public_dates
- **Bond (7+1)**: bond_base_info / all_base_info / realtime / trades /
  today_bill / history_bill / convertible_bond
- **Futures (3+1)**: futures_base_info / realtime / trades
- **Options (2+1)**: options_list / trends
- **Research (1+1)**: research_visits
- **MarketData (10)**: hot_rank / limit_pool / northbound / margin / longhu /
  market_stat / board_rank / fund_flow / stock_changes / rank
- **Search (7)**: wencai / screening / suggest / index_constituents /
  industry_board / board_list / board_member
- **Macro (2)**: fx_rates / global_quotes

要求：每个能力必须通过 QueryPlanner 编译 + request_from_typed 适配 +
Runtime 语义执行（含语义缓存身份区分）。必填业务字段 fail-closed 校验。

### Domain Model（2026-09-14 完成）

`atst/domain/records.py` 提供 9 个类型化 Domain Record 族：

- FinancialRecord / FundRecord / BondRecord / NewsRecord / ResearchRecord /
  OptionRecord / MarketDataRecord / SearchRecord / MacroRecord
- `to_dict` / `from_dict` 无损往返；None 字段剥离（list[dict] 兼容）
- `typed_query.records_from_response(query, response)` 把执行结果归一化为
  Domain Record

### Streaming v14 集成（2026-09-14 完成）

把 canonical `StreamPlanner`（`atst.stream_contract`，fail-closed 编译
Provider 专属流计划）与 fail-closed 生命周期状态机
`StreamLifecycle`（`atst.streaming.state`，CREATED/RUNNING/STOPPING/
CLOSED/FAILED）桥接到 v14 Runtime 编排层：

- `atst/runtime/stream.py`：`StreamHandle`（plan + lifecycle + id）+
  `runtime_subscribe()` + `runtime_has_provider()`。
- `Runtime.subscribe()` / `unsubscribe()` / `get_subscription()` /
  `subscriptions()`：句柄集合管理与幂等清理。
- `StreamHandle.begin_start()` / `begin_stop()` / `close()` / `fail()` /
  `require_subscribable()` 完整生命周期面；FAILED 语义永不被擦除。
- 契约测试 `tests/v14/test_stream_integration.py`（24 测试）覆盖：
  Planner 校验传播、Provider 预检、状态迁移、幂等、快照独立性、
  多订阅隔离。
- `atst.runtime` 公共 API 导出 `StreamHandle` 与 `runtime_subscribe`。

约束：Runtime 层只做编排（编译 + 注册 + 生命周期管理），不驱动回源
Worker；回源由外部 StatefulQuoteStream 按 `handle.plan` 执行，避免
把 TDX 传输细节泄漏到 Runtime 内核。

## 优化进度总览

| Phase | 状态 | 完成日期 |
|-------|------|----------|
| 1 Typed Capability Expansion (60 契约) | ✅ 完成 | 2026-09-14 |
| 2 Domain Model (9 Record 族) | ✅ 完成 | 2026-09-14 |
| 3 Provider Adapter (统一接口) | ✅ 完成 | 2026-09-14 |
| 4 Contract Automation (审计脚本) | ✅ 完成 | 2026-09-14 |
| 5 Streaming v14 集成 (编排桥接) | ✅ 完成 | 2026-09-14 |
| 6 Gateway convergence | 🔄 进行中 | RuntimeGateway 已实现 |
| 7 Optimizer (DAG CSE / 并行 / 批处理) | 🔄 进行中 | execute_batch() 已实现 |
| 8 Release hardening (Ruff / mypy / CI) | ✅ 完成 | 2026-09-14 |

## 后续优化方向

### Phase 6 Gateway convergence

REST / WebSocket / MCP / CLI 翻译边界请求并委托 Runtime 执行。

网关禁止实现独立的 Provider 选择、回退、缓存或 provenance 逻辑。

当前状态：CLI 和 Client 仍走 legacy dispatch 路径；RuntimeFacadeAdapter
已存在但非默认。**2026-09-14 新增** `RuntimeGateway`（`atst/runtime/gateway.py`）
作为 CLI/HTTP/WS 的统一适配层，通过 RuntimeFacadeAdapter 桥接到 Runtime
执行，禁止实现独立 Provider 选择/回退/缓存/provenance 逻辑。
下一步：评估 CLI 从 TdxClient 迁移到 RuntimeGateway 的成本并制定迁移方案。

### Phase 7 Optimizer

语义对等稳定后才启动：

- DAG common-subexpression elimination ⏳ 待启动
- 独立节点并行执行 ⏳ 待启动
- semantic-cache-aware rewrite ⏳ 待启动
- streaming 增量执行 ⏳ 待启动
- Provider 批量执行 ✅ **2026-09-14 完成**：`Runtime.execute_batch()`
  支持语义缓存去重 + 并发执行（`max_concurrent` 参数），返回保序结果。

优化器禁止改变 QuerySpec / QueryFingerprint / Provider 身份或可观察结果语义。

### Phase 8 Release hardening

必须通过的门禁：

- Ruff check + format ✅（2026-09-14 完成）
- mypy ✅（atst/ 全包清洁，0 errors）
- 全量非网络 pytest 矩阵 ✅（632 v14 测试通过）
- 覆盖率 ≥ 仓库基线 ⏳ 待测量
- AST 模块可达性（零意外孤儿）⏳ 待验证
- golden / spec / originality / adversarial 门禁 ⏳ 待验证
- wheel/sdist 安装 smoke ⏳ 待执行
- 最终同 SHA workflow 证据 ⏳ 待执行

## 验收标准

```
Typed Query
 =
Registry
 =
Binding
 =
Runtime
 =
Surface
 =
Test
```

## 验收标准

禁止：

- hidden routing
- dict-only business API
- duplicate provider selection
- untyped capability expansion
- surface-specific business logic

只有所有 capability 满足完整链路，才认为 v14 完成。
