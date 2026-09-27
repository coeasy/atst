# atst 重构升级方案 v7 —— v6 收官与结构收敛

> 日期：2026-09-05/06
> 定位：本文是 **v6（ARCHITECTURE_AND_OPTIMIZATION_PLAN_v6.md）的执行收官方案**。
> v6 完成了 30 项问题清单的审计与排期；本文在 v6 部分落地后重新梳理项目全貌，
> 给出**剩余项的具体改造步骤**并标注验收标准，作为本轮「全部改造完」的执行依据。

---

## 1. 项目全貌梳理（当前状态）

### 1.1 主要功能（7 大能力域）

| 能力域 | 模块 | 说明 |
|---|---|---|
| TDX 协议栈 | `protocol/` | 5 协议族（STANDARD/EXTENDED/MAC/GOODS/F10）、85 命令账本（commands.py）、~80 解析器、三级兜底派发（L1 精确 / L2 通用 / L3 原始透传） |
| 客户端 | `client.py` | TdxClient / AsyncTdxClient 同步异步全镜像 + 7 个子类（Goods/ExMarket/Mac/F10 等），`request_result()` 暴露 ParseResult 元数据 |
| 传输层 | `transport/` | ConnectionPool 多主站槽位、TokenBucket 限流、协议嗅探、测速 |
| Web 行情源 | `web/` | 7 上游家族 / 30 源 / WebQuoteSession 门面（45+ 方法）/ 统一容错（重试退避+失败计数+下线检测+共享限流桶） |
| 降级路由 | `sources/` + `facade/` | DataSourceRouter 5 级链（tdx→web→reader→cache→synthetic）；UnifiedQuoteAPI 路由选择+熔断+口径守卫 |
| 缓存/存储 | `cache.py` | KlineCache（TTL）；reader 层 parquet 离线数据 |
| 集成/工程 | `integration/`、`cli.py`、`mcp_server.py` | HTTP 43 端点、MCP 23 工具、CLI 子命令、26 Hooks 三阶段 |

### 1.2 数据契约（不可变）

- 价格 = 元（float）、成交量 = **股**（int）、成交额 = 元（float）
- 多源归一化收敛于 `web/normalize.py`（腾讯「手」×100 → 股；港美直接「股」）
- 命令号单一事实源 = `protocol/commands.py` 的 `CMD` 字典（v6 B3 已落地）

### 1.3 工程现状（实测）

| 指标 | 值 |
|---|---|
| `atst/` Python 文件 | 115 |
| client.py 行数 | 1857（同步/异步全镜像所致） |
| web/facade.py 行数 | 1320（WebQuoteSession god-class） |
| 测试 | **2014 passed / 8 skipped / 1 xfailed**（`-m "not network"`） |
| 覆盖率 | 76.09%（门禁 fail_under=75） |
| `_f`/`_i` 本地重复定义 | web 层 **13 个文件**（B7 待收口） |
| STATUS_OFFLINE 命令 | 7 条（无 client 层 fail-fast，B5 待做） |
| 熔断 | 仅 biz_failures 计数，无状态机（B4 待做） |
| CredentialStore 调用方 | 0（B10 待处置） |

### 1.4 v6 已完成项（本轮之前）

| 项 | 内容 | 状态 |
|---|---|---|
| G1 | 覆盖率门禁 75%（pyproject + CI + Makefile） | ✅ |
| G2 | 网络测试隔离（network marker + live-smoke.yml） | ✅ |
| G3 | 规格漂移门禁（spec_audit --strict 入 CI） | ✅ |
| A1/A2 | request() 消费 as_format；新增 request_result() | ✅ |
| A3 | Web 容错旁路收口：12 处裸 `client.get/post` → `_request_text`/`_request_post`（`_request_http` 内核统一 GET/POST/JSON 重试） | ✅ |
| A4 | 腾讯 fetch_all 单批失败：记录→退避→重试一次→UserWarning（不再静默丢数据） | ✅ |
| A5 | 腾讯 K 线 amount 全 0 → 一次性 UserWarning | ✅ |
| B3 | 命令号单一事实源：`CMD` 字典 + `cmd()`；client.py 52 处字面量全部替换 | ✅ |
| B9 | RateLimiter.set_bucket 公共方法，消除 `_buckets` 私有属性跨文件写 | ✅ |

