# derived Provider 接口与 Channel 契约

> Provider ID: `derived`  
> API compatibility selector: `provider="derived"`  
> Role: `composite`

## 1. 定位

**显式聚合 Provider**：能力名里的 `provider` 指"取原始数据的那条腿"，`derived`
只负责编排、复权、同步与富集，不持有自己的传输。它与 `builtin` 的区别是：`derived`
会真实调用其他 Provider/内核组件，`builtin` 只返回进程内常量。

## 2. Channel

```text
catalog
adjustment
sync
enriched
```

- `catalog`：跨源统一名字（`all_market`、`history`、`hk_quotes`、基金/债券/
  公告/研报等，见 `atst/providers/derived.py` 的完整清单）；
- `adjustment`：`adjusted_bars`（复权 K 线，`backend='composed'`）；
- `sync`：`sync_daily`（日线落盘同步）；
- `enriched`：`daily_enriched`（日线 + 复权 + 停复牌等富集结果）。

`CHANNEL_API_EXEMPT = True`：没有自有 adapter，绑定分别落到 `web_session` 或
`composed` 执行器。

## 3. Unified API

```python
md.bars("sh600519", provider="derived", adjust="qfq")
md.call("all_market", provider="derived", node="hs_a")
md.call("sync_daily", provider="derived", ...)
md.call("daily_enriched", provider="derived", ...)
```

## 4. Rate / Transport

复用被编排的那条腿的限流与超时；`adjusted_bars` 的分页常量
（`_ADJUST_EVENT_MAX_PAGES` 等）集中在 `atst/runtime/composed.py`。

## 5. Freshness

由被编排的源决定；富集结果另带 `daily_enriched` 的 `as_of` 与缺项告警
（停复牌/复权事件缺失不静默补零）。

## 6. Errors

- 子调用失败按原 `WebSourceError` 家族向上抛，context 保留 `provider` / `channel`；
- 参数组合不合法（如 `adjust` 与周期不兼容）→ `ValidationError`(E1010)；
- 分页触顶 → `web_eastmoney_page_limit` 告警（见 `docs/errors.md`）。

## 7. Provider-specific Model

复权/同步/富集各有结果类型（`AdjustedBars` / `SyncReport` / `DailyEnriched`），
不塞进 `Quote.extra`。

## 8. Conformance

验证复权因子方向、粗粒度周期（`_COARSE_DAYLIKE_PERIODS`）、分页上限、
`derived` 不改变被编排源的身份（provenance 仍指向真实上游）。
