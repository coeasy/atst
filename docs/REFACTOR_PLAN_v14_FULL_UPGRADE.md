# REFACTOR_PLAN_v14_FULL_UPGRADE

> Status: **implementation in progress**  
> Branch: `v14-runtime-phase1`  
> Draft PR: `#7`  
> Baseline: `main` / v1.0.0

## 1. Goal

V14 is not a feature-only release. Its purpose is to move tstdx from a collection of protocol, source and facade capabilities to a single market-data execution runtime while preserving the v1 public API.

Target request path:

```text
Python API / CLI / REST / WS / MCP
              |
              v
           Gateway
              |
              v
        QueryRequest
              |
              v
        Runtime Kernel
              |
              v
           Planner
              |
              v
       ExecutionPlan / DAG
              |
              v
       Provider Router
       /      |      \
     TDX     Web    Local / Cache
              |
              v
      Canonical Domain
              |
              v
     Cache / Stream / Storage
```

## 2. Hard constraints

1. Existing `TdxClient`, `AsyncTdxClient` and `UnifiedQuoteAPI` APIs remain compatible.
2. Core installation remains zero hard dependencies.
3. Protocol parser behaviour is not rewritten as part of Runtime Phase 1.
4. New runtime paths must have explicit contract tests before they replace legacy routing.
5. No merge until same-SHA CI executes real steps and is green.
6. Cache misses must fall through; they must never masquerade as a successful `None` result.
7. Provider fallback must preserve the actual selected provider in response provenance.
8. Execution graphs must reject cycles, missing dependencies and duplicate nodes.

## 3. Current implementation status

### 3.1 Runtime Kernel — IMPLEMENTED (Phase 1)

Implemented modules:

```text
tstdx/runtime/
  __init__.py
  bootstrap.py
  context.py
  request.py
  response.py
  runtime.py
```

Implemented contracts:

- `QueryRequest` preserves positional arguments, keyword arguments and runtime metadata.
- semantic `cache_key` excludes volatile trace/routing metadata.
- `ExecutionContext` carries `request_id`, `trace_id`, timeout and selected provider.
- `QueryResponse` carries success/error/data/error-code plus provenance metadata.
- `Runtime` preserves registered compatibility handlers.
- unhandled operations execute through Planner -> ExecutionPlan -> ProviderRouter.
- `create_runtime()` registers injected TDX/Web/Local/Cache backends without eager optional dependencies.

### 3.2 Execution Graph — IMPLEMENTED (Phase 1)

Implemented modules:

```text
tstdx/execution/
  __init__.py
  node.py
  graph.py
  plan.py
  planner.py
```

Implemented guarantees:

- duplicate execution nodes are rejected;
- missing dependency nodes are rejected;
- graph cycles are detected with a readable cycle path;
- explicit output nodes are supported;
- Planner creates provider-backed execution plans;
- explicit provider metadata and ordered fallback are supported.

### 3.3 Provider Runtime — IMPLEMENTED (Phase 1)

Implemented modules:

```text
tstdx/provider/
  __init__.py
  base.py
  router.py
  tdx.py
  web.py
  local.py
  cache.py
```

Implemented guarantees:

- single Provider ABC (`base.py`);
- duplicate orphan provider abstraction removed;
- provider registration and lookup;
- provider health gate;
- ordered fallback;
- aggregated provider failure diagnostics;
- positional/keyword call-shape preservation;
- cache miss raises a fallback signal instead of returning false-success `None`.

### 3.4 Facade Runtime Adapter — IMPLEMENTED, NOT DEFAULT

Implemented:

```text
tstdx/facade/runtime_adapter.py
```

The adapter:

- translates facade-shaped method calls to `QueryRequest`;
- maps `route=tdx/web/local/cache` to explicit provider selection;
- leaves `route=auto` to provider fallback policy;
- converts `QueryResponse` back to existing `ApiResponse`;
- preserves runtime error code and provenance metadata.

`UnifiedQuoteAPI` itself still uses the legacy routing path. This is intentional until runtime contract tests and same-SHA CI are green.

## 4. Tests added

```text
tests/v14/
  test_runtime_execution.py
  test_runtime_bootstrap.py
  test_facade_runtime_adapter.py
```

Covered behaviours include:

- fallback across failed/unhealthy providers;
- explicit provider selection;
- compatibility handlers;
- unsupported-operation envelope;
- planner/router identity coherence;
- cache-key stability;
- cache miss fallthrough;
- TDX positional/keyword argument preservation;
- execution graph cycle/missing-dependency gates;
- facade route mapping;
- facade error-code and provenance preservation;
- zero-dependency runtime bootstrap.

