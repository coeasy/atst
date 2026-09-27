# atst 重构方案 v10（2026-09，✅ 已全部落地）

> 承接 v8（结构治理，已落地）与 v9（路由合并 + client 共享骨架，已落地）。
> 本文基于 v9 终验后的**增量审计**：四道门禁全绿（全量 pytest / 对抗矩阵 /
> golden / strict 可达性），`test_route_parity.py` 复跑 3 次无偶发。
> 范围取值：提问未获回复，按推荐默认执行——低风险项全做，中高风险项（解析器/
> 传输层拆分、错误树梳理）留 v11 评估。

## 一、现状快照（v9 后）

- **架构主链路**：单一取数内核（router 五级链）+ facade 熔断壳 + client
  `_mixin` 共享骨架 + web 惰性导入 + 域 Mixin 会话，双实现已消除。
- **功能完整性**：README 宣称能力全部落地；「受限项」（复权 local-only、
  全市场不含港美、trade 模拟器、异步门面紧凑设计）均为显式文档化的能力边界。
- **链路贯通**：可达性孤儿=0（trade/native/streaming.push/output 均
  白名单登记并附 ADR 理由）。
- **千行级文件余量**：facade/api.py 1297、web/base.py 1229、
  protocol/parsers/std7709.py 1216、mcp_server.py 1062、fundflow.py 1007。

## 二、v10 改造项（按执行顺序）

### P10-1 删除 CredentialStore（执行既有 ADR-007-010）
- 该 ADR 已裁定「v8 删除」但未执行：`security/credentials.py`（526 行）全库
  零调用方，仅 `security/__init__.py` 与顶层 `_LAZY` 导出。
- 删除：模块文件 + 两处导出 + `tests/security` 对应用例 + docs/api 行 +
  `_reach_allow.txt` 的 `atst.security.credentials` 条目；`atst/security`
  包保留（ADR 复审注释）。
- 若未来 trade/CLI 出现真实凭据需求，按 ADR 重新设计（env→file 两级起步）。

### P10-2 facade 路由壳拆分
- `facade/api.py`（1297 行，87 方法）中**路由/熔断基础设施**独立为
  `facade/routing.py`（~150-200 行）：`_iter_routes`/`_try_routes`/
  `_route_cooling`/`_note_route_failure`/`_note_route_success`/
  `reset_circuit`/`_attach_route_errors`/`_require_tdx_route`/
  `_ROUTE_FAIL_LIMIT`/`_ROUTE_COOLDOWN_SECONDS` 收口为
  `RouteSelector`（或 Mixin），`UnifiedQuoteAPI` 组合使用。
- 公开面不变：`from atst.facade.api import UnifiedQuoteAPI, quote_api`；
  熔断状态仍在实例上（`_route_fail_counts` 等属性名不变，W11 测试守护）。
- 风险：低。验收：`tests/facade`（含 test_w11_w12_w13 熔断语义）全绿。

### P10-3 web/base.py 拆分
- 1229 行按职责拆三块（Mixin 组合，`BaseWebSource` 名称与 MRO 不变）：
  - `web/_base_core.py`：`BaseWebSource` 生命周期 + 模板方法
    （`source_name/build_url/parse`、`fetch()` 编排、反爬头、限流）；
  - `web/_base_retry.py`：重试退避 + `_record_failure/_reset_failures/
    _check_deprecated` 失败双桶计数；
  - `web/_base_em.py`：`_EastmoneyJson`（主机池 failover/黑名单，corporate/
    fundflow/adapters 多处继承）。
- 单文件 <600 行；`from atst.web.base import BaseWebSource, _EastmoneyJson`
  等全部现有导入路径不变（`web/base.py` 保留为 re-export 门面或直接承载
  组合类）。
- 风险：低-中。验收：`tests/web` 423 例全绿。

### P10-4 文档与遗留清理
- `atst/sink/__init__.py` docstring 中 `atst.sinks` 旧名引用改为
  `atst.output`（sinks shim 文案保持）。
- `scripts/_reach_allow.txt` 条目与 ADR-007-010 状态对齐（P10-1 后）。
- README 特性表补一行 streaming/native 定位（ADR-011 结论）。

### P10-5 route_parity flaky 根因排查
- v9 期间出现过 1 次未复现失败（`test_start_beyond_len_clamps` 等 2 例）。
- 排查方向：`.day` 合成 fixture 的共享 tmp 目录、golden_root 全局配置泄漏、
  monkeypatch 恢复顺序；找到根因则修，复现不出则在测试内加隔离
  （monkeypatch config 单例）并记录。

## 三、明确不做（v11 候选，附理由）

| 项 | 理由 |
|---|---|
| `protocol/parsers/std7709.py`（1216 行）拆分 | 61 解析器注册表以命令号为键跨域分布，拆文件要动注册路径与 golden 门禁映射，收益仅是文件尺寸；等 P10-2/P10-3 模式稳定后再评估 |
| `transport/async_.py`(831)/`pool.py`(800)、`mcp_server.py`(1062) 拆分 | 结构内聚尚可，无双实现问题；v9 刚完成大批次，避免连续大动 |
| errors.py 40+ 类梳理 | 影响面广（错误码是公开契约），需独立设计批次 |
| `_mixin.py` trampoline 回滚 | traceback 多两帧为已知代价，换 -1100 行重复体值得；保留 |

## 四、执行顺序与门禁

```
P10-1（独立） → P10-2 → P10-3 → P10-4 → P10-5
```

每步后：`pytest tests/ -q` + `scripts/audit_reachability.py --strict` +
对抗矩阵 + golden 门禁；完成后 CHANGELOG 记录、README 同步。

## 五、预期收益

- 消除 ADR 已裁定未执行的遗留（CredentialStore）。
- 千行文件从 5 个降到 3 个（均为「暂不动」名单内）。
- facade/web 底座职责三分，后续演进（如错误树梳理、解析器拆分）有干净落点。
> **状态（2026-09-06）**：P10-1~P10-5 全部完成，四道门禁通过，执行记录见 CHANGELOG。

## 附：P11 后续批次执行记录（2026-09-06，用户指示继续优化）

- P11-0 ruff 门禁 93→0（F821 修复、I001 排序、F401 门面豁免 + 死导入清理、format 对齐）。
- P11-1 `integration/mcp_server.py` 1062 行 → `integration/mcp/` 四模块 + 135 行门面。
- P11-3 `protocol/parsers/std7709.py` 1216 行 → `_std7709_common/_quote/_bars` + 107 行注册门面。
- P11-2 决议：transport 两文件职责单一不强拆（原文「不强拆」条款）。千行大文件余 2 个，均为单一职责域。
- 全部门禁（ruff check/format、全量 pytest、对抗矩阵、golden、strict 可达性）通过。
- 收尾：错误树梳理完成（44 类无重叠对结论 + docs/errors.md 使用指南 +
  errors.py/README 指向同步）——审计清单全部闭环。
