# tstdx

> Provider-first market-data runtime and TDX protocol toolkit.
>
> Current package version: **v1.0.0**. The `refactor/runtime-integration-v12` branch is converging on the **v13 clean-break architecture** described in `docs/ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md`.

## v13 architecture

The supported business API is intentionally small and explicit:

```text
Python / Async / CLI / HTTP / WS / MCP
                |
                v
         Client / AsyncClient
                |
        +-------+--------+
        |                |
        v                v
     QuerySpec       FallbackPolicy
        |                |
        v                v
   QueryPlanner   ProviderOrchestrator
        |                |
        +-------+--------+
                v
          UnifiedRuntime
                |
   L1 -> L2 -> NegativeCache -> SingleFlight
                |
                v
        exact DirectBinding
                |
                v
   Provider-native implementation
                |
                v
 QueryResult / ResultMeta / Provenance
```

The kernel executes **one Provider + one Channel** per QueryPlan. Cross-Provider fallback is never implicit; it exists only through an explicit `FallbackPolicy` handled by `ProviderOrchestrator`.

There is no public `route=`, no `route="auto"`, no pseudo-provider `web`, and no facade/router fallback kernel.

## Core semantics

- **Provider**: canonical data authority such as `tdx`, `local_vipdoc`, `tencent`, `sina`, `eastmoney`, `baidu`.
- **Channel**: Provider-native endpoint/transport family selected by the planner.
- **Capability**: business operation such as `quotes`, `bars`, `snapshot`, `minute`, `trades`.
- **QuerySpec**: immutable request SSOT.
- **QueryPlan**: one executable Provider/Channel plan.
- **QueryFingerprint**: full semantic identity for cache and SingleFlight.
- **UnifiedRuntime**: sole single-Provider query kernel.
- **ProviderOrchestrator**: sole cross-Provider fallback executor.
- **QueryResult / ResultMeta / Provenance**: canonical success contract.
- **ErrorEnvelope**: canonical safe external failure contract.
- **StreamSpec / StreamPlan / StatefulQuoteStream**: canonical streaming contract and lifecycle.

## Supported v13 Provider capabilities

The Registry is executable truth: a capability is listed only after it has a canonical Query contract and an exact Direct binding.

| Provider | Channel | Capabilities |
|---|---|---|
| `tdx` | `quotation` | `quotes`, `bars`, `snapshot`, `minute`, `trades`, `security_count`, `security_list` |
| `local_vipdoc` | `vipdoc` | `bars` (`day`, `1min`, `5min`) |
| `tencent` | `quote` | `quotes` |
| `tencent` | `kline` | historical `bars` |
| `tencent` | `minute_kline` | minute `bars` |
| `sina` | `quote` | `quotes` |
| `sina` | `history_kline` | `bars` |
| `eastmoney` | `quote` | `quotes` |
| `eastmoney` | `kline` | `bars` |
| `baidu` | `quote` | `quotes` |
| `baidu` | `kline` | `bars` |

See `docs/PROVIDER_CAPABILITY_MATRIX_v13.md` for the executable support contract.

Historical modules may still contain finance, F10, fund, futures, bonds, options, rankings, news or other provider-specific helpers. Those are **not** supported v13 business capabilities until they are promoted end to end through QuerySpec + Registry + DirectBinding + Result/Provenance + surface adapters + contract tests.

## Installation

```bash
pip install tstdx
pip install "tstdx[all]"
pip install "tstdx[web,server,mcp]"
pip install -e ".[dev]"
```

Optional extras remain available for provider transports, DataFrame/Parquet/DuckDB output, metrics, HTTP/WebSocket server dependencies, MCP and development tooling.

## Python quick start

### Explicit single Provider

```python
from tstdx import Client

with Client() as client:
    quotes = client.quotes(
        ["sh600519", "sz000001"],
        provider="tdx",
    )

    bars = client.bars(
        "sh600519",
        provider="eastmoney",
        period="day",
        count=80,
    )

    snapshot = client.snapshot("sh600519", provider="tdx")
    minute = client.minute("sh600519", provider="tdx")
    trades = client.trades("sh600519", provider="tdx", count=100)
    count = client.security_count(market=1, provider="tdx")
    securities = client.security_list(market=1, start=0, provider="tdx")
```

Every successful call returns a `QueryResult` whose `meta` records Provider, Channel, capability, fingerprint and provenance.

