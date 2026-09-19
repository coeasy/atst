# iWencai Provider 接口与 Channel 契约

> Provider ID: `iwencai`  
> API compatibility selector: `source="iwencai"`  
> Role: `auxiliary_info`

i问财用于自然语言选股/条件筛选，是独立信息 Provider，不属于股票实时行情替代链。

Channel：`screening`。

Direct API：

```python
md.iwencai.screen("连续三年ROE大于15%")
md.iwencai.query("今日涨停且主力净流入")
```

Cookie/token 等认证信息必须由 Provider runtime 管理，不能散落在业务参数和日志中。

返回 screening-specific schema，至少保留查询条件、字段名、证券标识、数据日期、Provider 更新时间和原始字段映射。

Freshness 按筛选结果的数据日期/更新时间验证；不得把历史筛选结果伪装为当前实时状态。

错误沿用 `docs/providers/README.md` §12 那一棵树：`screening` 是非 live channel，`currentness='live'` 在规划期就被 `ValidationError`(E1010) 拒绝；认证缺失/被拒、限流与反爬都由 `WebSourceError`(E7xxx) 家族表达（本仓没有单独的认证错误类），context 带 `provider=iwencai`/`channel`/`capability`。

失败不得切其它 Provider。
