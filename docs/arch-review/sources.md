# sources/ 层审查报告（slug=sources）

审查范围：`atst/sources/`（69 文件约 1.8 万行）+ `atst/providers/` 声明层对账。
方法：读包根基座（`_base_http/_base_retry/_base_core/_base_em/_paginate/normalize/sources/session`）、
三家大 adapter（sina 723 / tencent 648 / eastmoney 544）、`fundflow.py`(895) 及 `_session_*` 代表模块；
用脚本对 `atst.providers.MIGRATED_BINDINGS` 全量 282 条绑定做了 factory/method 存在性对账。

## 1. 结论摘要

- 【确定】providers/ ↔ sources/ 的**导入期对账是干净的**：282 条绑定（web_session 237 / direct_adapter 11 / web_adapter 12 / 其余 tdx 族）的 factory 与 `WebQuoteSession` 方法全部可解析，`providers/__init__._validate_channel_declarations` 在 import 期就拒绝 channel 错位。双账本漂移不在「绑定存在性」，而在**能力词表、量纲口径、默认源顺序**三条第二账本上。
- 【高置信】最大的架构落差是**降级/路由禁令被公开门面自己打破**：ARCHITECTURE.md 承诺 provider-first「一个 Plan 永不私选第二 Provider」，内核 `runtime/execute_channel.py` 明说有序降级是「内核禁止的形状」，但 `atst.sources.WebQuoteClient` / `get_quotes()` 缺省路径就是一条多源降级链，且把解析期异常当「源不可用」吞掉继续换源。
- 【高置信】正确性隐患最实的一条：`EastmoneySource.fetch` 只取 `symbols[0]`，多标的批量行情**静默丢数据**。
- 【高置信】HTTP 会话/重试/主机 failover 声称已收口，实际仍有 3 份主机 failover 手写循环、4 份独立 `_get_json`、2 处自写退避；`WebQuoteSession` 组合 19 个 Mixin、184 个公开方法，是按主题切分与按 Provider 归组两套架构互相妥协出来的上帝对象。

## 2. 发现清单

