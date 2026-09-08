# tstdx

> TDX-first、Provider-aware 的中国市场数据协议与统一访问库。
>
> **当前版本：1.4.0**。当前重构执行基线见
> [docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md](docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)。

## 当前架构契约

`tstdx` 将“谁提供数据”统一建模为 **Provider**：

- `tdx`：默认、主要实时 Provider；
- `tencent` / `sina` / `eastmoney` / `baidu`：独立 Web Provider；
- `jsl` / `boc` / `iwencai`：独立特色信息 Provider。

`source=` 仅作为兼容参数映射到同一个 ProviderId；内部不维护第二套 Source
对象、SourceRegistry 或 SourceManager。

### 不自动跨 Provider 降级

选择某个 Provider 后，查询只允许在该 Provider 内执行：

```text
provider=tdx
    ↓
tdx/quotation
    ↓
host A → host B → host C   # 允许：同 Provider、同语义 host failover
```

禁止：

```text
tdx 失败 → sina
tdx 失败 → tencent
tdx 失败 → eastmoney
```

如果所选 Provider 不可用、数据过期或无法验证，返回明确错误，不使用另一站点的数据
伪装成原 Provider 的结果。

## 主要能力

- **TDX 五个协议/数据族**：quotation / extended / goods / F10 / MAC；
- **本地 TDX 历史数据**：`tdx/vipdoc`，仅用于 local historical，不替代实时行情；
- **统一行情服务**：`UnifiedMarketDataService`；
- **Direct Provider API**：保留每家 Provider 的特色数据和原生口径；
- **同步 / 异步 API**：同步与异步共享同一 QueryPlan 与 Provider 生命周期；
- **QueryPlan**：Provider、Channel、Capability、窗口和 schema 一次编译；
- **Provider 生命周期**：TDX 协议池、Web HTTP keep-alive、Provider Health；
- **SingleFlight / BatchPlanner**：同语义请求合并、Provider batch limit；
- **Semantic Cache V2**：Provider-scoped、QueryFingerprint-scoped、显式 opt-in；
- **Freshness / Provenance**：实时、历史、cache/replay/synthetic 可审计；
- **Streaming**：Provider-bound 调度、watermark、gap/reconnect/resubscribe；
- **HTTP REST / WebSocket / MCP / CLI**：统一 Provider/错误/来源语义；
- **错误树 E1–E9**：公共 `SourceUnavailable(E7050)` 保持兼容。

## 安装

```bash
pip install tstdx
pip install "tstdx[all]"
pip install -e ".[dev]"
```

常用 optional extras：

| Extra | 用途 |
|---|---|
| `config` | Pydantic 严格配置 |
| `web` | HTTP Web Provider |
| `dataframe` | pandas DataFrame |
| `parquet` | pyarrow |
| `duckdb` | DuckDB |
| `metrics` | Prometheus metrics |
| `server` | FastAPI / uvicorn / websockets |
| `mcp` | MCP stdio server |
| `dev` | pytest / ruff / mypy / build tools |

## 推荐：统一 Provider API

```python
from tstdx import UnifiedMarketDataService

with UnifiedMarketDataService() as md:
    # 未显式指定 Provider 时，TDX 是默认 Provider。
    quotes = md.quotes(["sh600519", "sz000001"])

    # 推荐显式 provider=。
    tdx_quotes = md.quotes(["sh600519"], provider="tdx")
    qq_quotes = md.quotes(["sh600519"], provider="tencent")

    bars = md.bars("sh600519", period="day", count=80, provider="tdx")
```

兼容写法：

```python
quotes = md.quotes(["sh600519"], source="tdx")
```

如果同时提供：

```python
provider="tdx"
source="sina"
```

则直接 `ValidationError`，不会猜测调用方意图。

## Direct Provider API

统一 API 只覆盖真正具有共同语义的数据。Provider-specific 数据使用 Direct API。

```python
with UnifiedMarketDataService() as md:
    # TDX 协议族
    md.tdx.quotation.quotes(["sh600519"])
    md.tdx.extended.bars("00700", period="day")
    md.tdx.goods.bars("IFL8", period="day")
    md.tdx.f10.catalog("sh600519")

    # 腾讯
    md.tencent.minute("sh600519")
    md.tencent.ticks("sh600519")
    md.tencent.global_quotes()

    # 新浪
    md.sina.news("sh600519")
    md.sina.fund_flow("sh600519")

    # 东财
    md.eastmoney.fund_flow(["sh600519"])
    md.eastmoney.limit_pool()
    md.eastmoney.hot_rank()

    # 百度
    md.baidu.minute("sh600519")
    md.baidu.ticks("sh600519")

    # 已验证的特色 Provider 能力
    md.jsl.bonds()
    md.boc.fx_rates()
    md.iwencai.screen("连续三年ROE大于15%")
```

Registry 与实际 adapter 尚未验证的能力不会写进正式 Direct API；例如不能让一个可转债
endpoint 以另一个资源类型名称返回。

## 批量查询与部分结果

普通 `quotes()` 是严格完整结果：Provider 返回缺少标的时 fail-closed。

```python
quotes = md.quotes(
    ["sh600519", "sz000001"],
    provider="tdx",
)
```

需要部分成功语义时使用：

```python
result = md.quotes_batch(
    ["sh600519", "sz000001"],
    provider="tdx",
)

print(result.items)
print(result.errors)
print(result.partial)
```

`BatchResult` 会区分：

- `failed`：该 chunk 已真实请求且失败；
- `missing`：Provider 请求成功，但该 symbol 缺失；
- `not_attempted`：前序 chunk 失败后，该 symbol 没有继续请求。

