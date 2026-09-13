# tstdx v13 Clean-Break Refactor Execution Status

> Source status only. Release verification still requires exact-head real CI execution.

## Source-level convergence completed

- [x] `QuerySpec` is Provider-only; no legacy `source`, `route` or generic `allow_partial`.
- [x] `BatchSpec` owns explicit partial/batch semantics.
- [x] Registry is executable truth and `web` pseudo-provider is rejected.
- [x] Tier-A quotes/bars/snapshot/minute/trades/security_count/security_list use exact DirectBindings.
- [x] local_vipdoc remains a separate Provider.
- [x] `UnifiedRuntime` is the sole single-Provider kernel.
- [x] `ProviderOrchestrator` is the sole explicit cross-Provider fallback layer.
- [x] `Client` / `AsyncClient` are the sole high-level business APIs.
- [x] Stateful streaming executes through UnifiedRuntime.
- [x] legacy `UnifiedQuoteAPI`, async facade, RouteSelector, DataSourceRouter business kernel and old HTTP/WS business servers are removed.
- [x] L1/L2/negative-cache/SingleFlight contracts remain canonical and bounded/verified.

## Complete historical business capability migration

The previous Tier-A-only limitation has been removed at source level.

- [x] `tstdx.capability_catalog` is the migration SSOT.
- [x] all known historical business capability families are represented in `MIGRATED_CAPABILITIES`.
- [x] migrated bindings declare explicit Provider + Channel + backend.
- [x] known single-source abilities retain actual Provider identity.
- [x] aggregate/composite historical helpers use explicit `derived` provenance instead of false upstream identity.
- [x] TDX finance/F10/extended/goods/protocol supplemental abilities are promoted.
- [x] fund/fund-manager/fund-company abilities are promoted.
- [x] finance statements/valuation/governance/shareholder/ESG abilities are promoted.
- [x] futures/bond/options/goods/extended-market abilities are promoted.
- [x] blocks/sectors/indexes/rankings/search/iWencai/macro/IPO/northbound abilities are promoted.
- [x] news/announcement/research abilities are promoted.
- [x] rates and web supplemental history/minute abilities are promoted.
- [x] adjusted-bars and local-day sync are explicit `derived` capabilities.
- [x] Registry is generated from migrated bindings and `audit_direct_bindings()` enforces Registry/Binding parity.
- [x] migration contract test locks historical ability coverage.

## Public surface parity

All migrated capabilities use the same chain:

```text
Client.call / same-name Client method
  -> QuerySpec
  -> QueryPlanner
  -> UnifiedRuntime
  -> exact DirectBinding
  -> Provider implementation
  -> QueryResult / Provenance
```

And are reachable through:

- Python: `Client.call(...)` plus same-name migrated methods;
- Async: `AsyncClient.call(...)` plus same-name async methods;
- HTTP: `POST /v13/query/{capability}`, discovery via `GET /v13/capabilities`;
- WebSocket JSON-RPC: `query`, discovery via `runtime.capabilities`;
- MCP: `query_capability` plus Tier-A tools;
- CLI: `tstdx query`, discovery via `tstdx capabilities`.

Tier-A keeps dedicated ergonomic methods/endpoints in addition to the generic capability path.

## Provider identity

- `web` remains invalid.
- `local_vipdoc` remains separate from `tdx`.
- `derived` is an explicit composite Provider, not fallback or a pseudo-source.
- cross-Provider fallback still exists only through `FallbackPolicy -> ProviderOrchestrator`.
- one QueryPlan never silently selects a second Provider.

## Retired semantics remain retired

The migration does **not** restore:

- `UnifiedQuoteAPI` / `AsyncUnifiedQuoteAPI`;
- `route=` / `route="auto"`;
- `source` Provider aliases;
- Facade-owned fallback/circuit state;
- DataSourceRouter business execution;
- MCP Facade/raw-client dual routing;
- legacy QuoteStream business kernel.

Low-level protocol/web/provider modules remain implementation assets behind DirectBindings where needed.

## Deterministic migration gates

`tests/runtime/test_legacy_capability_migration_v13.py` requires:

1. the historical business ability contract is a subset of `MIGRATED_CAPABILITIES`;
2. every migrated binding is declared in ProviderRegistry;
3. every migrated binding has an exact DirectBinding;
4. every migrated capability compiles through QueryPlanner;
5. composite abilities use explicit `derived` provenance;
6. retired Facade exports remain absent;
7. representative finance/fund/futures/bond/options/news/F10/derived abilities remain available.

## Verification still pending

Do **not** claim release completion until the exact latest SHA has real executed evidence for:

- Ruff;
- mypy;
- pytest / coverage;
- architecture/spec/golden/adversarial/reachability/docs gates;
- Native build/parity;
- real Provider smoke/calibration where required.

GitHub Actions jobs with no assigned runner, `steps=[]`, `steps=null`, `runner_id=0`, skipped/disabled/soft-failed jobs or workflow-level success without real blocking steps are not verification.

## Release / merge gate

PR #6 remains Draft. It may leave Draft only after the same exact head SHA executes all deterministic blocking gates and they are green. Source inspection, migration breadth, or outer workflow status alone never satisfies the merge gate.