### Explicit cross-Provider fallback

```python
from tstdx import Client, FallbackPolicy

policy = FallbackPolicy.build("tdx", "tencent", "sina")

with Client() as client:
    result = client.quotes(
        ["sh600519"],
        policy=policy,
    )
```

Fallback is auditable. The actual successful Provider stays in result metadata, while provenance records the requested Provider and whether fallback occurred.

### Raw QuerySpec execution

```python
from tstdx import Client, QuerySpec

spec = QuerySpec.build(
    "bars",
    symbols="sh600519",
    provider="tdx",
    period="day",
    count=120,
    currentness="historical",
)

with Client() as client:
    result = client.execute(spec)
```

### Async

`AsyncClient` uses the same semantic core instead of maintaining a second business architecture.

```python
import asyncio
from tstdx import AsyncClient


async def main():
    async with AsyncClient() as client:
        quotes, bars = await asyncio.gather(
            client.quotes(["sh600519"], provider="tdx"),
            client.bars("sh600519", provider="tdx", period="day", count=80),
        )
        print(quotes.meta.provider, bars.meta.provider)


asyncio.run(main())
```

## Batch

Partial success belongs to the explicit batch contract; normal QuerySpec has no `allow_partial` field.

```python
from tstdx import Client

with Client() as client:
    result = client.quotes_batch(
        ["sh600519", "sz000001"],
        provider="tdx",
    )

    print(result.failed)
    print(result.missing)
```

## Stateful streaming

Streaming uses the same UnifiedRuntime quote execution path. It does not maintain a second TdxClient-based business kernel.

```python
import time
from tstdx import Client


def on_quote(symbol, quote):
    print(symbol, quote.get("price"))


with Client() as client:
    stream = client.stream(
        ["sh600519", "sz000001"],
        provider="tdx",
        interval=1.0,
        diff_only=True,
        on_quote=on_quote,
    )
    stream.start()
    try:
        time.sleep(10)
    finally:
        stream.stop()
```

Current canonical Direct stream binding is `tdx/quotation`. Unsupported stream Providers fail before worker startup.

## CLI

The v13 CLI is a thin Client adapter:

```bash
tstdx version
tstdx quotes sh600519 sz000001 --provider tdx
tstdx bars sh600519 --provider eastmoney --period day --count 80
tstdx snapshot sh600519 --provider tdx
tstdx minute sh600519 --provider tdx
tstdx trades sh600519 --provider tdx --count 100
tstdx security-count --provider tdx --market 1
tstdx security-list --provider tdx --market 1 --start 0

# Explicit fallback, never implicit route="auto"
tstdx quotes sh600519 --fallback tdx,tencent,sina
```

Normal CLI failures are rendered with the same ErrorEnvelope used by the other external transports. KeyboardInterrupt remains process control and exits with code 130.

## HTTP

Install the server extra and create the canonical app:

```python
from tstdx.integration import create_runtime_app

app = create_runtime_app()
```

Canonical endpoints:

- `GET /v13/quotes`
- `GET /v13/bars/{symbol}`
- `GET /v13/snapshot/{symbol}`
- `GET /v13/minute/{symbol}`
- `GET /v13/trades/{symbol}`
- `GET /v13/security/count`
- `GET /v13/security/list`
- `GET /v13/runtime/health`

HTTP owns framing only. Business execution goes through Client/UnifiedRuntime.

## WebSocket JSON-RPC

The canonical WebSocket path is `/v13/ws`.

```python
from tstdx.integration import RuntimeWsConfig, serve_runtime_ws

server = await serve_runtime_ws(
    config=RuntimeWsConfig(host="127.0.0.1", port=8765, path="/v13/ws")
)
```

Supported JSON-RPC business methods mirror the promoted Tier-A runtime:

- `quotes`
- `bars`
- `snapshot`
- `minute`
- `trades`
- `security.count`
- `security.list`
- `runtime.health`

## MCP

The MCP stdio adapter uses the same Client and exposes only promoted canonical capabilities:

- `get_bars`
- `get_quote`
- `get_quotes`
- `get_snapshot`
- `get_minute_today`
- `get_trades`
- `get_security_count`
- `get_security_list`

There is no `use_facade`, no dual TdxClient/Facade execution target, and no hidden Provider fallback.

## Cache and concurrency model

Canonical runtime order:

