# ATST Core Functionality Readiness V1

> Branch: `fix/python-release-pipeline`  
> PR: #8 (Draft)  
> Date: 2026-09-28  
> Scope: canonical core runtime and its public surfaces, not every migrated/catalog capability.

## 1. Conclusion

The canonical ATST core path is structurally connected, but **not every published capability can be
claimed as normally usable**.

The seven dedicated core capabilities are:

- `quotes`
- `bars`
- `snapshot`
- `minute`
- `trades`
- `security_count`
- `security_list`

After this review:

| Capability | Omitted-provider default | Code path | Existing live evidence | Current verdict |
|---|---|---|---|---|
| `quotes` | TDX | connected | TDX live probe exists | usable, pending latest-head CI execution |
| `bars` | TDX | connected | TDX daily + intraday live probes exist | usable, pending latest-head CI execution |
| `snapshot` | TDX | connected, composed quote + bar context | TDX live probe exists | usable, pending latest-head CI execution |
| `minute` | Tencent operational default | connected through Web adapter | no exact-head live run yet | code-complete / externally unverified |
| `trades` | Tencent operational default | connected through Web adapter | no exact-head live run yet | code-complete / externally unverified |
| `security_count` | TDX | connected | TDX live probe exists | usable, pending latest-head CI execution |
| `security_list` | TDX canonical fail-fast | intentionally offline | command ledger says no response | currently unavailable |

Therefore the correct statement is:

```text
6/7 dedicated core capabilities have an operational execution path.
1/7 (security_list) is intentionally unavailable and remains fail-closed.
```

This does **not** mean every catalog/migrated capability is live-verified. `Client.capabilities()`
remains a names-only discovery surface by prior compatibility decision; a published name is not a
guarantee of current operational availability.

## 2. Canonical execution path

The intended core path is:

```text
Python Client / AsyncClient
        |
CLI / HTTP / WS / MCP
        |
        v
Client semantic method / Client.call
        |
        v
QuerySpec
        |
        v
QueryPlanner
        |
        v
one Provider + one Channel
        |
        v
UnifiedRuntime
        |
        v
DirectProviderExecutor
        |
        +--> TDX protocol client / transport
        +--> Web direct adapter
        +--> local_vipdoc reader
        |
        v
QueryResult + Provenance
        |
        v
serialization / output
```

Cross-provider fallback is not hidden inside this path. It remains an explicit
`FallbackPolicy`/orchestrator operation.

## 3. Review round 1 — default-call usability

### Problem found

The public API exposed `minute` and `trades` as core methods, and operational Web providers were
already registered, but every convenience surface forced TDX by default.

TDX currently cannot execute these structured requests:

- `minute`: TDX command 0x0537 request/parser is still inferred and the client raises
  `NotImplementedFeature` before sending.
- `trades`: TDX command 0x0FC5 has the same structured-API guard.

This created the following broken shape:

```text
Client.minute() / Client.trades()
CLI
HTTP
WS
MCP
   -> implicit TDX
   -> deterministic fail-fast
```

while usable adapters already existed for:

- minute: Tencent / Eastmoney / Baidu;
- trades: Tencent / Baidu.

### Fix

Provider Registry now distinguishes:

```text
declared capability
vs
operationally available capability
```

TDX keeps its declarations so an explicit TDX request still returns the correct protocol-specific
exception. The registry marks the currently unavailable core TDX capabilities separately.

For an omitted provider:

1. keep the configured/default Provider when it is operational;
2. if it declares the capability but is operationally unavailable, use the registry operational
   default;
3. if no operational Provider exists, keep the canonical default and preserve its domain exception.

Explicit per-request Provider choice is never replaced.

### Result

Current defaults:

```text
quotes          -> tdx
bars            -> tdx
snapshot        -> tdx
minute          -> tencent
trades          -> tencent
security_count  -> tdx
security_list   -> tdx -> CommandOffline
```

## 4. Review round 2 — discovery honesty and compatibility

### Problem found

