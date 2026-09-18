# Cookbook：单一执行内核实战

本文档展示 `Client` + `UnifiedRuntime` 这条唯一执行路径的实战用法：
零缓存查询与审计、批量三态、显式跨源、Typed Query 与 Domain Record、
流式订阅、以及用 `KernelExecutor` 注入假执行体做离线测试。

> 历史背景：本页曾描述 v14 Runtime 的 `create_runtime` / `RuntimeGateway` /
> `QueryRequest` / `semantic_cache` 用法。那套信封运行时与全部缓存层已随
> v16 Phase 2 物理删除，不存在兼容别名。

## 1. 单发查询与溯源审计

每次请求编译成**恰好一个** Provider / channel 的 `QueryPlan`，直调绑定实现，
结果自带 provenance；没有任何缓存会替你回答第二次。

```python
from tstdx import Client, QuerySpec

client = Client()

bars = client.bars("sh600519", period="day", count=80)
print(len(bars.data), bars.meta.provider, bars.meta.channel, bars.meta.capability)

# 通用面：任意 capability 走同一入口（参数在规划期按真实签名校验）
spec = QuerySpec.build("bars", symbols="sh600519", period="day", count=80)
result = client.execute(spec)
print(result.meta.fingerprint)     # 稳定指纹：可直接作为调用方自建缓存的键
print(result.meta.provenance)      # 直连来源；与请求 Provider 不一致时内核直接抛错
```

非法参数（错 capability、缺参、类型不符）在 `execute()` 发请求**之前**就抛
`ValidationError`，不会静默退化成默认值。

## 2. 批量：逐 symbol 三态，绝不部分静默

```python
from tstdx import Client

client = Client()
batch = client.quotes_batch(["sh600519", "sz000001", "sz399999"])

print(batch.status_counts)         # {'ok': .., 'failed': .., 'missing': .., 'not_attempted': ..}
for symbol, item in batch.items.items():
    print(symbol, item.status, item.error if item.error else "")

print(batch.partial, list(batch.errors))   # 有缺口时 errors 必非空
```

`items` 按请求顺序给出 `{symbol: BatchItem}`；`BatchItem.status` 只可能是
`ok / missing / failed / not_attempted`。`partial` 与 `errors` 严格一致（构造期校验），
所以"看起来成功了一半"永远不会被误读成全部成功。

## 3. 跨源：只在显式策略下发生

默认路径**永不**换源。要跨 Provider 容错，必须显式给出有序策略：

```python
from tstdx import Client, FallbackPolicy, QuerySpec

client = Client()
policy = FallbackPolicy(providers=("tdx", "tencent"))

out = client.quotes("sh600519", policy=policy)
# 或通用面： client.execute_with_policy(QuerySpec.build("quotes", symbols="sh600519"), policy=policy)

print(out.result.meta.provider)                  # 实际成功的那个源
print([(a.provider, a.status, a.code) for a in out.attempts])   # 逐源尝试审计痕迹
```

`attempts` 是完整尝试序列：谁失败了、以什么错误码失败，都可核对；被换掉的源不会
伪装成原请求结果。

## 4. Typed Query 与 Domain Record

冻结 dataclass 契约 → 内核 → 强类型记录，避免 `list[dict]` 口径漂移。

```python
from tstdx import Client
from tstdx.typed_query import FundHoldingsQuery, records_from_response

client = Client()
typed = client.typed(FundHoldingsQuery(code="000001"))
print(typed.capability, len(typed.data))

# 也可以先拿 QueryResult 再显式转换
result = client.call("fund_holdings", code="000001")
records = records_from_response(FundHoldingsQuery(code="000001"), result)
```

Domain Record 族共 9 类（`tstdx.domain.records`）：`FinancialRecord`、
`FundRecord`、`BondRecord`、`NewsRecord`、`ResearchRecord`、`OptionRecord`、
`MarketDataRecord`、`SearchRecord`、`MacroRecord`。

## 5. 流式订阅：显式生命周期

```python
from tstdx import Client

client = Client()
stream = client.stream(
    ["sh600519", "sz000001"],
    provider="tdx",
    interval=1.0,
    diff_only=True,
    max_queue=1024,
    on_quote=lambda q: print(q["symbol"], q["price"]),
    on_error=lambda e: print("stream error:", e),
)
stream.start()
print(stream.state)          # StreamState.RUNNING
# ... 采样若干帧 ...
stream.stop()
print(stream.state)          # CLOSED（半死/重启失败会落 FAILED，绝不静默起第二个 worker）
stream.close()
```

`StreamState` 五态：`CREATED / RUNNING / STOPPING / CLOSED / FAILED`。
详见 [04_streaming.md](04_streaming.md)。

## 6. 离线测试：注入假执行体

`KernelExecutor` 是执行面唯一的 Protocol 接缝——不碰网络即可端到端测试
`Client` 之上的全部契约。

```python
from tstdx import Client, QuerySpec
from tstdx.query import QueryPlan
from tstdx.result import QueryResult, Provenance
from tstdx.runtime.kernel import UnifiedRuntime


class FakeExecutor:
    def execute(self, plan: QueryPlan) -> QueryResult:
        return QueryResult.from_plan(
            [{"symbol": "sh600519", "price": 10.0}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )


client = Client(runtime=UnifiedRuntime(executor=FakeExecutor()))
result = client.execute(QuerySpec.build("quotes", symbols="sh600519"))
assert result.meta.provider == "tdx"
assert result.data[0]["price"] == 10.0
```

## 7. 启动对账

三方事实（Provider registry / capability catalog / `DIRECT_BINDINGS`）在启动时对账，
漂移即报错，而不是等到线上第一次调用才发现某个 capability 没接线。

```python
from tstdx.runtime.audit import audit_runtime

report = audit_runtime()
print(report)
```
