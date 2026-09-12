# tstdx v13 Implementation Alignment Matrix

> Companion to `ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md` and `REFACTOR_PLAN_v13_CLEAN_BREAK.md`.
>
> This matrix distinguishes **source alignment** from **verification evidence**. `SOURCE-ALIGNED` means the repository source now follows the v13 contract. It does **not** mean CI is green. A row becomes `VERIFIED` only when the exact head SHA receives real runners and executes the blocking gates successfully.

---

## 1. Status vocabulary

- `SOURCE-ALIGNED` — one v13 source interpretation remains and deterministic gates exist.
- `SOURCE-PARTIAL` — v13 foundation exists but a duplicate path or missing link remains.
- `NOT-PROMOTED` — historical implementation may exist, but the capability is intentionally outside the supported v13 Registry until rebuilt end to end.
- `REMOVED` — retired architecture physically removed from the canonical source tree/supported surface.
- `VERIFICATION-PENDING` — source work is aligned but exact-head CI has not produced real executed evidence.
- `VERIFIED` — same exact SHA executed the blocking gate and passed.

`steps=[]`, `steps=null`, `runner_id=0`, skipped/disabled/soft-failed jobs and workflow-level success without executed blocking jobs are never `VERIFIED`.

---

## 2. Semantic and core matrix

| Area | Source state | v13 contract | Evidence state |
|---|---|---|---|
| Provider identity | SOURCE-ALIGNED | one canonical Provider ID; `web` rejected | VERIFICATION-PENDING |
| Channel identity | SOURCE-ALIGNED | one canonical channel per QueryPlan | VERIFICATION-PENDING |
| Capability identity | SOURCE-ALIGNED for promoted set | Registry == DirectBinding | VERIFICATION-PENDING |
| QuerySpec | SOURCE-ALIGNED | sole query SSOT; no `source`, `route`, generic `allow_partial` | VERIFICATION-PENDING |
| QueryFingerprint | SOURCE-ALIGNED | schema v2; Provider/Channel/capability/symbol/market/period/range/adjustment/currentness/max-age/options | VERIFICATION-PENDING |
| QueryPlanner | SOURCE-ALIGNED | sole intent-to-plan compiler | VERIFICATION-PENDING |
| UnifiedRuntime | SOURCE-ALIGNED | sole single-Provider kernel | VERIFICATION-PENDING |
| ProviderOrchestrator | SOURCE-ALIGNED | only cross-Provider fallback executor | VERIFICATION-PENDING |
| Result/Provenance | SOURCE-ALIGNED | sole success contract; no `.source` alias | VERIFICATION-PENDING |
| ErrorEnvelope | SOURCE-ALIGNED | sole safe external failure contract | VERIFICATION-PENDING |
| Batch | SOURCE-ALIGNED | `BatchSpec/BatchResult`; partial success never on QuerySpec | VERIFICATION-PENDING |
| Streaming | SOURCE-ALIGNED for promoted stream | `StreamSpec -> StreamPlan -> Stateful -> UnifiedRuntime`; TDX quotation only | VERIFICATION-PENDING |
| L1 cache | SOURCE-ALIGNED | full fingerprint + bounded deterministic LRU + metrics | VERIFICATION-PENDING |
| L2 cache | SOURCE-ALIGNED | JSON/SQLite v2 + embedded identity + SHA-256 integrity + DIRECT-only | VERIFICATION-PENDING |
| Negative cache | SOURCE-ALIGNED | separate terminal-only bounded LRU | VERIFICATION-PENDING |
| SingleFlight | SOURCE-ALIGNED | full-fingerprint coalescing + deep isolation | VERIFICATION-PENDING |
| Python API | SOURCE-ALIGNED | `Client/AsyncClient` only | VERIFICATION-PENDING |
| HTTP | SOURCE-ALIGNED | one `/v13` Client-backed surface | VERIFICATION-PENDING |
| WebSocket | SOURCE-ALIGNED | one JSON-RPC handler + thin `/v13/ws` transport | VERIFICATION-PENDING |
| MCP | SOURCE-ALIGNED | Client-only canonical promoted tools | VERIFICATION-PENDING |
| CLI | SOURCE-ALIGNED | Client-only Tier-A + Stateful stream | VERIFICATION-PENDING |
| Task store | SOURCE-ALIGNED infrastructure | bounded result/error retention; no business routing | VERIFICATION-PENDING |
| DataSourceRouter | REMOVED | no business runtime role | VERIFICATION-PENDING |
| UnifiedQuoteAPI | REMOVED | unsupported/physically removed | VERIFICATION-PENDING |
| RouteSelector | REMOVED | unsupported/physically removed | VERIFICATION-PENDING |
| legacy HTTP/WS servers | REMOVED | replaced by canonical runtime transports | VERIFICATION-PENDING |
| legacy QuoteStream APIs | REMOVED from public/core surface | Stateful runtime is the only business stream | VERIFICATION-PENDING |

