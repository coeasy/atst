# atst v13 Implementation Alignment Matrix

> Companion to `ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md`, `REFACTOR_PLAN_v13_CLEAN_BREAK.md`, and `PROVIDER_CAPABILITY_MATRIX_v13.md`.
>
> `SOURCE-ALIGNED` is source state only. `VERIFIED` requires the exact same head SHA to receive real GitHub Actions runners and execute every blocking gate successfully. `steps=[]`, `steps=null`, `runner_id=0`, skipped or outer-workflow-only success are never verification.

## 1. Core architecture

| Area | Source state | v13 contract | Evidence |
|---|---|---|---|
| Provider / Channel / Capability | SOURCE-ALIGNED | Registry is executable truth; `web` rejected; aggregate helpers use explicit `derived` | VERIFICATION-PENDING |
| QuerySpec / QueryFingerprint | SOURCE-ALIGNED | no `source`, `route`, generic `allow_partial`; deterministic options identity | VERIFICATION-PENDING |
| QueryPlanner | SOURCE-ALIGNED | sole request-to-plan compiler | VERIFICATION-PENDING |
| UnifiedRuntime | SOURCE-ALIGNED | sole single-Provider execution kernel | VERIFICATION-PENDING |
| ProviderOrchestrator | SOURCE-ALIGNED | sole explicit cross-Provider fallback layer | VERIFICATION-PENDING |
| Result / Provenance | SOURCE-ALIGNED | canonical Provider/Channel/Fingerprint provenance; no `.source` alias | VERIFICATION-PENDING |
| ErrorEnvelope | SOURCE-ALIGNED | canonical safe external failure contract | VERIFICATION-PENDING |
| L1 / L2 / negative cache / SingleFlight | SOURCE-ALIGNED | full-fingerprint identity, bounded/verified caches, isolated coalescing | VERIFICATION-PENDING |
| Streaming | SOURCE-ALIGNED | StreamSpec -> StreamPlanner -> Stateful -> UnifiedRuntime | VERIFICATION-PENDING |

## 2. Complete legacy capability migration

The historical `UnifiedQuoteAPI` business ability set is no longer `NOT-PROMOTED`.

`MIGRATED_BINDINGS` is now the migration SSOT. Every migrated entry must satisfy:

```text
legacy ability
  -> migrated capability name
  -> explicit Provider + Channel
  -> ProviderRegistry declaration
  -> exact DirectBinding
  -> QuerySpec / QueryPlanner
  -> UnifiedRuntime
  -> QueryResult / Provenance
  -> Client / AsyncClient
  -> HTTP / WS / MCP / CLI generic capability surface
```

Covered families include:

- finance / statements / valuations / governance / shareholders / ESG;
- corporate actions / capital changes / adjusted bars;
- F10;
- blocks / sectors / indexes / rankings / auction / volume-price;
- funds / fund managers / fund companies;
- bonds / futures / options / goods / extended markets;
- macro / IPO / northbound data;
- search / suggest / iWencai;
- news / announcements / reports / research visits;
- FX / rates;
- web history / minute / supplemental market datasets;
- local-day synchronization.

`tests/runtime/test_legacy_capability_migration_v13.py` locks the historical business contract and fails if any known old ability is absent from the v13 migrated catalog.

## 3. Public surface parity

| Contract | Python | Async | CLI | HTTP | WS | MCP |
|---|---:|---:|---:|---:|---:|---:|
| Tier-A ergonomic methods | yes | yes | yes | yes | yes | yes |
| all migrated capabilities | `Client.call` + same-name methods | `AsyncClient.call` + same-name async methods | `atst query` | `POST /v13/query/{capability}` | JSON-RPC `query` | `query_capability` |
| capability discovery | `Client.capabilities()` | yes | `atst capabilities` | `GET /v13/capabilities` | `runtime.capabilities` | tool manifest + query tool |
| explicit Provider | yes | yes | yes | yes | yes | yes |
| shared result serialization | object | object | yes | yes | yes | yes |
| canonical ErrorEnvelope | domain boundary | same | stderr JSON | HTTP JSON | JSON-RPC | JSON-RPC/tool |
| hidden route/source semantics | none | none | none | none | none | none |

## 4. Provider identity after migration

- Known one-source capabilities keep their actual Provider (`tdx`, `sina`, `tencent`, `eastmoney`, `baidu`, `boc`, `iwencai`, `builtin`).
- Historical helpers that internally choose or combine sources use the explicit `derived` Provider instead of publishing false upstream provenance.
- `adjusted_bars` and `sync_daily` are explicit derived/composite capabilities.
- `local_vipdoc` remains a separate Provider.
- `web` remains invalid.
- hidden cross-Provider fallback remains forbidden; only `FallbackPolicy -> ProviderOrchestrator` may switch Providers.

## 5. Clean-break removal state

| Legacy component / semantic | State |
|---|---|
| QuerySpec `source` | REMOVED |
| QuerySpec generic `allow_partial` | REMOVED |
| public `route=` / `route="auto"` | REMOVED |
| `web` pseudo-provider | REJECTED |
| ResultMeta `.source` | REMOVED |
| `UnifiedQuoteAPI` / `AsyncUnifiedQuoteAPI` | REMOVED |
| `RouteSelector` / facade fallback kernel | REMOVED |
| `DataSourceRouter` business kernel | REMOVED |
| legacy HTTP / WS servers | REMOVED |
| MCP facade / raw-TdxClient dual target | REMOVED |
| old CLI route/web business commands | REMOVED |
| legacy QuoteStream business kernel | REMOVED |

Low-level Provider implementations remain internal implementation assets where the new DirectBinding layer delegates to them. Their existence does not create a second public business API.

## 6. Deterministic gates

The source now contains gates for:

- historical ability set <= `MIGRATED_CAPABILITIES`;
- every migrated catalog entry exists in ProviderRegistry;
- every Registry row has a DirectBinding;
- representative finance/fund/futures/bond/options/news/F10/derived capabilities compile through QueryPlanner;
- retired Facade exports stay absent;
- Tier-A behavior remains locked;
- cache identity/freshness/integrity remains locked;
- HTTP/WS/MCP/CLI call only the Client/runtime chain;
- Streaming remains UnifiedRuntime-backed.

## 7. Current verification state

Current source target: **SOURCE-ALIGNED**.

Current release evidence: **VERIFICATION-PENDING** until the latest exact SHA has real executed evidence for at least:

- Ruff;
- mypy;
- pytest / coverage;
- architecture / spec / golden / adversarial / reachability / docs gates;
- Native build/parity gate;
- real Provider smoke/calibration where required.

The PR must remain Draft while those gates have no real runner/step evidence.

## 8. Definition of Done

### Source migration

- [x] one Client / AsyncClient public business layer;
- [x] one QueryPlanner and UnifiedRuntime;
- [x] explicit Provider provenance including derived/composite work;
- [x] all known legacy business abilities represented in migration contract;
- [x] migrated capability catalog feeds Registry and DirectBinding;
- [x] all protocol surfaces expose migrated capability execution;
- [x] retired facade/router semantics remain removed.

### Release evidence

- [ ] latest exact SHA receives runners;
- [ ] all blocking jobs execute real steps;
- [ ] all blocking gates pass on that same SHA;
- [ ] real network/provider calibration is completed where required;
- [ ] only then move PR out of Draft / merge.
