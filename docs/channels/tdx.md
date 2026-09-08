# TDX Source 接口与 Channel 契约

> Source ID: `tdx`  
> Role: `primary_live`  
> Status: v12 target contract; protocol facts continue to follow Command Ledger / golden evidence.

## 1. 定位

TDX 是 tstdx 的默认主行情源。TDX 内部允许在**同一 Channel 的主站池**中切换 endpoint，但不得因为 TDX 失败而跨 Provider 到腾讯、新浪、东财。

## 2. Channel

### `quotation`

协议族：7709。

目标能力：

```text
quotes
bars
minute
trades
security_count
security_list（仅在命令事实为 online 时）
finance
capital_changes
snapshot（仅在命令事实为 online 时）
```

Direct API：

```python
md.tdx.quotation.quotes(symbols)
md.tdx.quotation.bars(symbol, period="day")
md.tdx.quotation.minute(symbol)
md.tdx.quotation.trades(symbol)
md.tdx.quotation.finance(symbol)
md.tdx.quotation.capital_changes(symbol)
```

Endpoint failover：允许在 quotation host pool 内切换。

### `extended`

协议族：7727。

目标能力：扩展市场、扩展标的目录、扩展市场行情和 K 线等。

```python
md.tdx.extended.markets()
md.tdx.extended.instruments(...)
md.tdx.extended.quote(...)
md.tdx.extended.bars(...)
```

### `goods`

协议族：GOODS。

目标：商品/期货等 TDX Goods 数据族。

```python
md.tdx.goods.quote(...)
md.tdx.goods.quotes(...)
md.tdx.goods.bars(...)
```

### `f10`

协议族：F10。

```python
md.tdx.f10.catalog(symbol)
md.tdx.f10.download(symbol, filename)
```

F10 是资料 Channel，不应被建模成 quotation fallback。

### `mac`

协议族：MAC。

```python
md.tdx.mac.quote(...)
```

具体 capability 只以已验证协议事实为准。

## 3. TDX 内部容错

允许：

```text
quotation host A -> host B -> host C
```

禁止：

```text
tdx quotation -> tencent kline
```

不同 TDX Channel 默认也不互换，除非 ChannelRegistry 明确声明某 capability 语义等价。

## 4. Freshness

实时数据必须验证：

```text
source timestamp / trading day / sequence / observed_at
```

返回 stale 或无法证明实时性的响应时，严格模式返回 `FreshnessViolation`。

## 5. ResultMeta

```text
source=tdx
channel=quotation|extended|goods|f10|mac
market=...
capability=...
freshness=...
real=true
```

具体 host 仅进入内部 diagnostics。

## 6. Error

- `AllHostsUnreachable`：同 Channel host pool 全失败；
- `SourceUnavailable(source=tdx)`：TDX source 当前无法满足请求；
- `ChannelUnavailable`：指定 TDX Channel 不可用；
- `CommandOffline`：Command Ledger 已知 offline；
- `CapabilityUnsupported`：该 TDX Channel 无此能力；
- `FreshnessViolation`：数据不满足最新性要求；
- `DataIntegrityError`：解析/校验失败。

错误后不得跨 Web Provider。

## 7. 当前协议事实保护

已验证的 online/offline/degraded 状态必须继续以 Command Ledger 和真机/golden 证据为准。架构重构不得为了“接口完整”把 offline 命令标成 online，也不得用 Web 数据伪装 TDX 命令成功。

## 8. 性能

优先：

1. persistent connection pool；
2. host ranking/health；
3. batch/chunk；
4. SingleFlight；
5. deadline budget；
6. canonical normalization once。

## 9. Unified API

```python
md.quotes(symbols, source="tdx")
md.bars(symbol, source="tdx")
md.minute(symbol, source="tdx")
md.trades(symbol, source="tdx")
```

这些调用可以在 TDX 内部做 host failover，但 `ResultMeta.source` 始终必须是 `tdx`。

## 10. Conformance

必须断言：

```python
assert result.meta.source == "tdx"
assert {a.source for a in result.meta.attempts} == {"tdx"}
```

并验证 quotation/extended/goods/F10/MAC 生命周期相互隔离。