```text
QueryPlan
  -> L1 semantic cache
  -> L2 persistent semantic cache
  -> terminal negative cache
  -> SingleFlight
  -> exact DirectBinding
  -> QueryResult/Provenance
```

Key properties:

- full QueryFingerprint identity;
- L1 bounded LRU, never whole-map flush at capacity;
- L2 safe SQLite + deterministic JSON; no pickle;
- L2 payload identity/hash verification before decode;
- DIRECT non-fallback persistence only;
- L2->L1 promotion cannot extend original expiry;
- transient/source-unavailable failures are never negative-cached;
- SingleFlight leader/followers receive isolated values.

## Low-level protocol toolkit

`tstdx` still contains the underlying TDX protocol, parser, codec, transport, reader and provider adapter implementations. These are implementation/advanced-tooling layers rather than parallel business APIs.

Examples include:

```text
tstdx/protocol/
tstdx/codec/
tstdx/transport/
tstdx/client/       # low-level TDX transport client implementation
tstdx/web/          # concrete HTTP Provider adapters
tstdx/reader/       # local vipdoc Provider readers
tstdx/domain/
```

Import low-level components from their explicit submodules only when implementing protocol/provider tooling. Application code should use `tstdx.Client` / `tstdx.AsyncClient`.

## Source layout

```text
tstdx/
├── client_api.py            # Client / AsyncClient — supported business API
├── query.py                 # QuerySpec / QueryPlan / QueryPlanner / fingerprint
├── providers/               # executable Provider Registry SSOT
├── direct_provider.py       # exact Provider/Channel/Capability bindings
├── runtime.py               # single-Provider execution kernel
├── orchestration.py         # explicit cross-Provider policy only
├── result.py                # QueryResult / ResultMeta / Provenance
├── batch.py                 # BatchSpec / BatchResult / SingleFlight / NegativeCache
├── cache_semantic.py        # L1 semantic LRU
├── cache_persistent.py      # safe persistent L2
├── stream_contract.py       # StreamSpec / StreamPlan / StreamPlanner
├── streaming/               # Stateful lifecycle + reusable stream components
├── integration/             # canonical HTTP / WS / MCP / task adapters
├── cli/                     # Client-backed CLI
├── client/                  # low-level TDX Provider implementation
├── web/                     # concrete HTTP Provider implementations
├── reader/                  # local vipdoc Provider readers
├── protocol/                # protocol command/parse SSOT
├── codec/                   # frame/primitive codecs
└── transport/               # network transport/pool/host management
```

## Architecture documents

Read these in order:

1. `docs/ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md` — authoritative semantic/core SSOT.
2. `docs/REFACTOR_PLAN_v13_CLEAN_BREAK.md` — clean-break implementation roadmap.
3. `docs/IMPLEMENTATION_ALIGNMENT_MATRIX_v13.md` — current source/evidence status.
4. `docs/PROVIDER_CAPABILITY_MATRIX_v13.md` — executable Provider support matrix.
5. `docs/V13_REFACTOR_EXECUTION_STATUS.md` — implementation progress and merge evidence rules.
6. `docs/adr/` — irreversible architecture decisions.
7. `PROTOCOL_SPEC/` — protocol command specifications and codegen/audit chain.

## Removed v13 business semantics

The v13 architecture intentionally does not preserve these old interfaces:

- `UnifiedQuoteAPI` / `AsyncUnifiedQuoteAPI`;
- `quote_api()` / facade runtime factories;
- public `route=` / `route="auto"`;
- pseudo-provider `web`;
- `DataSourceRouter` as a business execution kernel;
- generic normal-query `allow_partial=True`;
- ResultMeta `.source` alias;
- legacy HTTP/WS business servers;
- MCP facade/TdxClient dual routing;
- legacy QuoteStream/AsyncQuoteStream business APIs.

Do not add compatibility shims that recreate these semantics.

## Verification and release gate

The clean-break branch remains a Draft integration PR until the **same exact head SHA** receives real GitHub Actions runners, executes blocking steps, and passes them.

The following do **not** count as green evidence:

- `steps=[]` / `steps=null`;
- `runner_id=0`;
- skipped or disabled gates;
- soft-failed gates;
- workflow-level success when blocking jobs never executed.

Source inspection and offline test code can establish source alignment, but they cannot establish release readiness without real executed CI evidence.

## License

See `LICENSE`.
