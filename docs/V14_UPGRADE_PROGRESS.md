# tstdx v14 深度升级进度

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
- ErrorEnvelope
- Semantic Cache
- SingleFlight
- Negative Cache

### Typed Query 第一阶段

已建立：

- CapabilityQuery
- SymbolQuery
- BatchCapabilityQuery
- BalanceSheetQuery
- FundRankQuery
- OptionSnapshotQuery
- TypedQueryResult

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