---

## 3. Canonical front-to-back flow

### 3.1 Query request

```text
Python / Async / CLI / HTTP / WS / MCP
                |
                v
         Client / AsyncClient
                |
                v
             QuerySpec
                |
                v
       normalize + validate
                |
                v
        QueryPlanner.compile
                |
                v
             QueryPlan
                |
                v
          UnifiedRuntime
```

Rules:

- every supported business surface constructs or delegates to the same QuerySpec semantics;
- no surface owns route order;
- no surface calls DataSourceRouter;
- no surface uses `web` as a Provider;
- unsupported semantics fail before Provider I/O where possible.

### 3.2 Single-Provider execution

```text
QueryPlan
  -> L1 semantic cache
  -> L2 semantic cache + integrity/freshness verification
  -> terminal negative cache
  -> SingleFlight(full fingerprint)
  -> leader cache re-check
  -> exact DirectBinding
  -> Provider-native implementation
  -> normalized data
  -> QueryResult / ResultMeta / Provenance
  -> L1 write
  -> best-effort eligible L2 write
```

No node above is allowed to select a second Provider.

### 3.3 Explicit cross-Provider policy

```text
QuerySpec + FallbackPolicy[A,B,C]
  -> ProviderOrchestrator
  -> UnifiedRuntime(provider=A)
  -> record attempt
  -> UnifiedRuntime(provider=B) only if policy permits
  -> ...
  -> OrchestratedResult
```

Fallback provenance records requested and actual Provider. Fallback results do not enter DIRECT-only L2.

### 3.4 Streaming

```text
Client.stream
  -> StreamSpec
  -> StreamPlanner
  -> exact tdx/quotation stream plan
  -> StatefulQuoteStream / AsyncStatefulQuoteStream
  -> UnifiedRuntime.quotes(use_cache=False)
  -> QueryPlan / DirectBinding
  -> normalized quote events
  -> StreamState lifecycle
```

The retired QuoteStream/AsyncQuoteStream business kernel is not used.

### 3.5 Output

```text
QueryResult / OrchestratedResult / BatchResult
  -> shared serializer
  -> Python / CLI / HTTP / WS / MCP framing
```

Transport framing differs; Provider/Channel/Fingerprint/Provenance/Error semantics do not.

---

## 4. Tier-A core capability matrix

Tier A defines the v13 core that must be complete before the project can claim core-function alignment.

| Capability | QuerySpec | Registry | Direct binding | Runtime | Client/Async | HTTP | WS | MCP | CLI | Source state |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| quotes | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| bars | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| snapshot/orderbook | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| minute/intraday | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| trades/ticks | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| security_count | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |
| security_list | yes | yes | yes | yes | yes | yes | yes | yes | yes | SOURCE-ALIGNED |

Provider support is intentionally narrower than historical helper availability. See `PROVIDER_CAPABILITY_MATRIX_v13.md`.

---

## 5. Provider matrix rule

The supported Registry is deliberately limited to executable v13 rows:

- `tdx/quotation`: Tier A seven capabilities;
- `local_vipdoc/vipdoc`: bars (day/1min/5min);
- `tencent`: quotes + exact daily/minute bars channels;
- `sina`: quotes + history bars;
- `eastmoney`: quotes + kline bars;
- `baidu`: quotes + kline bars.

