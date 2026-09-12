# tstdx v14 Full Upgrade Plan

## Status

Based on v13 clean-break migration.

Goal: after legacy business capability migration, continue upgrading the entire system into a single typed, provider-first runtime.

Principles:

- No restoration of legacy Facade/route/source semantics.
- One runtime execution chain only.
- All historical capabilities must be upgraded, not merely wrapped.
- Documentation, code, tests and CI gates must describe the same architecture.

---

# 1. Target Architecture

```
User / Strategy / Application
          |
          v
 Client / AsyncClient
          |
          v
 Typed Capability QuerySpec
          |
          v
 QueryPlanner
          |
          v
 QueryPlan
          |
          v
 UnifiedRuntime
          |
 +-----------------------------+
 | Cache | SingleFlight | Error |
 +-----------------------------+
          |
          v
 ProviderAdapter
          |
          v
 QueryResult<T> + Provenance
```

Cross provider execution:

```
FallbackPolicy
      |
      v
ProviderOrchestrator
      |
      +--> UnifiedRuntime(provider=A)
      +--> UnifiedRuntime(provider=B)
```

UnifiedRuntime never performs implicit fallback.

---

# 2. Remaining Problems After v13 Migration

## 2.1 Generic capability parameters

Current:

```
Client.call(capability, *args, **kwargs)
```

Problem:

- weak typing
- hidden semantic drift
- difficult schema evolution

Upgrade:

Introduce typed capability specifications:

- BalanceSheetQuerySpec
- FundRankQuerySpec
- OptionSnapshotQuerySpec
- NewsQuerySpec
- ResearchQuerySpec
- F10QuerySpec

Every capability must have:

- typed request
- planner validation
- fingerprint support
- typed result
- serialization schema

---

# 3. Domain Result Model Upgrade

Current migrated capabilities still contain dict based outputs.

Upgrade all important domains:

```
Raw Provider Data
      |
      v
Domain Model
      |
      v
QueryResult[DomainType]
```

Examples:

- BalanceSheetRecord
- FinancialReportRecord
- FundRankRecord
- OptionSnapshotRecord
- NewsRecord
- ResearchReportRecord

Benefits:

- schema stability
- IDE support
- dataframe/export consistency
- better validation

---

# 4. Provider Adapter Refactor

Current DirectProviderExecutor is becoming too large.

Split into:

```
providers/

 tdx/
 eastmoney/
 sina/
 tencent/
 baidu/
 boc/
 iwencai/
 local_vipdoc/
 derived/
```

Each adapter implements:

```
ProviderAdapter

capabilities()
execute(plan)
health()
metadata()
```

Runtime never imports provider implementation classes.

---

# 5. Capability Contract Generator

Create automatic contract generation.

For every capability:

Verify:

```
Registry
   ==
DirectBinding
   ==
Planner
   ==
Client Surface
   ==
Serializer
   ==
Tests
```

CI fails if any mismatch exists.

---

# 6. Cache Architecture Upgrade

## L1

Complete:

- bounded LRU
- metrics
- eviction policy

Further:

- capability aware TTL
- memory accounting
- hot key detection

## L2

Add:

- schema migration
- compression policy
- corruption recovery
- background cleanup
- cache observability

Different capability policies:

```
quotes       seconds
finance      hours
announcement minutes
sync         disabled
```

---

# 7. Streaming Upgrade

Move to:

```
StreamSpec
   |
StreamPlanner
   |
StreamPlan
   |
StreamProviderAdapter
   |
Stateful Runtime
```

Support:

- quote stream
- minute stream
- tick stream
- orderbook stream

All events carry:

- provider
- channel
- timestamp
- sequence
- provenance

---

# 8. Protocol Surface Upgrade

All surfaces become generated adapters:

## Python

Client / AsyncClient only.

## HTTP

Generated from capability schemas.

## WS

Unified JSON-RPC.

## MCP

Capability discovery + execution.

## CLI

Capability driven commands.

No surface implements business logic.

---

# 9. Observability Upgrade

Add unified tracing:

Every execution records:

- request id
- fingerprint
- provider
- channel
- latency
- cache status
- error code
- fallback attempts

Metrics:

- provider latency
- cache hit rate
- failure rate
- capability usage

---

# 10. Testing Strategy

## Contract tests

Every capability:

- registry test
- planner test
- binding test
- result schema test
- error test

## Property tests

Verify:

- fingerprint stability
- cache isolation
- provenance correctness
- no hidden fallback

## Integration tests

Verify:

- Python
- HTTP
- WS
- MCP
- CLI

all execute identical runtime paths.

---

# 11. Cleanup

Remove:

- duplicated provider selection
- old helper wrappers
- dict-only business APIs
- dead compatibility modules
- duplicated serializers

Keep only:

- protocol implementation
- provider adapters
- runtime
- typed domain models

---

# 12. Final Definition of Done

The project is complete only when:

1. Every capability has typed QuerySpec.
2. Every capability has DirectBinding.
3. Every capability has typed Result model.
4. Every surface uses Client/runtime.
5. No hidden routing exists.
6. No duplicate execution path exists.
7. Cache semantics are capability aware.
8. Streaming uses one lifecycle model.
9. CI executes real steps and passes.
10. Documentation matches implementation exactly.

---

# Execution Order

1. Typed capability specifications.
2. Provider adapter split.
3. Domain result models.
4. Capability contract generator.
5. Cache hardening.
6. Streaming expansion.
7. Protocol generation.
8. Observability.
9. Full CI verification.
10. Release.
