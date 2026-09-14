# tstdx v14 深度升级进度

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
- ErrorEnvelope（`tstdx.error_envelope`，自 PR #6 提取）
- Semantic Cache（L1: `tstdx.cache_semantic`）
- SingleFlight（`tstdx.batch.SingleFlight`，自 PR #6 提取）
- Negative Cache（`tstdx.batch.NegativeCache`，自 PR #6 提取）
- BatchSpec / BatchItem / BatchResult（自 PR #6 提取）
- PersistentSemanticCache L2（`tstdx.cache_persistent`，自 PR #6 提取）
- StreamSpec / StreamPlanner（`tstdx.stream_contract`，自 PR #6 提取）

### v14 Runtime 内核（PR #7 已合并）

已建立：

- `tstdx.runtime/` 包：`Runtime` / `QueryRequest` / `QueryResponse` / `create_runtime` / `request_from_typed`
- `tstdx.execution/` 包：`ExecutionPlanner` / `ExecutionGraph` / `ExecutionNode` / `ExecutionPlan` / `SemanticExecutionAdapter`
- `tstdx.provider/` 包：Provider 基础契约 / `ProviderRouter` / TDX / Local / Web 适配器
- `tstdx.facade.runtime_adapter`：Facade → Runtime 兼容适配器
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

（扩展类在 main 的 `tstdx/typed_query.py`，共 15 个类）

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

`tstdx/domain/records.py` 提供 9 个类型化 Domain Record 族：

- FinancialRecord / FundRecord / BondRecord / NewsRecord / ResearchRecord /
  OptionRecord / MarketDataRecord / SearchRecord / MacroRecord
- `to_dict` / `from_dict` 无损往返；None 字段剥离（list[dict] 兼容）
- `typed_query.records_from_response(query, response)` 把执行结果归一化为
  Domain Record

### Streaming v14 集成（2026-09-14 完成）

把 canonical `StreamPlanner`（`tstdx.stream_contract`，fail-closed 编译
Provider 专属流计划）与 fail-closed 生命周期状态机
`StreamLifecycle`（`tstdx.streaming.state`，CREATED/RUNNING/STOPPING/
CLOSED/FAILED）桥接到 v14 Runtime 编排层：

- `tstdx/runtime/stream.py`：`StreamHandle`（plan + lifecycle + id）+
  `runtime_subscribe()` + `runtime_has_provider()`。
- `Runtime.subscribe()` / `unsubscribe()` / `get_subscription()` /
  `subscriptions()`：句柄集合管理与幂等清理。
- `StreamHandle.begin_start()` / `begin_stop()` / `close()` / `fail()` /
  `require_subscribable()` 完整生命周期面；FAILED 语义永不被擦除。
- 契约测试 `tests/v14/test_stream_integration.py`（24 测试）覆盖：
  Planner 校验传播、Provider 预检、状态迁移、幂等、快照独立性、
  多订阅隔离。
- `tstdx.runtime` 公共 API 导出 `StreamHandle` 与 `runtime_subscribe`。

约束：Runtime 层只做编排（编译 + 注册 + 生命周期管理），不驱动回源
Worker；回源由外部 StatefulQuoteStream 按 `handle.plan` 执行，避免
把 TDX 传输细节泄漏到 Runtime 内核。

## 当前优化方向

### Phase 1 Typed Capability Expansion

继续覆盖：

- FinancialQuery
- FundQuery
- BondQuery
- FuturesQuery
- OptionsQuery
- NewsQuery
- ResearchQuery
- F10Query
- MacroQuery
- SearchQuery

要求：

每个能力必须拥有：

```
Typed Query
Registry
Direct Binding
Runtime Execution
Domain Result
Surface Adapter
Contract Test
```

## Phase 2 Domain Model

逐步替换：

```
list[dict]
```

为：

```
Domain Record
    -> QueryResult[T]
```

包括：

- FinancialRecord
- FundRecord
- BondRecord
- OptionRecord
- NewsRecord
- ResearchRecord

## Phase 3 Provider Adapter

目标：

```
providers/
    tdx/
    eastmoney/
    sina/
    tencent/
    boc/
    iwencai/
    derived/
```

统一接口：

- capabilities()
- execute()
- health()
- metadata()

## Phase 4 Contract Automation

自动验证：

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
