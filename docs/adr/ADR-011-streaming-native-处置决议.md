# ADR-011：streaming 平行实现与 native 实验层处置决议

- 状态：已接受（Accepted）
- 日期：2026-09-06
- 关联：REFACTOR_PLAN_v8.md / REFACTOR_PLAN_v9.md Q4-3 / ARCHITECTURE_AUDIT_v8.md §二.A.6

## 背景

可达性审计（v8）确认「有实现但主链路零引用」的模块，其中两个属 streaming 域：

1. `atst/streaming/engine.py` —— 白名单注释称「QuoteStream 平行实现」，但
   复核其 docstring（M1 决断，v1.4.0 用户拍板「接线」）：DeltaMerger/
   BackpressureQueue/ReconnectPolicy **已构成 QuoteStream 生产内核**，
   白名单注释过时，属扫描器可见性问题而非真孤儿；
2. `atst/streaming/push.py` —— 0x0547 推送通道组件（传输解耦设计：
   独占连接 + fake 可离线测试，L2 布局 best-effort 有 L3 兜底），文档完备、
   有测试，属**可选高级 API**而非半成品；
3. `atst/native/` —— Rust 加速实验层（自测对拍，非热路径）。

三者均已在 `scripts/_reach_allow.txt` 白名单登记。v9 需要给出收敛决议，避免「半成品无限期悬置」。

## 决策

### streaming：**维持现状，修正定位注释**

- `engine.py`：生产内核（M1 已接线）——白名单注释由「平行实现，G2 收敛」
  修正为「QuoteStream 生产内核组件（M1 接线）」，消除误导。
- `push.py`：保留为公开高级组件 API；docstring 已声明独占连接约束与
  L2 best-effort 语义，无需改动；真实主站推送帧定标仍待样本（与
  PROTOCOL_SPEC 采集闭环衔接，非本仓可独立完成）。

### native（Rust 实验层）：**保留，维持批次 H 待决策**

- 对拍测试存在且绿，删除无收益；加速收益需 benchmark 数据支撑（`benches/` 已有骨架）。
- 若连续两个大版本（v10/v11）仍无热路径接线与 benchmark 证据，则整体移除（届时从 git 历史可找回）。

### 复审节奏

每个大版本发布前重跑 `scripts/audit_reachability.py`，白名单条目须附带本 ADR 编号或等价理由；无理由的孤儿一律接线或删除。

## 后果

- 主链路（client/facade/integration）零改动，无回归风险。
- 「半成品」状态从**隐性悬置**转为**显性定位**：每个模块的保留/移除条件可查。

## 备选方案（未采纳）

- **删除 engine/push**：engine 是生产内核（删不得）；push 是文档完备的可选组件，删除丢失能力。
- **push 强行接入主链路**：真实主站推送帧需样本定标，仓内无法验证，强接只会制造不可测路径。
- **立即删除 native**：无 benchmark 证据支持「无用」结论。