Changing the main capability discovery payload to include availability would have violated an
existing compatibility decision: `Client.capabilities()`, HTTP `/v13/capabilities`, and WS
`runtime.capabilities` are names-only discovery surfaces.

The repository already contains architecture tests that intentionally enforce:

```text
name published != operational availability guaranteed
```

### Fix

The names-only discovery shape remains unchanged.

A separate Python API was added:

```python
Client.core_capability_statuses()
AsyncClient.core_capability_statuses()
```

It returns, for the dedicated core surface:

- `available`
- `declared_providers`
- `operational_providers`
- `default_provider`

HTTP and WS health surfaces expose only an additive summary:

```text
core_unavailable
```

This preserves the prior discovery contract while giving operators a truthful core-health signal.

## 5. Review round 3 — planner boundary and all public surfaces

### Problem found

Fixing only `Client.minute()` / `Client.trades()` would still leave a second broken path:

```python
UnifiedRuntime.execute(QuerySpec.build("minute", ...))
```

`UnifiedRuntime` and `QuerySpec` are root-package public APIs, so a Client-only fix would create
two different provider-selection semantics.

### Fix

Operational default resolution was moved to the canonical QuerySpec normalization/planning
boundary.

Consequently the same rule now applies to:

- `Client.minute()`
- `Client.trades()`
- `Client.call("minute", ...)`
- `Client.call("trades", ...)`
- `AsyncClient`
- `UnifiedRuntime.minute()`
- `UnifiedRuntime.trades()`
- `UnifiedRuntime.execute(QuerySpec(...))`
- CLI
- HTTP
- WebSocket JSON-RPC
- MCP tools

No caller-specific default table is required.

## 6. Explicit TDX behavior is preserved

The fix is not a hidden fallback.

These calls continue to target TDX:

```python
client.minute("sh600519", provider="tdx")
client.trades("sh600519", provider="tdx")
```

and therefore continue to expose TDX's real structured-command limitation.

This preserves the Provider identity trust boundary.

The automatic operational selection happens only when the request does not pin a Provider.

## 7. security_list

`security_list` is different from minute/trades.

TDX command 0x044D is recorded offline after multi-host no-response measurements, and no other
Provider currently declares an operational security catalog page.

The implementation therefore intentionally remains:

```text
security_list
 -> TDX
 -> fail-fast CommandOffline
```

The API is retained for compatibility and future protocol correction.

It must not be described as operationally available until a real Provider implementation exists.

## 8. TDX live-evidence boundary

The repository's live probe currently exercises the following real 7709 chain:

- `security_count`
- daily `bars`
- live `quotes`
- composed `snapshot`
- intraday 1-minute `bars` during the trading session

The probe is intentionally fail-closed for real network failure and only skips the intraday timing
assertion when the market session condition does not apply.

This is stronger evidence than static registration, but the latest PR #8 GitHub Actions runs have
not actually received runners, so there is no latest-head execution evidence yet.

## 9. Web minute/trades boundary

The operational default path now resolves to Tencent:

```text
minute -> tencent/minute -> direct adapter
trades -> tencent/ticks  -> direct adapter
```

Alternative explicit providers remain available where registered:

```text
minute: eastmoney, baidu
trades: baidu
```

These paths are structurally connected through catalog bindings and `DirectProviderExecutor`.

They should not be called "live-verified on the latest head" until a real network probe executes on
that exact head.

A future hardening step should add a bounded external live smoke for the operational Web defaults,
separate from deterministic PR merge gates.

## 10. Streaming

Canonical streaming remains quote/snapshot based and defaults to TDX, whose required core chain is
operationally declared.

The streaming subsystem has explicit lifecycle states and fail-closed start/stop behavior:

- one worker per stream;
- startup failure enters failed state;
- unexpected worker exit is recorded;
- stop paths are bounded/guarded;
- no silent second worker is spawned;
- backpressure queue accounting is explicit.

This review found no new stream lifecycle disconnect.

## 11. local_vipdoc

The local historical path is independent from live TDX identity:

```text
provider = local_vipdoc
channel  = vipdoc
periods  = day / 1min / 5min
```

