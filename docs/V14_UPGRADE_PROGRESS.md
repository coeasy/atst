# tstdx v14 深度升级进度

> 更新: 2026-09-13
> 说明: 已合并 PR #7 (v14-runtime-phase1) 与 PR #6 的独立工程原语提取。

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
