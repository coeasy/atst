# 从 easyquotation 迁移

> **垫片状态**：早期版本提供过 `tstdx.web.easyquotation.use()` 兼容垫片，已在 v1.2.0
> 清理批次中移除（其能力面已并入 `tstdx.web` 多源路由与门面 API）。迁移请直接使用
> 原生 Web 源 / 门面 API，字段口径与 easyquotation 兼容（见下）。

## 迁移目标 API

### 门面（统一响应 + 多源降级，推荐）

```python
from tstdx.facade import quote_api

api = quote_api()
resp = api.query("quotes", ["sh600519", "sz000001"])
if resp:
    df = resp.df  # pandas DataFrame（可选）
    print(resp.data, resp.extra.get("source"))
else:
    print(resp.error, resp.code)
```

### 多源自动降级（WebQuoteClient / DataSourceRouter）

```python
from tstdx.web import WebQuoteClient

client = WebQuoteClient()
quotes = client.quotes(["sh600519"])  # 东财/新浪/腾讯等多源互为备份
```

### 单源直连（等价 easyquotation 的 use("sina")）

```python
from tstdx.web import get_quotes, get_kline, create_source

quotes = get_quotes(["sh600519"], source="sina")  # 指定单源（sina|tencent|eastmoney|jsl|hk|boc）
all_market = create_source("sina").fetch_all(node="hs_a")  # 新浪全市场
kline = get_kline("sh600519", period="day")  # K 线
```

字段名与 easyquotation 保持一致（`name/open/prev_close/price/high/low/...`）。

## easyquotation API → tstdx 对照

| easyquotation | tstdx | 说明 |
|---|---|---|
| `hq.real(codes)` | `WebQuoteClient.quotes(codes)` / `get_quotes(codes)` | 实时行情（含五档），全源 |
| `hq.all(node="hs_a")` | `create_source("sina").fetch_all(node="hs_a")` | 全市场（新浪 `Market_Center.getHQNodeData` 同接口） |
| `hq.get_stock_market(codes)` | `get_quotes(codes)` | 等价 quotes |
| `hq.get_klines(symbol, type)` | `get_kline(symbol, period=...)` | 日/周/月/分时 K 线 |
| `hq.get_index()` | `web_session("sina").index()` | 大盘指数（上证/深成/创业板/沪深 300，`WebQuoteSession.index()`） |

多源自动降级（`WebQuoteClient` / `DataSourceRouter`）与单源直连是**两套独立入口**，按需选用。

## 行为差异

| 差异点 | easyquotation | tstdx |
|---|---|---|
| 成交量单位 | 源原始口径 | **统一为股** |
| 成交额单位 | 源原始口径 | **统一为元** |
| 失败行为 | 抛裸异常 | `TdxError` + `RetryAdvice`（含换源建议）|
| 降级 | 无 | 单源直连无自动备份；多源互为备份用 `WebQuoteClient` / `DataSourceRouter` |
| 限流 | 无 | 每源自带 RateLimiter（超时抛 `WebRateLimited`） |

## 卸载原库

确认测试通过后：

```bash
pip uninstall easyquotation
```
