# 许可白名单（License Allowlist）

> 本文档定义 atst 项目接受的第三方许可类型及其与项目自有许可（**MIT**）的兼容性分析。
> 检查工具 `atst.tools.check_originality` 依据此白名单判断 `license_ok` 字段。

## 项目自有许可

atst 以 **MIT License** 发布（见项目根目录 `LICENSE` 文件）。

MIT 是最宽松的开源许可之一，允许商业使用、修改、分发、私用，唯一要求是
保留版权声明与许可声明。

## 白名单

以下许可类型被明确接受。检查工具在检测到这些 SPDX id 时，`license_ok` 为 `True`。

| SPDX ID | 名称 | 与 MIT 兼容性 | 备注 |
|---------|------|---------------|------|
| **MIT** | MIT License | ✅ 完全兼容 | 项目自有许可 |
| **BSD-2-Clause** | BSD 2-Clause "Simplified" License | ✅ 完全兼容 | 与 MIT 等效的宽松许可 |
| **BSD-3-Clause** | BSD 3-Clause "New" or "Revised" License | ✅ 完全兼容 | 增加非背书条款，仍为宽松许可 |
| **Apache-2.0** | Apache License 2.0 | ✅ 兼容（含专利授权） | 含显式专利授权与贡献者声明，兼容 MIT |
| **GPL-2.0** | GNU General Public License v2.0 | ⚠️ 有条件兼容 | 见下方 GPL 兼容说明 |
| **GPL-3.0** | GNU General Public License v3.0 | ⚠️ 有条件兼容 | 见下方 GPL 兼容说明 |
| **LGPL-2.1** | GNU Lesser GPL v2.1 | ⚠️ 有条件兼容 | 动态链接方式使用，MIT 代码不受传染 |
| **LGPL-3.0** | GNU Lesser GPL v3.0 | ⚠️ 有条件兼容 | 动态链接方式使用，MIT 代码不受传染 |
| **MPL-2.0** | Mozilla Public License 2.0 | ✅ 兼容（文件级） | 文件级 copyleft，未修改的 MIT 文件不受影响 |

## 未列入白名单的许可

以下许可类型 **不在** 白名单内。检查工具将报告 `license_ok = False`，需人工评审：

- **AGPL-3.0** — 网络服务 copyleft，与 MIT 不兼容（会传染）
- **EPL-2.0** — Eclipse Public License，强 copyleft
- **CDDL-1.0** — Common Development and Distribution License，文件级 copyleft 但含强制专利授权
- **Unlicense** / **CC0-1.0** — 公共领域奉献，非传统许可，需确认项目接受
- **Proprietary / 未标注** — 无法确认许可状态，默认拒绝

## GPL 兼容说明

GPL 系列许可采用"传染性 copyleft"模型——链接 GPL 代码后，整体作品必须
以 GPL 发布。这对 atst（MIT 许可）的影响取决于使用方式：

### 推荐做法

- **仅通过动态链接（`.py` 模块 import）使用 GPL/LGPL 库**：LGPL 允许
  这种使用方式，MIT 代码不受传染
- **避免在 atst 核心包内直接 import GPL 库**：这会触发 GPL 传染
- **将 GPL/LGPL 依赖列为 optional extra**：`pyproject.toml` 中的
  `[project.optional-dependencies]` 已是这种模式

### 不推荐做法

- 将 GPL 库的代码复制进 atst 源码树
- 在 `atst/` 包内直接 `import` GPL 库（除非声明为 optional extra 且
  运行时隔离）

### 审计建议

每次引入新的 GPL/LGPL 依赖时，应在 `AUDIT_REPORT.md` 中记录：

- 依赖名称与版本
- 使用方式（动态链接 / 复制 / 编译期链接）
- 许可证条款确认
- 审批人

## MPL-2.0 兼容说明

Mozilla Public License 2.0 是**文件级 copyleft**：

- 未修改的 MIT 文件可以原样包含在 MPL 项目中
- 修改 MPL 文件后，修改部分必须保持 MPL 许可
- 新增文件可以选择任何兼容许可（含 MIT）

因此，atst 可以安全地**消费** MPL-2.0 许可的库（通过动态链接），
但不应将 atst 源码修改后以 MPL 发布。

## 更新流程

1. 确认新许可的 SPDX id（参考 [SPDX License List](https://spdx.org/licenses/)）
2. 评估与 MIT 的兼容性
3. 更新本文件的白名单表格
4. 同步更新 `atst/tools/check_originality.py` 中的 `ALLOWED_LICENSES` 常量
5. 在 `AUDIT_REPORT.md` 中记录变更
