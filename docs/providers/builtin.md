# builtin Provider 接口与 Channel 契约

> Provider ID: `builtin`  
> API compatibility selector: `provider="builtin"`  
> Role: `composite_static`

## 1. 定位

不持有传输的**内置聚合/常量** Provider：`dc_reports` 的报表目录、`index_list` 的
指数清单、`trade_calendar` 的离线交易日历摘要。它只保证统一 `QuerySpec` 路径可达，
不承诺远端实时性；需要在线校准请用 `trading_calendar`（Eastmoney）。

## 2. Channel

```text
catalog
```

- `dc_reports`、`index_list`、`trade_calendar`

`CHANNEL_API_EXEMPT = True`：没有自有 adapter，绑定按历史兼容口径标记
`backend='web_session'` / `source='sina'`，实际消费的是进程内常量与
`atst.domain.calendar`。

## 3. Unified API

```python
md.call("trade_calendar", year=2026, provider="builtin")
md.call("index_list", provider="builtin")
md.call("dc_reports", provider="builtin")
```

## 4. Rate / Transport

无网络请求，无限流；不占用连接池槽位。

## 5. Freshness

- `trade_calendar`：来自 `TradingCalendar` 内置表，2026 年为**估算值**，
  结果带 `estimated` 标记；需要权威数据用 `trading_calendar`（在线校准）或手动
  `set_holidays()`。
- `index_list` / `dc_reports`：随代码版本更新，版本号即新鲜度。

## 6. Errors

参数校验失败 → `ValidationError`（E1010）。不产生 `WebSourceError` 家族异常，
因为它不联网。

## 7. Provider-specific Model

返回普通 dict / list（报表目录、指数行、日历摘要），不新增领域模型。

## 8. Conformance

验证不触网、不占槽位、`estimated` 标记随年份变化、与 `trading_calendar` 的
`freshness` 口径一致（`manual > online > estimated > builtin` 的优先级由
`TradingCalendar.freshness()` 单点定义）。
