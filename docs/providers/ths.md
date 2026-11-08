# ths Provider 接口与 Channel 契约

> Provider ID: `ths`  
> API compatibility selector: `provider="ths"`  
> Role: `auxiliary_theme`

## 1. 定位

同花顺（主题/概念/热度类数据）：涨停池、概念成分、人气榜、题材归因。
它与 Eastmoney 的同名能力字段口径不同，混用前应检查 provenance。

## 2. Channel

```text
catalog
```

- `concept_members`（`ThsSource.fetch_concept_members`）
- `hot_rank`（`ThsSource.fetch_hot_rank`）
- `limit_pool`（`ThsSource.fetch_limit_pool`）
- `theme_attribution`（`ThsSource.fetch_theme_attribution`）

`CHANNEL_ADAPTERS`：`catalog → ('atst.sources.ths.adapters', 'ThsSource')`。

## 3. Unified API

```python
md.call("hot_rank", provider="ths", top=50)
md.call("limit_pool", provider="ths", date="2026-11-05")
md.call("concept_members", provider="ths", concept="人工智能")
md.call("theme_attribution", provider="ths", symbol="sh600519")
```

## 4. Referer / Rate

同花顺页面接口对 `Referer` 与频率敏感。该事实属于 Provider/Channel metadata，
由 Provider runtime 统一装配；高频抓取会触发反爬（`AntiSpiderBlocked`），
不以换源规避。

## 5. Freshness

人气榜按分钟级刷新；涨停池按交易日；概念成分按日/周。`currentness="live"`
只适用于 `hot_rank`，其余 channel 在规划期拒绝。

## 6. Errors

上游失败保持 `WebSourceError` 家族原异常；空榜（非交易日/无涨停）是合法结果
（空表 + `currentness` 标记），不是 `SourceDeprecated`。

## 7. Provider-specific Model

`HotRankRow` / `LimitPoolRow` / `ConceptMember` / `ThemeAttribution` 独立建模。

## 8. Conformance

验证 Referer、代码归一化、榜单排序稳定、空榜不冒充故障、无跨 Provider 静默换源。