---

## 2. 剩余项改造方案（本文执行范围）

### 2.1 B7+C4 · Web 辅助函数上提 + NaN 统一（P1，机械）

- **方案**：
  1. `web/base.py` 新增 `num_f(value, default=0.0)` / `num_i(value, default=0)`，内部用 `math.isfinite` 校验——JSON `NaN`/`Inf` 一律回落 default，不再伪装合法值；
  2. 13 个文件的本地 `_f`/`_i` 删除，改为 `from .base import num_f, num_i`（保持模块内调用名不变的用 `num_f as _f` 别名导入，最小化 diff）；
  3. `history.py` 原 `math.isfinite` 版本语义并入 `num_f`（其余文件由此获得 NaN 防御）。
- **验收**：`grep -rn "^def _f\b" atst/web/` 仅剩 base.py（或 0 处）；注入 `NaN` 的 JSON 解析测试通过。

### 2.2 B5 · offline 命令 fail-fast（P1，高价值低成本）

- **方案**：
  1. `errors.py` 新增 `CommandOffline(TdxError)`；
  2. `client.py` 请求入口（`request`/`request_result` 的调用前，具体在发请求的公共路径）查账本 `get_command(cmd).status`，`STATUS_OFFLINE` 且无回退路径 → 直接抛 `CommandOffline`（含替代命令文案），不再走 3s×N 超时链；
  3. `quotes_snapshot`（0x054C）**跳过 fail-fast**——它有逐只 0x0530 回退，且批量命令的 offline 状态由回退路径消化。
- **验收**：调用 offline 命令 <10ms 抛明确异常；`quotes_snapshot` 回退路径测试仍绿。

### 2.3 C9 · 腾讯 K 线超限自动分段（P2，v5 PG8 补课）

- **方案**：`KlineSource.fetch_bars` 当 `count > TENCENT_KLINE_MAX(800)` 时按 800 根分段请求（沿日期向前翻页），拼接去重后返回全量；分段间保留告警提示数据为分段拼接。
- **验收**：请求 2000 根 → 返回 2000 根（离线用分段 fake 验证）。

### 2.4 C1/C3/C6/C8 · 文档与命名整洁（P2，低风险）

- **C1**：CLI docstring「32 端点」改为动态统计口径（`http_server` 端点数在启动时统计，docstring 写「40+ 端点（以启动日志为准）」消除硬编码漂移）；
- **C3**：`native.py` 保留但模块 docstring 顶部加 `.. deprecated::` 说明（原生扩展源码已移除，`NATIVE_AVAILABLE` 恒 False 时仅提示）；确认不再发布 parity 误导；
- **C6**：`transport/pool.py` `request_multi` 注释升级为完整 docstring，书面说明「同连接多帧」取舍（为什么不做并发槽：TDX 协议帧序依赖单连接）；
- **C8**：`BinaryClient`/`BridgeClient`/`HqClient`/`AsyncUnifiedQuoteAPI` 兼容门面类 docstring 加 `.. deprecated::` 与替代方案（指向 `TdxClient`/`UnifiedQuoteAPI`）。

### 2.5 C2 · i18n 更名 charset（P2）

- **方案**：`atst/i18n/` 更名 `atst/charset/`（内容本就是字符集探测）；`atst/i18n` 保留**兼容 shim**（`from .charset import *` + DeprecationWarning），外部导出同步更新。
- **验收**：`import atst.charset` 可用；`import atst.i18n` 仍可用但告警。

### 2.6 C5 · 缓存覆盖面（P2）

