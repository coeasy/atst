# tstdx

> Provider-first market-data runtime and TDX protocol toolkit.
>
> Current package version: **v1.0.0**. This branch implements the v13 clean-break architecture in `docs/ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md`.

## v13 architecture

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

One `QueryPlan` executes one explicit Provider and one Channel. Cross-Provider fallback exists only through `FallbackPolicy -> ProviderOrchestrator`.

There is no public `route=`, no `route="auto"`, no `source` alias and no pseudo-provider `web`.

## Complete capability migration

The old `UnifiedQuoteAPI` / `AsyncUnifiedQuoteAPI` business surface is retired, but its business abilities have been migrated into the v13 runtime instead of being dropped.

`tstdx.capability_catalog.MIGRATED_BINDINGS` is the migration SSOT. It covers the historical finance/F10/fund/futures/bond/options/board/ranking/search/news/macro/governance/ESG/extended-market/goods/rates and supplemental web abilities, plus explicit derived capabilities such as adjusted bars and local-day synchronization.

Provider identity remains auditable:

- known single-source operations use `tdx`, `sina`, `tencent`, `eastmoney`, `baidu`, `boc`, `iwencai` or `builtin`;
- local vipdoc remains `local_vipdoc`;
- historical helpers that internally select or combine sources use the explicit `derived` Provider instead of publishing false upstream provenance.

The migration gate in `tests/runtime/test_legacy_capability_migration_v13.py` requires the historical business ability contract to remain covered and every Registry row to have an executable DirectBinding.

## Installation

```bash
pip install tstdx
pip install "tstdx[all]"
pip install "tstdx[web,server,mcp]"
pip install -e ".[dev]"
```

## Python API

### Tier-A strongly typed operations

```python
from tstdx import Client

with Client() as client:
    quotes = client.quotes(["sh600519", "sz000001"], provider="tdx")
    bars = client.bars("sh600519", provider="eastmoney", period="day", count=80)
    snapshot = client.snapshot("sh600519", provider="tdx")
    minute = client.minute("sh600519", provider="tdx")
    trades = client.trades("sh600519", provider="tdx", count=100)
```

Every successful operation returns canonical `QueryResult` metadata with Provider, Channel, capability, fingerprint and provenance.

### Migrated business abilities

Use the canonical generic contract:

```python
from tstdx import Client

with Client() as client:
    balance = client.call("balance_sheet", "sh600519")
    fund_rank = client.call("fund_rank", fund_type="all")
    options = client.call("options_snapshot", "510050")
    news = client.call("news_financial", page=1, size=20)
    f10 = client.call("f10", "sh600519", "600519.txt", provider="tdx")
```

Migrated names are also available as same-name Client methods where they do not collide with Tier-A semantics:

```python
with Client() as client:
    balance = client.balance_sheet("sh600519")
    reports = client.research_reports("sh600519")
```

Discover the runtime catalog with `Client.capabilities()`.

### Async

`AsyncClient` uses the same runtime and capability catalog:

```python
import asyncio
from tstdx import AsyncClient

async def main():
    async with AsyncClient() as client:
        result = await client.call("balance_sheet", "sh600519")
        print(result.meta.provider, result.meta.channel)

asyncio.run(main())
```

## Explicit fallback

```python
from tstdx import Client, FallbackPolicy

policy = FallbackPolicy.build("tdx", "tencent", "sina")
with Client() as client:
    result = client.quotes(["sh600519"], policy=policy)
```

Fallback is explicit and auditable. It is never encoded as `route="auto"`.

## Batch and streaming

Partial success belongs to the explicit batch contract:

```python
with Client() as client:
    batch = client.quotes_batch(["sh600519", "sz000001"], provider="tdx")
```

Stateful streaming executes quotes through the same UnifiedRuntime rather than a second raw-client kernel:

```python
with Client() as client:
    stream = client.stream(["sh600519"], provider="tdx", interval=1.0)
    stream.start()
    # ...
    stream.stop()
```

## HTTP / WebSocket / MCP / CLI parity

All migrated business abilities are reachable through the same Client/runtime chain:

- HTTP: `POST /v13/query/{capability}` and `GET /v13/capabilities`;
- WebSocket JSON-RPC: `query` and `runtime.capabilities`;
- MCP: `query_capability` plus dedicated Tier-A tools;
- CLI: `tstdx query <capability> --args '[...]' --kwargs '{...}'` and `tstdx capabilities`.

Tier-A retains dedicated ergonomic endpoints/tools in addition to the generic capability path.

## Provider capability contract

See:

- `docs/PROVIDER_CAPABILITY_MATRIX_v13.md`
- `docs/IMPLEMENTATION_ALIGNMENT_MATRIX_v13.md`
- `docs/V13_REFACTOR_EXECUTION_STATUS.md`
- `docs/ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md`
- `docs/REFACTOR_PLAN_v13_CLEAN_BREAK.md`

Registry declarations without exact DirectBindings are architecture failures.

## Verification status

The branch is source-refactored, but source alignment is not release evidence. PR #6 remains Draft until the exact latest SHA receives real GitHub Actions runners and Ruff, mypy, pytest/coverage, architecture/spec/golden/adversarial/reachability/docs and Native blocking gates actually execute and pass.

`steps=[]`, `steps=null`, `runner_id=0`, skipped jobs or outer workflow success without executed blocking jobs are not considered green evidence.
