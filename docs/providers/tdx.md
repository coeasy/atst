# TDX Provider 接口与 Channel 契约

> Provider ID: `tdx`  
> API compatibility selector: `source="tdx"`  
> Role: `primary_live`  
> Status: v12 target contract; protocol facts follow Command Ledger / golden evidence.

## 1. 定位

TDX 是 tstdx 默认主 Provider。用户未显式指定 Provider 且 capability 支持 TDX 时，`default_provider=tdx`。

TDX 内部允许在同一 Channel 的主站池/等价 endpoint 中容错，但不得因为 TDX 失败跨 Provider 到腾讯、新浪、东财。

## 2. Channel

### quotation

协议族：7709。

Capabilities：

```text
quotes
bars
minute（0x0537 的 request/parser 仍为 inferred，结构化 API 在发包前抛 NotImplementedFeature；分时改走 Web Provider）
trades（0x0FC5 同上：tdx 面的当日逐笔没有可用结构化入口，逐笔改走 Web Provider）
security_count
security_list（0x044D 多主站实测无响应，已下线：发包前抛 CommandOffline）
finance（字段口径未闭合，见 REFACTOR_PLAN_V17_CLOSURE F-37：结构与条数可用，逐字段语义不保证）
capital_changes（同 finance，F-37）
snapshot
```

括号里的批注不是可选项，由 `tests/architecture/test_offline_capability_honesty.py` 按命令账本、
`core._UNVERIFIED_STRUCTURED_BLOCK` 与内核直绑表（`runtime/executor.py`）现推：判据是"这条链
在客户端就发不出去"，所以**通的能力也不许被写成受限能力**。要改这一栏，先改账本状态或拦截集。

Direct API：

```python
md.tdx.quotation.quotes(symbols)
md.tdx.quotation.bars(symbol, period="day")
md.tdx.quotation.minute(symbol)
md.tdx.quotation.trades(symbol)
md.tdx.quotation.finance(symbol)
md.tdx.quotation.capital_changes(symbol)
```

### extended

协议族：7727。

```python
md.tdx.extended.markets()
md.tdx.extended.instruments(...)
md.tdx.extended.quote(...)
md.tdx.extended.bars(...)
```

### goods

协议族：GOODS。

```python
md.tdx.goods.quote(...)
md.tdx.goods.quotes(...)
md.tdx.goods.bars(...)
```

### f10

协议族：F10。

```python
md.tdx.f10.catalog(symbol)
md.tdx.f10.download(symbol, filename)
```

### mac

协议族：MAC。

```python
md.tdx.mac.quote(...)
```

### vipdoc

本地历史数据 Channel，不是独立 Provider：

```python
md.tdx.vipdoc.day(symbol, ...)
md.tdx.vipdoc.minute(symbol, period="1m|5m", ...)
```

`vipdoc` 只服务本地历史数据，不替代 TDX 实时行情。

## 3. 内部容错术语

允许：

```text
host failover
endpoint failover
registry-driven channel resolution
```

不再称为“TDX 内部换 source”。

合法：

```text
tdx/quotation host A -> host B -> host C
```

禁止：

```text
tdx/bars -> sina/bars
tdx/bars -> tencent/bars
```

## 4. Unified API

推荐：

```python
md.quotes(symbols, provider="tdx")
md.bars(symbol, provider="tdx")
md.minute(symbol, provider="tdx")
md.trades(symbol, provider="tdx")
```

兼容期：

```python
md.quotes(symbols, source="tdx")
```

两者内部都归一成 `ProviderId("tdx")`。

## 5. Freshness

实时数据必须验证：

```text
trading day
source timestamp if available
sequence/response evidence
observed_at
received_at
age
```

运行期对 `currentness` 的判据只有一处，落在 `tstdx/runtime/freshness.py`：
执行 channel 读本地文件（`ChannelSpec.local`）却被要求当期口径（`live`/`business`）时，
运行期没有证据证明文件已覆盖当期——`strict=True` 当场返回 `FreshnessViolation`，
非严格模式把它作为 `currentness_unproven` 瑕疵随结果出发（不静默）。

上面 freshness profile 清单里的 `age`/`received_at` 在 v17 运行期**没有对应字段、没有生产者、也没有读取者**（全仓 `received_at`/`age` 命中 0 处），`observed_at` 只有 `Provenance.observed_at_ns` 一个事实；因此除上述本地文件面之外，本仓不声称任何运行期年龄校验（剩余面登记为 F-68，等删除或占位裁决）。

`live` 打在非 live channel 上仍是规划期的输入错误（`ValidationError`/422），不改写成 503：请求本身不可满足，与结果是否新鲜无关。
不得用缓存/Replay/Web 伪装 TDX 实时成功。

## 6. ResultMeta

```text
provider=tdx
channel=quotation|extended|goods|f10|mac|vipdoc
capability=...
market=...
freshness=...
real=true
```

兼容 JSON 可临时输出 `source=tdx`；内部只存 provider。

## 7. Error

保留现有错误树：

```text
AllHostsUnreachable
CommandOffline
FreshnessViolation
IntegrityViolation
ConnectionFailed / ReadTimeout / WebSourceError
```

`FreshnessViolation`(E4060) 是唯一由 `currentness` 契约触发的数据新鲜度错误，抛点见 §5。Provider 真实不可用在本仓表现为上面那组传输层原异常（`TdxError` 原样保留并补齐 Provider/Channel context）。

能力/period/channel 与 tdx 不匹配（含把 `currentness='live'` 打在非 live channel 上）在规划期就是 `ValidationError`(E1010)；协议侧字段自洽性由 `IntegrityViolation`(E3042) 表达，严格口径下分页漂移或累计字节未达服务端声明数是 `TruncatedDataError`(E4050)。本仓没有"Provider 不支持某能力"或"数据完整性"的专用异常类，也不做统一"不可用"包装（退役记录见 `docs/errors.md` §一之二）。

## 8. 协议事实保护

online/offline/degraded 只由 Command Ledger + 真机/golden 证据更新。不得为了接口完整把 offline 命令改 online，也不得拿 Web Provider 数据模拟 TDX 成功。

## 9. 性能

优先顺序：

1. persistent TDX connection pools；
2. per-Channel host ranking/health；
3. batch/chunk；
4. SingleFlight；
5. total deadline budget；
6. normalize once；
7. benchmark 后才做 parser 微优化。

## 10. Conformance

必须验证：

```python
assert result.meta.provider == "tdx"
assert {a.provider for a in result.meta.attempts} == {"tdx"}
```

并验证所有 TDX Channel 生命周期隔离、同 Channel endpoint failover、无跨 Provider attempt。