- **方案**：`cache.py` 新增 `QuoteCache`（默认 TTL 3s，对齐盘中刷新节奏）与 `MinuteCache`（TTL 60s）；门面层 `get_quote`/`get_minute` 接入（缓存 miss 才走源）；K 线维持 KlineCache。
- **验收**：同一 symbol 连续两次 get_quote，第二次命中缓存（注入 fake 源计数断言 1 次）。

### 2.7 C7 · mypy 分拆配置（P2，配置）

- **方案**：`[tool.mypy]` 基础配置保持核心严格（`ignore_missing_imports=false`），对可选依赖模块（`atst.native`、web 可选 httpx/fastapi 相关）加 `[[tool.mypy.overrides]] module=... ignore_missing_imports=true`。
- **验收**：`mypy atst/` 通过且核心模块不再被全局宽松掩盖。

### 2.8 B10 · CredentialStore 处置（P1）

- **方案**：短期无调用方 → **标记 deprecated**：`security/credentials.py` 类 docstring 加 `.. deprecated::` 说明（三级凭据回退为 trade 模拟器预留能力，当前无调用方；若 v8 未接入则删除）；`docs/adr/` 新增 ADR-0001 记录决策。
- **验收**：ADR 存在 + deprecation 标记；不留「死能力」悬念。

### 2.9 B11/B12/B13 · 工程配置三件套（P1，配置）

- **B11 Docker 多阶段**：`builder` 阶段装 dev 依赖跑 pytest 冒烟；`runtime` 阶段仅 `pip install .`，不 COPY `tests/`、`PROTOCOL_SPEC/`；
- **B12 manylinux wheels**：`wheels.yml` 引入 cibuildwheel（manylinux2014 x86_64/aarch64，Python 3.10–3.13），产物上传；
- **B13 文档入 CI**：ci.yml 新增 docs job——mkdocs 构建（若配置存在则构建，否则做 markdown 链接完整性检查：docs 内相对链接指向的文件必须存在）。
- **验收**：workflow YAML 语法有效（actionlint 或 python yaml 解析）；docker build 逻辑审查通过（本环境无 docker daemon，标记为 CI 验证项）。

### 2.10 B4 · 连接池熔断状态机（P1，中等）

- **方案**：`transport/pool.py` 为每 host 引入 `CircuitState`（HEALTHY→DEGRADED→OPEN→HALF_OPEN）：
  - 连续失败 ≥3 → DEGRADED（请求仍发但优先级降）；连续失败 ≥8 → OPEN（**直接跳过该 host**，不再吃 3s 超时）；OPEN 后冷却 30s → HALF_OPEN（单次探测）；探测成功 → HEALTHY；
  - `biz_failures`（业务层解析失败）按 0.5 权重计入连续失败。
- **验收**：注入持续失败主站 → 熔断后该 host 被跳过（单测断言不发请求）；冷却后 HALF_OPEN 探测恢复。

### 2.11 B1 · 同步/异步镜像消除（P1，最大结构收益·分步）

- **方案（本轮执行第一步，零行为变更）**：抽 `client.py` 顶部 `_ClientCore` mixin——承载**纯协议构造逻辑**（请求体拼装、`_emit`、`_row_to_quote`/`_row_to_bar`、分页参数校验），`TdxClient`/`AsyncTdxClient` 及 7 子类继承之；传输 seam（`_pool.request` / `await self._pool.request`）保留在各自类中。
- **验收**：2014 测试全绿（行为零变更）；client.py 行数下降 ≥8%（第一步目标；第二步合并方法体留待后续，避免一次性 25% 的大爆炸）。

### 2.12 B2/B6 · 降级引擎统一 / facade 拆分（P1，标注为分步后续）