| # | 严重度 | 类型 | 位置 | 问题 | 改法 |
|---|--------|------|------|------|------|
| F1 | 高 | 架构问题 | `atst/sources/__init__.py:373-449` | `WebQuoteClient.quotes` 有序多源降级，与 provider-first 禁令冲突；非 TdxError 被 warning 后继续降级 | 降级只留内核 `FallbackPolicy`；门面缺省改单源 fail-fast，或在文档与异常里显式标注 legacy |
| F2 | 高 | 细节问题 | `atst/sources/eastmoney/adapters.py:81-90` | `EastmoneySource.fetch` 只请求 `symbols[0]`，多标的批量静默丢其余 | 循环/切批请求或显式抛「仅支持单标的」 |
| F3 | 高 | 架构问题 | `atst/sources/session.py:1-19` vs `_session_market.py:310-404` | 会话层声称「不是路由层」，`klines()` 却按市场路由（A股→腾讯 mkline、hk/us→东财），`history()` 按 adjust 自动换东财 | 路由决策上移到 providers 声明（`MARKET_DEFAULT_CAPABILITIES` 已有此表，会话层应查表而非 hardcode） |
| F4 | 高 | 架构问题 | `atst/sources/session.py:176-198` | `WebQuoteSession` 组合 19 个 Mixin、184 个公开方法；`catalog` channel 全部绑到它 | 按 Provider/域拆分会话面，或以绑定表驱动的方法解析替代静态组合 |
| F5 | 中 | 架构问题 | `atst/sources/sources.py:169` vs `atst/providers/sina.py:23` | 能力词表两套：sources.py 用 `quote`，providers 用 `quotes`；无跨表对账测试 | 统一词表或在测试中断言两表同义映射 |
| F6 | 中 | 架构问题 | `atst/sources/sources.py:519` vs `atst/providers/__init__.py:349` | 默认顺序两套：`DEFAULT_FALLBACK_ORDER=(tencent,sina,eastmoney)` 与 `_DEFAULT_PREFERENCE=(derived,tdx,eastmoney,sina,tencent,…)` | 收敛为一份由 providers 声明派生的顺序 |
| F7 | 中 | 架构问题 | `atst/sources/normalize.py:186-260` + `tests/sources/test_b8_scale_consistency.py:18-31` | 量纲三处账本（SourceSpec 系数 / VolumeNormalizer / 解析层内联），靠测试例外清单钉住 | 解析层不再内联缩放，系数只在 normalizer 一处 |
| F8 | 中 | 细节问题 | `atst/sources/_base_em.py:66` vs `eastmoney/adapters.py:249`、`efinance_deriv.py:229,369` | 主机 failover 声称收口为 `_EastmoneyJson._get_json` 一处，仍有 3 份手写 host 循环 | 统一走 `_get_json` / `_ordered_hosts` |
| F9 | 中 | 细节问题 | `atst/sources/baidu/adapters.py:171`、`efinance_fund.py:105`、`adapters_fund.py:249`、`esg.py:113` | 4 份独立 `_get_json`，与 `_base_em` 模块头「5 份拷贝已上收」矛盾 | 上收为带 hooks 的公共 JSON 取数器 |
| F10 | 中 | 细节问题 | `atst/sources/sina/adapters.py:332-344`、`tencent/adapters.py:215` | 自写退避重试（`time.sleep(0.5*attempt)` / `time.sleep(0.5)`），绕开 `_backoff_delay` | 复用 `_request_http(retries=…)` |
| F11 | 中 | 架构问题 | `atst/sources/sina/adapters.py:174` | `fetch_all` 从 `tencent.adapters` 导 `DEFAULT_MAX_PAGES`，跨 Provider 耦合 | 常量上移 `limits.py` |
| F12 | 中 | 细节问题 | `atst/sources/fundflow.py:547,689,787` | 东财 `ut=` token 硬编码散落 3 处 | 集中到常量模块并注明来源日期 |
| F13 | 中 | 细节问题 | `atst/sources/_session_research.py:689`、`_session_efinance.py:58,158` | 裸 `except Exception` 吞错继续，静默缺数 | 至少 `record_warning` 计数并在结果标注 |
| F14 | 中 | 架构问题 | `atst/sources/fundflow.py`（895 行） | 域模块混装东财 5 类 + 新浪资金流；sina 的 boards/esg/news 又散落别处 | 按 Provider 归组：sina 系回收 `sina/`，fundflow 只留东财 |
| F15 | 低 | 细节问题 | `atst/sources/_base_core.py:88-89` | `needs_referer` 一律注入 `finance.sina.com.cn`，对非新浪源是陷阱 | Referer 进 SourceSpec |
| F16 | 低 | 细节问题 | `atst/sources/tencent/adapters.py:148-149` | 高/低缺失回退用现价冒充（`f[33]` 缺 → `f[3]`） | 缺失置 0 或 NaN 并告警 |
| F17 | 低 | 细节问题 | `atst/sources/sina/adapters.py:68-71,90` | 单条字段不足抛异常使整批失败；空结果一律 `SourceDeprecated`（无效代码被当接口下线） | 逐条容错 + 区分「无此代码」 |
| F18 | 低 | 细节问题 | `atst/sources/tencent/adapters.py:497-500` | `parse_minute` 未守卫 payload 非 dict，`payload.get` 可 AttributeError | 复用 `decode_kline_payload` 的 isinstance 守卫 |
| F19 | 低 | 细节问题 | `atst/sources/_session_market.py:148-155` | `quotes()` 把非 TdxError 统一改写成 `CompatibilityError("底层源调用失败")`， ValueError（未知周期）等语义丢失 | 只包传输类，其余透传 |
| F20 | 低 | 细节问题 | `atst/sources/__init__.py:451-466` | `WebQuoteClient.klines` 强制追加 `KLINE` 源，无视调用方 `sources` 配置 | 尊重配置，缺失时报错 |
| F21 | 低 | 细节问题 | `atst/sources/_base_http.py:545-553` | `TokenBucket.acquire` 忙等 sleep 轮询（最粗 0.2/rate 秒） | 可接受；如改用条件变量更稳 |
| F22 | 低 | 细节问题 | `atst/sources/fundflow.py:522,627` | `EastmoneyLimitPoolSource`/`StockChangesSource` 继承 `BaseWebSource`，单主机 push2ex 无 failover，与同文件 `_EastmoneyJson` 家族不一致 | 改继承 `_EastmoneyJson` 并覆写 HOSTS |

## 3. 关键证据摘录

### F1 降级链与 provider-first 冲突（高）

`atst/sources/__init__.py:419-426`：

```python
def quotes(self, symbols: Sequence[str]) -> list[Quote]:
    """按降级顺序尝试各源，返回第一个成功的完整结果。"""
    ...
            except Exception as exc:  # noqa: BLE001  降级链兜底（W6）
```

非 TdxError 分支（:432-441）把 `AttributeError/KeyError` 记 warning 后 `continue` 换下一源。
而 `atst/runtime/execute_channel.py:174` 写明：刻意不复用 `get_quotes`，「按 `web.enabled_sources` 顺序降级，而有序降级是内核禁止的形状」；docs/ARCHITECTURE.md:60「一个 Plan 永不私选第二 Provider」。同一仓库两套裁决并存，公开门面是活着的降级实现。`get_quotes(source=None)`（`__init__.py:481-505`）缺省走这条路。

### F2 东财行情批量静默丢数据（高）

`atst/sources/eastmoney/adapters.py:81-90`：

```python
def fetch(self, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
    symbol = symbols[0] if symbols else ""
    secid = to_eastmoney_secid(symbol)
    path_query = f"/api/qt/stock/get?secid={secid}&fields={self.FIELDS}..."
```