The following families are currently `NOT-PROMOTED`, even if historical modules remain in the repository:

- finance/fundamental;
- corporate actions/capital changes/factors;
- F10;
- blocks/sectors/index catalogs/rankings/auction/volume-price;
- funds;
- bonds;
- futures;
- options;
- commodities/extended markets;
- FX/rates;
- search/suggest;
- news/events;
- provider-specific supplemental datasets.

They are **not core gaps** unless intentionally retained in the v13 product scope. A family is promoted only after all of these exist together:

1. typed request semantics;
2. Provider/Channel Registry declaration;
3. exact Direct binding;
4. normalized result model;
5. QueryResult/Provenance behavior;
6. ErrorEnvelope behavior;
7. required public adapters;
8. deterministic contract tests.

Otherwise it remains outside the supported API instead of being represented by a facade-only method.

---

## 6. Clean-break removal matrix

| Legacy semantic/component | State |
|---|---|
| QuerySpec `source` | REMOVED |
| QuerySpec generic `allow_partial` | REMOVED |
| public `route=` | REMOVED from canonical API |
| `route="auto"` | REMOVED |
| `web` pseudo-provider | REJECTED |
| ResultMeta `.source` alias | REMOVED |
| `UnifiedQuoteAPI` | REMOVED |
| `AsyncUnifiedQuoteAPI` | REMOVED |
| `quote_api()` / facade runtime factory | REMOVED |
| `RouteSelector` / `_try_routes` | REMOVED |
| facade-owned circuit/fallback state | REMOVED with facade |
| `DataSourceRouter` business kernel | REMOVED |
| hidden router cache/synthetic fallback | REMOVED with router kernel |
| legacy HTTP server | REMOVED |
| legacy WS server | REMOVED |
| MCP facade/TdxClient dual target | REMOVED |
| MCP `use_facade` | REMOVED |
| legacy CLI route/web/host business commands | REMOVED |
| legacy QuoteStream/AsyncQuoteStream public/core APIs | REMOVED |
| v12 compatibility-cutover document | REMOVED |
| tests requiring old public compatibility | REMOVED/REWRITTEN |

---

## 7. Cache/concurrency alignment

### L1

- full QueryFingerprint identity;
- redundant Provider/Channel/capability/provenance verification;
- freshness/currentness check on read;
- bounded LRU eviction;
- hit/miss/reject/evict counters;
- deep-copy isolation.

### L2

- SQLite + deterministic safe JSON;
- schema/table v2;
- codec v2;
- embedded fingerprint;
- Provider/Channel/capability redundancy;
- SHA-256 payload integrity covering identity, timing, provenance and data;
- constant-time digest comparison;
- DIRECT non-fallback only;
- L2->L1 promotion respects remaining TTL;
- corruption/tampering becomes invalidation/miss;
- L2 failure cannot fail a valid Provider query.

### Negative cache

- separate from successful empty data;
- full fingerprint key;
- only terminal non-retryable TdxError subclasses;
- SourceUnavailable/transient errors excluded;
- short TTL;
- bounded LRU;
- success invalidates prior negative entry.

### SingleFlight

- full fingerprint key;
- one leader;
- independent follower/leader values;
- cloned exceptions;
- process-control BaseException not normalized.

---

## 8. Surface parity

| Contract | Python | Async | CLI | HTTP | WS | MCP |
|---|---:|---:|---:|---:|---:|---:|
| explicit Provider | yes | yes | yes | yes | yes | yes |
| QuerySpec semantics | direct | same Client core | Client | Client | Client | Client |
| fallback policy | explicit | explicit | explicit for quotes/bars | explicit for quotes/bars | explicit for quotes/bars | no hidden fallback |
| canonical result metadata | object | object | serialized | serialized | serialized | serialized |
| ErrorEnvelope | domain exception/context | same | stderr JSON | HTTP framing | JSON-RPC framing | JSON-RPC/tool framing |
| no route/source semantics | yes | yes | yes | yes | yes | yes |

MCP intentionally exposes only promoted canonical tools; it does not automatically mirror every historical helper.

---

## 9. Deterministic gates present in source

### Architecture

