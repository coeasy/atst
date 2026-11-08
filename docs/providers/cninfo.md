# cninfo Provider 接口与 Channel 契约

> Provider ID: `cninfo`  
> API compatibility selector: `provider="cninfo"`  
> Role: `disclosure`

## 1. 定位

巨潮资讯（法定披露渠道）：沪深公告、港股公告、互动问答。公告是**法定文本**，
字段与排序口径与行情 Provider 不同，不并入 `Quote.extra`。

## 2. Channel

```text
catalog
```

- `announcements`（`CninfoSource.fetch_announcements`）
- `hk_announcements`（`CninfoSource.fetch_hk_announcements`）
- `interactive_qa`（历史兼容绑定，`backend='web_session'` / `source='sina'`；
  `needs_verify`）

`CHANNEL_ADAPTERS`：`catalog → ('atst.sources.cninfo.adapters', 'CninfoSource')`。

## 3. Unified API

```python
md.call("announcements", symbol="sh600519", provider="cninfo")
md.call("hk_announcements", symbol="hk00700", provider="cninfo")
md.call("interactive_qa", symbol="sh600519", provider="cninfo")
```

## 4. Referer / Rate

巨潮公告接口要求分页参数与 `stock` / `category` 过滤；没有专用限流池，
复用进程内 HTTP 客户端的默认超时与重试。翻页未取尽时显式告警，不截断冒充全量。

## 5. Freshness

公告的"新"以披露时间（`announcement_time`）为准，不是抓取时间；盘后披露
（15:00 之后）与盘中披露分开标注。`currentness="live"` 不适用于公告 channel。

## 6. Errors

上游失败保持原 `WebSourceError` 家族（429 → `WebRateLimited`，反爬 →
`AntiSpiderBlocked`，接口下线/空结果 → `SourceDeprecated`）；参数不合法走
`ValidationError`(E1010，HTTP 422 / JSON-RPC -32602 / CLI 退出码 2)。

## 7. Provider-specific Model

`Announcement` / `HkAnnouncement` / `InteractiveQA` 各自独立，正文与 PDF 链接
保留原始 URL，不做正文清洗。

## 8. Conformance

验证分页完整性、披露时间口径、港股/沪深代码归一化、无跨 Provider 静默换源。