部分结果不会写入 semantic cache。

## 最新真实数据与缓存

实时查询默认是 direct Provider：

```python
md.quotes(["sh600519"], provider="tdx")
```

默认行为：

```text
max_age=None / 0
    ↓
不读 cache
不写 cache
    ↓
direct Provider request
```

只有显式允许 bounded cache 时：

```python
md.quotes(
    ["sh600519"],
    provider="tencent",
    max_age=1.0,
)
```

才允许复用相同 QueryFingerprint 的结果。

Query identity 至少包含：

```text
Provider
Channel
Capability
Symbols
Period
Start / Count
Adjustment
Schema version
```

cache hit 仍保留真实 Provider provenance，并重新执行 freshness validation；cache、replay、
synthetic 和 local historical 都不能冒充 direct live result。

K 线 freshness 区分 `current_series` 与 `historical_closed`：历史闭合窗口可以是有效、完整、
可审计的 Provider 数据，但不会因此被标记为“当前实时”。

## Streaming

新应用使用 Provider-bound planned stream：

```python
from tstdx import PlannedQuoteStream

stream = PlannedQuoteStream(provider="tdx")
stream.subscribe("sh600519", interval=1.0)
stream.start()
```

每个 stream 固定一个 Provider：

```text
TDX stream 出错
    ↓
允许：TDX reconnect / host failover / resubscribe
禁止：自动切腾讯/新浪/东财
```

planned streaming 维护 per-symbol watermark、gap 和 reconnect epoch；恢复只有在对应 symbol
真正重新收到 Provider 数据后才关闭 gap。

## 异步 API

```python
import asyncio

from tstdx import AsyncMarketDataService


async def main():
    async with AsyncMarketDataService(max_workers=4) as md:
        quotes = await md.quotes(["sh600519"], provider="tdx")
        bars = await md.bars("sh600519", provider="tdx", count=80)
        print(quotes, bars[-1])


asyncio.run(main())
```

异步层不会维护第二套路由；实际工作仍进入同一个 PlannedService。

## HTTP 服务

安装：

```bash
pip install "tstdx[server]"
```

官方工厂：

```python
from tstdx.integration import create_app

app = create_app()
```

Provider-aware endpoints 包括：

```text
GET /providers
GET /providers/{provider}
GET /providers/{provider}/quotes
GET /providers/{provider}/quotes/batch
GET /providers/{provider}/bars/{symbol}
```

URL 中的 Provider 是执行边界；请求失败不会换站。

## CLI

```bash
tstdx bars sh600519 --period day --count 80
tstdx quotes sh600519 sz000001
tstdx server-test
tstdx hosts audit --family quotation
tstdx probe 0x052D
```

TDX 协议/主站巡检仍是 TDX Provider 内部治理，不代表跨 Provider fallback。

## 直接使用底层 TDX 客户端

需要协议级接口时仍可使用：

```python
from tstdx import TdxClient

with TdxClient() as client:
    bars = client.bars("sh600519", period="day", count=80)
    quotes = client.quotes(["sh600519", "sz000001"])
```

底层 TDX client 是协议 API；新应用的统一 Provider 选择、QueryPlan、semantic cache、统一
freshness/error contract 推荐通过 `UnifiedMarketDataService` 使用。

## Provider 文档

| Provider | 文档 |
|---|---|
| TDX | [docs/providers/tdx.md](docs/providers/tdx.md) |
| Tencent | [docs/providers/tencent.md](docs/providers/tencent.md) |
| Sina | [docs/providers/sina.md](docs/providers/sina.md) |
| Eastmoney | [docs/providers/eastmoney.md](docs/providers/eastmoney.md) |
| Baidu | [docs/providers/baidu.md](docs/providers/baidu.md) |
| JSL | [docs/providers/jsl.md](docs/providers/jsl.md) |
| BOC | [docs/providers/boc.md](docs/providers/boc.md) |
| iWencai | [docs/providers/iwencai.md](docs/providers/iwencai.md) |

架构 SSOT：

- [docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md](docs/TDX_PROVIDER_CHANNEL_ARCHITECTURE_PLAN_v12.md)
- [docs/adr/ADR-013-provider-source-terminology.md](docs/adr/ADR-013-provider-source-terminology.md)
- [docs/providers/README.md](docs/providers/README.md)

## 错误语义

`SourceUnavailable` 是稳定公共错误，代码 `E7050`。在 v12 中它表示：

> 已选择的 Provider 无法满足当前查询。

错误上下文使用：

```json
{
  "provider": "tdx",
  "channel": "quotation",
  "capability": "bars",
  "fallback": false
}
```

`fallback=false` 是核心契约，不是调试建议。

## 开发与验证

```bash
pip install -e ".[dev]"
ruff format --check tstdx tests
ruff check tstdx tests
mypy tstdx
pytest
```

项目 CI 还包括：

- Python / OS 测试矩阵；
- protocol spec / golden / parser accuracy；
- Provider/Channel 架构门禁；
- docs link integrity；
- module reachability / originality；
- native bridge/parity 等门禁。

任何 Provider/runtime 重构都不应通过降低阈值、跳过测试或静默 fallback 来获得绿色。

## 协议事实与 Golden 数据

协议命令账本位于 `PROTOCOL_SPEC/`，Golden 数据位于 `tests/golden/`。协议行为必须以真实抓包、
已验证命令和样本为依据；某个已知 offline/degraded 命令不能为了 API 完整被伪装成可用。

## License

MIT — see [LICENSE](LICENSE).