It requires an explicit `vipdoc_root`.

It does not silently impersonate live TDX and does not silently apply adjustment.

This review found no new local-reader routing disconnect.

## 12. Output layer

The output layer currently has four user-facing forms:

- DataFrame;
- Parquet;
- CSV;
- DuckDB.

Important contracts:

- optional dependencies fail explicitly;
- CSV/Parquet use temporary-file + atomic replace;
- DuckDB connection closes in `finally`;
- invalid DuckDB table names fail before SQL construction;
- output format inference fails rather than silently returning a DataFrame for an unknown target.

No new core output-chain break was identified in this review.

## 13. HTTP / WS / MCP

### HTTP

Core semantic routes translate to the same Client methods. Minute/trades no longer force TDX when
the provider is omitted.

`/v13/runtime/health` reports the unavailable core summary.

### WebSocket JSON-RPC

Minute/trades no longer synthesize `provider="tdx"`. They delegate omitted-provider semantics to
Client/Planner.

`runtime.health` reports the same unavailable core summary.

### MCP

The minute/trades handlers no longer force TDX.

Descriptions now say:

- omitted Provider uses an operational Web provider;
- explicit TDX remains unavailable for these two structured capabilities.

Snapshot wording was corrected: the canonical snapshot is quote + bar context and does not claim
orderbook depth that the current implementation does not provide.

## 14. Cross-surface tests added

`tests/runtime/test_core_operational_defaults.py` now protects:

- declared vs operational Provider state;
- minute default = Tencent;
- trades default = Tencent;
- security_list has no operational Provider;
- explicit Provider is never replaced;
- Client convenience methods;
- generic `Client.call`;
- QueryPlanner;
- UnifiedRuntime;
- CLI defaults;
- HTTP defaults;
- WS defaults;
- MCP defaults;
- HTTP health unavailable summary;
- WS health unavailable summary.

The tests use the real planner with a recording executor so they verify planning semantics without
performing network I/O.

## 15. What is not proven

The following claims would be too strong at this point:

- "all 170+ capability names are live usable";
- "all Web providers are currently reachable";
- "minute/trades have been live-tested on the latest PR head";
- "latest PR code passed Ruff/mypy/pytest";
- "security_list is usable".

The current GitHub Actions infrastructure has repeatedly failed before runner assignment. Therefore
the new tests exist in source but have not yet executed in GitHub Actions.

## 16. Final readiness classification

### A — core path with existing live probe

- quotes
- bars
- snapshot
- security_count

### B — core path structurally complete, operational Web provider available, latest-head live proof pending

- minute
- trades

### C — intentionally unavailable / fail-closed

- security_list

### D — additional migrated/catalog capabilities

Names are discoverable and bound through the catalog/runtime architecture, but availability must be
evaluated per Provider/capability. They are outside the assertion "all core functions are normally
usable."

## 17. Acceptance criteria before declaring full core readiness

Required:

- [ ] GitHub runner is actually assigned on the latest head;
- [ ] Ruff runs and passes;
- [ ] mypy runs and passes;
- [ ] offline pytest suite runs and passes;
- [ ] `test_core_operational_defaults.py` runs and passes;
- [ ] existing TDX live smoke executes successfully;
- [ ] bounded Tencent minute live smoke passes;
- [ ] bounded Tencent trades live smoke passes;
- [ ] no new Provider/catalog audit drift;
- [ ] no lifecycle/reachability gate regression.

Even after these pass, `security_list` should remain documented unavailable until protocol evidence
changes.

## 18. Final statement

The core architecture is now internally connected and the previous default-routing disconnect for
minute/trades is repaired across all public surfaces.

The accurate current claim is:

```text
ATST has 6 operationally routable dedicated core capabilities out of 7.
Four have existing TDX live-probe coverage.
Two (minute/trades) now have complete operational Web routes but still need latest-head live evidence.
One (security_list) is intentionally offline and fail-closed.
```

That is a stronger and more truthful industrial-readiness statement than saying "all capabilities
work" simply because their names are registered.