- QuerySpec has no `source`, `route`, generic `allow_partial`;
- top-level business API is Client/AsyncClient-first;
- retired facade has no supported exports;
- `web` Provider is rejected;
- Registry set equals DirectBinding set;
- TDX Tier A Registry set is exact;
- streaming non-bound Provider fails closed;
- MCP manifest contains promoted canonical tools only.

### Runtime/contracts

- Query normalization/planning/fingerprint tests;
- Provider/channel isolation;
- unsupported semantics fail closed;
- QueryResult/Provenance identity;
- ErrorEnvelope sanitization;
- explicit orchestration attempt/provenance tests;
- BatchSpec/BatchResult auditability;
- SingleFlight deep isolation;
- terminal negative-cache policy;
- local_vipdoc exact reader paths.

### Cache

- L1 semantic identity/freshness;
- L2 domain-model roundtrip;
- copied-row poisoning rejection;
- provenance/payload tamper rejection;
- DIRECT-only persistence;
- verified L2 promotion without Provider I/O.

### Transport

- `/v13` HTTP validation/error gates;
- JSON-RPC v13 handler gates;
- real loopback HTTP smoke;
- real loopback WebSocket smoke;
- MCP stdio smoke;
- CLI ErrorEnvelope/process-control behavior.

### Streaming

- Stateful synchronous runtime polling;
- Stateful async runtime polling;
- one-shot lifecycle;
- worker fail-closed invariant;
- DeltaMerger/Backpressure/Gap/Reconnect components.

---

## 10. Verification state

Source alignment is **not** the same as release evidence.

At the time of this matrix update, GitHub Actions infrastructure has repeatedly produced jobs with no executed steps / no assigned runner. Therefore:

- source-level v13 core semantics: `SOURCE-ALIGNED`;
- Tier-A core function chain: `SOURCE-ALIGNED`;
- supported front-to-back surfaces: `SOURCE-ALIGNED`;
- deterministic CI evidence on the latest exact SHA: `VERIFICATION-PENDING`;
- real Provider network smoke/calibration: `VERIFICATION-PENDING`.

No PR may leave Draft solely because source inspection looks correct.

---

## 11. Definition of Done

### Semantic DoD

- [x] one Provider/Channel/Capability vocabulary for supported v13 runtime;
- [x] QuerySpec/StreamSpec are canonical requests;
- [x] full semantic fingerprint identity;
- [x] old route/source ambiguity removed;
- [x] no silent provider/parameter downgrade in canonical planner/bindings.

### Core-flow DoD

- [x] one QueryPlanner;
- [x] one single-Provider UnifiedRuntime;
- [x] one explicit ProviderOrchestrator;
- [x] one QueryResult/Provenance model;
- [x] one ErrorEnvelope;
- [x] one cache identity model;
- [x] Stateful streaming executes through UnifiedRuntime.

### Core-function DoD

- [x] all Tier-A capabilities have QuerySpec/Registry/Binding/Runtime/Client chains;
- [x] supported Provider capability claims equal executable Direct bindings;
- [x] non-promoted historical families are not falsely advertised as supported v13 runtime capabilities.

### Front-to-back DoD

- [x] Python/Async/CLI/HTTP/WS/MCP business requests use the canonical Client/runtime chain;
- [x] successful transport output uses shared canonical serialization;
- [x] external errors use canonical ErrorEnvelope semantics;
- [x] fallback is explicit/auditable;
- [x] legacy facade/router/old HTTP/WS business paths are removed from canonical execution;
- [x] real loopback HTTP/WS and MCP stdio smoke gates exist.

### Release evidence DoD

- [ ] exact-head blocking CI jobs receive runners;
- [ ] exact-head Ruff executes and passes;
- [ ] exact-head mypy executes and passes;
- [ ] exact-head pytest/coverage executes and passes;
- [ ] exact-head architecture/spec/golden/adversarial/reachability/docs gates execute and pass;
- [ ] exact-head Native parity gate executes and passes;
- [ ] required real Provider smoke/calibration is recorded.

Only when the **Release evidence DoD** is complete may the project state that v13 is fully verified and release-ready.