- **B2**：本轮完成**第一步**——`facade/api.py` 的 `local` 路由注释明确委托关系与差异说明，并把两套引擎的口径差异（datetime/条数）写入 §4 对拍测试的 TODO 标注；完整收窄留待 v8（涉及 API 兼容面大，需独立窗口）。
- **B6**：本轮完成**第一步**——`WebQuoteSession` 引入共享 `HttpClient` 惰性单例（首次调用创建、`close()` 释放），消除「每次调用新建+关闭」的连接开销；子会话拆分留待 v8。
- **验收**：B6 第一步——同一 session 实例连续调用复用同一 HttpClient（单测断言 `is` 同一对象）。

### 2.13 B8 · 缩放口径三处一致性（P1）

- **方案**：新增 `tests/web/test_scale_consistency.py`——对每个源断言 `SourceSpec.scales`、`normalize.py` 缩放表、解析层实际缩放三者一致（腾讯 A 股 K 线 ×100、港美 ×1 等已知锚点）；发现漂移即红。
- **验收**：故意改错一处缩放 → 测试红。

### 2.14 B14 · golden 体系

- **维持现状**（v6 明确不做重写：530 组真实样本真实性优先）。

---

## 3. 实施顺序与回归门禁

```
B7+C4 → B5 → C9 → C1/C3/C6/C8 → C2 → C5 → C7 → B10 → B11/B12/B13 → B4 → B8 → B1 → B2/B6 第一步
```

**全程门禁**：每批完成后跑
`pytest tests/ -m "not network" --cov=atst --cov-fail-under=75` + `ruff check atst/`。
最终交付前跑全量回归 + spec_audit --strict。

## 4. 风险与回滚

| 风险 | 缓解 |
|---|---|
| B7 改 13 文件，改名遗漏 | 用 `num_f as _f` 别名导入，调用点零修改 |
| B5 fail-fast 误伤（某主站实际可用） | 账本 status 是实测事实且仅 quotes_snapshot 有回退豁免；误伤修复路径 = 更新账本 status |
| B4 熔断误开路 | HALF_OPEN 单次探测 + 冷却 30s；DEGRADED 不跳过只降优先级 |
| B1 mixin 抽取触碰 1857 行核心 | 纯方法上移零行为变更，2014 测试守门 |

## 附录：修改记录

| 日期 | 记录 |
|---|---|
| 2026-09-06 | v7 初版：v6 收官方案。已完成项状态固化（G1-G3/A1-A5/B3/B9），剩余 18 项给出具体步骤与验收标准 |

## 附录 B：实施状态（2026-09-06 收官）