## 5. CI status

The PR is deliberately kept Draft.

Observed CI behaviour during implementation:

- `Native` workflow has executed successfully on intermediate heads.
- some `CI` runs failed before any job steps were created and produced no downloadable job logs; these are runner/scheduling failures, not source-level evidence.
- latest heads are continuously re-triggering pull-request workflows.

Merge rule remains unchanged: do not treat `mergeable=true` as validation. Merge only when the final same SHA runs the required gates and they are green.

## 6. Phase 2 — Runtime integration

### 6.1 UnifiedQuoteAPI opt-in runtime path

Target design:

```text
UnifiedQuoteAPI.query()
   |-- legacy path (default during migration)
   `-- RuntimeFacadeAdapter (opt-in)
```

Required before default switch:

- quotes parity;
- bars parity;
- route error parity;
- `ApiResponse` code/extra parity;
- `.df` compatibility;
- no eager TDX/Web client construction.

### 6.2 Provider policy

Next provider runtime work:

- health history rather than one boolean probe;
- latency/success ranking;
- circuit state;
- retry advice integration;
- per-operation provider capability checks;
- failure attempt provenance.

## 7. Phase 3 — Cache Provenance

Introduce a canonical cache record:

```text
CacheEntry
  data
  source
  created_at
  cache_key
  quality
  checksum
  version
```

Rules:

- cache metadata is separate from market payload;
- routing/trace metadata does not change semantic cache keys;
- stale/invalid cache records trigger provider fallthrough;
- provenance survives Runtime -> Facade -> Gateway.

## 8. Phase 4 — Canonical Domain

Unify provider-specific results behind existing domain models instead of creating parallel shadow models.

Priority order:

1. Symbol / market identity;
2. Quote;
3. Bar;
4. Tick/minute;
5. corporate-action metadata;
6. source/provenance envelope.

The V14 rule is **extend the existing `tstdx.domain` single source of truth**, not create a second incompatible domain hierarchy.

## 9. Phase 5 — Streaming Runtime

Integrate the existing StreamEngine rather than replacing it blindly.

Target additions:

```text
MarketEvent
EventBus
StateStore
WindowProcessor
Subscription
```

Required invariants:

- existing QuoteStream / AsyncQuoteStream remain compatible;
- reconnect/backpressure/gap-fill behaviour remains owned by current streaming engine;
- event processing must be replayable;
- stateful processors must have bounded memory policies.

## 10. Phase 6 — Gateway convergence

REST, WebSocket, MCP and CLI should translate requests at the boundary and delegate market-data execution to Runtime.

Do not duplicate provider selection or fallback policy inside gateways.

## 11. Phase 7 — Optimizer

Only after Runtime parity is stable:

- execution-node common subexpression elimination;
- parallel independent nodes;
- cache-aware rewrite;
- incremental execution for streaming updates;
- batch provider execution.

Optimizers must never change observable query semantics.

## 12. Phase 8 — release hardening

Required release gates:

- Ruff check and format;
- mypy;
- full non-network pytest matrix;
- coverage gate >= repository baseline;
- AST module reachability: zero unregistered orphans;
- golden/spec/originality/adversarial gates;
- wheel/source installation smoke;
- same-SHA workflow evidence.

## 13. Explicit non-goals for Phase 1

The following are intentionally deferred until the runtime migration is proven:

- replacing all existing source classes;
- rewriting protocol parsers;
- adding a second factor/indicator framework inside tstdx;
- introducing distributed execution;
- changing v1 public method signatures;
- deleting legacy routing before parity gates exist.

## 14. Migration strategy

```text
Stage A: build Runtime contract
Stage B: adapter parity tests
Stage C: opt-in facade runtime
Stage D: gateway reuse
Stage E: runtime becomes default
Stage F: remove duplicated legacy routing only after deprecation window
```

## 15. Immediate next tasks

1. Get final same-SHA CI to execute real steps.
2. Fix source-level lint/type/test failures first.
3. Add provider capability/provenance tracking.
4. Add opt-in `UnifiedQuoteAPI.query()` runtime path in a safe patch environment.
5. Add CacheEntry provenance contract.
6. Extend parity tests for quotes/bars.

---

This document is the implementation source of truth for V14. Status must be updated from actual committed code and CI evidence; planned modules must not be marked complete before they exist and are tested.
