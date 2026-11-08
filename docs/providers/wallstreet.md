# wallstreet Provider 接口与 Channel 契约

> Provider ID: `wallstreet`  
> API compatibility selector: `provider="wallstreet"`  
> Role: `auxiliary_news`

## 1. 定位

华尔街见闻（全球快讯流）：只提供 `breaking_news` 一条 channel 的快讯序列，
不提供行情或个股基本面。快讯是**事件流**，没有交易日概念。

## 2. Channel

```text
catalog
```

- `breaking_news`（`WallstreetSource.fetch_breaking_news`）

`CHANNEL_ADAPTERS`：`catalog → ('atst.sources.wallstreet.adapters', 'WallstreetSource')`。

## 3. Unified API

```python
md.call("breaking_news", provider="wallstreet", limit=100)
```

## 4. Referer / Rate

快讯接口通常带游标/时间戳；复用进程内 HTTP 客户端的默认超时，翻页游标缺失时
显式告警而不是重复返回同一页。

## 5. Freshness

以快讯发布时间为准（带时区），不是抓取时间。历史快讯不做回溯抓取；
`currentness="live"` 表示尽量取最新一页。

## 6. Errors

上游失败保持 `WebSourceError` 家族原异常；空快讯是合法结果（非静默故障）。

## 7. Provider-specific Model

`BreakingNewsRow`（时间、标题、摘要、来源 URL），不并入 `Quote.extra`。

## 8. Conformance

验证时间时区、游标去重、空流语义、无跨 Provider 静默换源。