| 项 | 状态 | 落点 |
|---|---|---|
| B7+C4 | ✅ | `web/base.py` 新增 `num_f`/`num_i`（`math.isfinite` NaN 防御）；13 文件本地 `_f`/`_i` 清零；adapters_index 保留 None 语义变体 `_fopt`（指数成分可选字段缺失不填 0，测试钉住） |
| B5 | ✅ | `errors.CommandOffline`（E3035，不可重试）；`client_core._guard_offline`；同步/异步 `_req` 统一传输入口；0x054C 豁免（有逐只回退）；实测 0.01ms fail-fast；新增 `tests/client/test_offline_failfast.py`（5 用例） |
| C9 | ✅ | `KlineSource.fetch_bars` 超限自动分段（seg+1 重叠补偿、无新增即停、超收裁剪）；`build_url` 支持 `end`；新增 `tests/web/test_c9_kline_segment.py`（4 用例，2000 根→2000 根） |
| C1 | ✅ | CLI「32 端点」→「40+ 端点（以启动日志为准）」，消除硬编码漂移 |
| C3 | ✅（既有） | `native.py` 已带 v1.4.0 deprecation 计划（v1.6.0 删除），无需改动 |
| C6 | ✅ | `request_multi` docstring 书面说明「同连接多帧」取舍与上层并发替代方案 |
| C8 | ✅ | `BinaryClient`/`BridgeClient`/`HqClient`/`AsyncUnifiedQuoteAPI` 标注 `.. deprecated:: v7` 与替代方案 |
| C2 | ✅ | `atst/i18n` → `atst/charset`（名实一致）；旧路径保留 shim + DeprecationWarning + `i18n.encoding` 子模块别名 |
| C5 | ✅ | `cache.QuoteCache`（TTL 3s，线程安全，命中返回拷贝）；接入 `DataSourceRouter.quotes` 读穿/回写；分时「不缓存」写入文档（覆盖面与文档一致）；新增 2 用例 |
| C7 | ✅ | mypy 核心严格化（`ignore_missing_imports=false` + `check_untyped_defs`），可选依赖/native 单独 override |
| B10 | ✅ | `CredentialStore` 标注 deprecated（v8 无调用方则删除）；新增 `docs/adr/ADR-007-010.md` |
| B11 | ✅ | Dockerfile 多阶段：builder 跑测试冒烟，runtime 仅运行代码（剔除 tests/PROTOCOL_SPEC/docs） |
| B12 | ✅ | wheels.yml Linux job → cibuildwheel（manylinux2014 × x86_64/aarch64 × cp310-313） |
| B13 | ✅ | ci.yml 新增 docs-links job（markdown 相对链接完整性）；本地已清出 1 条真实断链（v1 计划中未实施项） |
| B8 | ✅ | 新增 `tests/web/test_b8_scale_consistency.py`（7 用例）：spec↔normalizer 全量比对 + 3 源锚点 + 解析层内联实证（minute/ticks/trends/limit_pool 例外清单）+ identity 防二次缩放 |
| B4 | ✅ | `HostEntry` 熔断字段（circuit/consec_weighted/circuit_opened_at）；连接失败 1.0 / 业务失败 0.5 加权；DEGRADED≥3 / OPEN≥8 / 冷却 30s→HALF_OPEN；选主站跳过 OPEN（全 OPEN 兜底原序，零回归）；`PoolStats.circuit_skips`；新增 9 用例。**异步池镜像列为 v8**（async_.py 失败标记分散，需先集中） |
| B1 | ✅（第一步） | 抽出 `atst/client_core.py`（231 行：符号解析、行→模型、`_emit`、offline 守卫、请求体拼装——纯协议构造零 I/O）；client.py 1857→1714 行；方法体合并（第二步）留 v8 |
| B2 | ✅（第一步） | `facade/api.py` 书面澄清与 DataSourceRouter 的关系/差异/对拍 TODO；完整收窄留 v8 |
| B6 | ✅（第一步） | `web/facade.py` 进程级共享 HttpClient（`_shared_http()`）+ `BaseWebSource._owns_client` 所有权语义；54 处按调用新建注入共享池；子会话拆分留 v8 |

**回归基线（2026-09-06 更新）**：C 盘空间恢复后全量回归 **2040 passed / 8 skipped /
1 xfailed** 全绿（此前 42 个 teardown ERROR 与 1 个 rmtree 型 FAILED 均为
环境级联，已消失）；覆盖率 **76.21%** ≥ 门禁 75；`spec_audit --strict` 通过
（golden 覆盖率 81.0%）；ruff 全绿。

**mypy 收官（2026-09-06 追加）**：C7 验收补课——安装 mypy 实测发现 39 个错误并
全部修复：
- 根因 28 处：B5 抽取的 `_req` 返回注解误写 `bytes`，而 `pool.request` 实际返回
  `ResponseFrame`（运行时无害、注解错误传导）→ 同步/异步 `_req` 注解修正；
- 10 处：可选依赖缺 override（websockets/prometheus_client/duckdb/zstandard/
  tomli；`tomllib` 因 `python_version=3.10` 被 mypy 视作缺失）→ pyproject
  overrides 补齐，同时移除未使用的 `cryptography.*` 段（`warn_unused_configs`）；
- 1 处：`facade.py` `_client` 惰性客户端 None 起步无注解 → `Any`。
最终 `mypy atst/`：**Success: no issues found in 117 source files**。
