# 从 easyquotation 迁移

> **垫片状态**：早期版本提供过 `tstdx.web.easyquotation.use()` 兼容垫片，已在 v1.2.0
> 清理批次中移除；v1.2 的门面 API（`tstdx.facade.*`）亦已随 v16 Phase 2 物理删除。
> 迁移请直接使用 `Client`（唯一业务入口）或原生 Web 源，字段口径与 easyquotation
> 兼容（见下）。

## 迁移目标 API

### 统一内核（推荐）

```python
from tstdx import Client

client = Client()
result = client.quotes(["sh600519", "sz000001"], provider="tencent")
print(result.data, result.meta.provider)      # meta 即溯源审计
df = to_dataframe(result.data)                # 可选：from tstdx.output import to_dataframe

# 需要按策略换源时显式声明（默认永不换源）
from tstdx import FallbackPolicy

out = client.quotes(["sh600519"], policy=FallbackPolicy(providers=("tencent", "sina")))
print(out.result.meta.provider, [(a.provider, a.status) for a in out.attempts])
```

### Web 多源会话（`WebQuoteClient`）

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

Web 多源会话（`WebQuoteClient`）内部互为备份；内核路径（`Client`）单源绑定、失败即抛，跨源需显式 `FallbackPolicy`。两者是独立入口，按需选用。

## 行为差异

| 差异点 | easyquotation | tstdx |
|---|---|---|
| 成交量单位 | 源原始口径 | **统一为股** |
| 成交额单位 | 源原始口径 | **统一为元** |
| 失败行为 | 抛裸异常 | `TdxError` + `RetryAdvice`（含换源建议）|
| 降级 | 无 | 单源直连无自动备份；多源互备用 `WebQuoteClient`，内核跨源用显式 `FallbackPolicy` |
| 限流 | 无 | 每源自带 RateLimiter（超时抛 `WebRateLimited`） |

## 卸载原库

确认测试通过后：

```bash
pip uninstall easyquotation
```
