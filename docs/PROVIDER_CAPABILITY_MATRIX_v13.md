# tstdx v13 Provider Capability Matrix

> Status: executable support contract.
>
> Rule: a capability appears here only when `ProviderRegistry == DirectBinding == Query contract` for the production runtime. Historical helpers outside this matrix are not supported v13 business capabilities.

## Canonical Providers

| Provider | Channel | Capability | Currentness | Notes |
|---|---|---|---|---|
| `tdx` | `quotation` | `quotes` | live | TDX host failover remains provider-internal |
| `tdx` | `quotation` | `bars` | historical | raw bars; silent adjustment is forbidden |
| `tdx` | `quotation` | `snapshot` | live | canonical snapshot/orderbook path |
| `tdx` | `quotation` | `minute` | live | intraday minute data |
| `tdx` | `quotation` | `trades` | live | intraday trades/ticks |
| `tdx` | `quotation` | `security_count` | business | market catalog metadata |
| `tdx` | `quotation` | `security_list` | business | market catalog page |
| `local_vipdoc` | `vipdoc` | `bars` | historical | day / 1min / 5min only |
| `tencent` | `quote` | `quotes` | live | explicit Provider, never `web` pseudo-provider |
| `tencent` | `kline` | `bars` | historical | day / week / month |
| `tencent` | `minute_kline` | `bars` | historical | minute periods |
| `sina` | `quote` | `quotes` | live | explicit Provider |
| `sina` | `history_kline` | `bars` | historical | exact history binding |
| `eastmoney` | `quote` | `quotes` | live | explicit Provider |
| `eastmoney` | `kline` | `bars` | historical | exact history binding |
| `baidu` | `quote` | `quotes` | live | explicit Provider |
| `baidu` | `kline` | `bars` | historical | adjustment unsupported and fails closed |

## Provider identity rules

- `web` is not a Provider.
- `local_vipdoc` is not a TDX channel; it is its own Provider.
- aliases may normalize only when they resolve to exactly one Provider.
- cross-Provider fallback is never encoded in this matrix; it is expressed only by `FallbackPolicy` and executed only by `ProviderOrchestrator`.
- a Provider/channel/capability row without an exact Direct binding is a CI failure.
- a Direct binding not declared here/Registry is a CI failure.

## Tier-A core completion

The v13 core is defined by the following Tier-A capabilities:

- `quotes`
- `bars`
- `snapshot`
- `minute`
- `trades`
- `security_count`
- `security_list`

All seven are compiled by `QueryPlanner`, executed by `UnifiedRuntime`, represented by `QueryResult/Provenance`, exposed by `Client/AsyncClient`, and adapted by HTTP/WS/MCP/CLI where applicable.

## Capabilities intentionally not promoted yet

Historical modules may still contain implementation code for finance/fundamentals, corporate actions, F10, blocks, rankings, funds, bonds, futures, options, news/events and other supplemental datasets. They are intentionally **not** present in the canonical Registry until each is rebuilt with:

1. a typed `QuerySpec` capability contract;
2. canonical Provider/Channel declaration;
3. exact Direct binding;
4. normalized output model;
5. result/provenance semantics;
6. error contract;
7. surface adapters;
8. deterministic contract tests.

This prevents documentation or legacy helper availability from being mistaken for runtime support.