`/api/qt/stock/get` 本身单标的；对比 `TencentSource.build_url`（tencent/adapters.py:107-109）是逗号拼接批量。内核 `_web_quotes`（execute_channel.py:171-183）把 `plan.spec.symbols` 整列表传进来，eastmoney 只回第一只，无告警。

### F3/F4 会话层路由 + 上帝对象（高）

`atst/sources/session.py:176-198` 的 `WebQuoteSession` 继承列表含 19 个 Mixin；`dir()` 实测 184 个公开方法。`_session_market.py:338-344`：

```python
if p.endswith("min"):
    mkt = parse_symbol(symbol).market
    if mkt in ("hk", "us"):
        src = EastmoneyHistoryKlineSource(...)
```

`providers/eastmoney.py` 的 `MARKET_DEFAULT_CAPABILITIES = {"minute_klines": ("hk", "us")}` 已声明同一事实，会话层却再 hardcode 一份。

### F5/F6/F7 三条第二账本（中）

- 词表：`sources.py:169` `SINA capabilities=("quote","rank","all_market")`；`providers/sina.py:23` `_c("quote", "quotes", live=True)`。`quote` vs `quotes` 两套名词，`tests/sources/test_registry_consistency.py` 只对账 sources 内部四方（spec/adapter/normalizer/facade），不碰 providers 声明。
- 默认序：`sources.py:519` `DEFAULT_FALLBACK_ORDER=(TENCENT, SINA, EASTMONEY)`；`providers/__init__.py:349-359` `_DEFAULT_PREFERENCE=("derived","tdx","eastmoney","sina","tencent",…)`。
- 量纲：`test_b8_scale_consistency.py:18-31` 自己列出 `IDENTITY_NORMALIZERS`、`PARSE_LAYER_INLINE` 两张例外表——测试在补偿设计，而不是约束设计。

### F8/F9/F10 传输底座重复（中）

`_base_em.py:66` 自述「此前 5 份 `_get_json` 近似拷贝……现上收为本类一处实现」，但：
- `eastmoney/adapters.py:249` `for host in self.HOSTS:` 手写 failover（且 `except Exception` 裸捕获传输错误，:316 附近）；
- `efinance_deriv.py:229` 与 `:369` 各有一份 `for host in _PUSH2HIS_HOSTS:`；
- `baidu/adapters.py:171`、`efinance_fund.py:105`、`adapters_fund.py:249`、`esg.py:113` 各有独立 `_get_json`；
- `sina/adapters.py:338` `time.sleep(0.5 * attempt)` 与 `tencent/adapters.py:215` `time.sleep(0.5)` 自写退避，未用 `_backoff_delay`（`_base_retry.py:62`）。

### F11 跨 Provider 耦合（中）

`sina/adapters.py:174`：

```python
# 避免循环 import：DEFAULT_MAX_PAGES 定义在 tencent.adapters
from ..tencent.adapters import DEFAULT_MAX_PAGES
```

新浪全市场分页上限取自腾讯模块，注释承认循环 import 风险。

### F13 裸 except 吞错（中）

`_session_research.py:688-689`（risk_scan）：

```python
            except Exception:  # noqa: BLE001 —— 单类雷点失败不拖垮整体扫描
                rows = []
```

`_session_efinance.py:58`（stock_base_info 批量）同型。结果里没有任何「缺了哪类」的信号。

## 4. 待证项

- 【待证】`WebQuoteClient` 降级链在生产代码（CLI/HTTP 面）是否仍被走到：`runtime/execute_channel.py` 已绕开，但 `atst/__init__.py` Quick start 仍教 `get_quotes(..., source=None)` 用法。查法：grep 调用方 `get_quotes(` 不带 source 的实参。
- 【待证】`_t` 缓存穿透参数（`eastmoney/adapters.py:87` `int(kwargs.get('_t', 0))`）是否有调用方真的传时间戳；缺省 0 意味着 URL 恒定，若上游按 URL 限流则无意义。
- 【待证】`hk_quotes(codes, provider="sina")` 的 `provider` 参数绕过了 providers 注册表的选择器体系（`_session_market.py:159`），是否与 `resolve_provider` 冲突。

## 5. 建议的下一步（可落地顺序）

1. 先修 F2（东财批量丢数）——纯正确性，改法局部。
2. 收敛三张第二账本：能力词表、默认顺序、量纲系数各留一份，删 tests 里的例外清单。
3. 把 `WebQuoteClient` 降级链标记 legacy 并默认关闭（与内核禁令对齐），或在 ARCHITECTURE.md 承认「门面层允许显式配置的有序降级」，二选一，别两处矛盾。
4. 传输底座二次收口：host failover 只留 `_EastmoneyJson._ordered_hosts`，自写退避换 `_request_http(retries=…)`；`DEFAULT_MAX_PAGES` 上移 `limits.py`。
5. 会话面瘦身：`klines`/`history` 的市场/复权路由改为查 `MARKET_DEFAULT_CAPABILITIES`；19 个 Mixin 按 Provider 域拆为 3-4 个会话类。
