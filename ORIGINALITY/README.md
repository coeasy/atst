# ORIGINALITY — 原创性与许可合规检查

本目录存放 atst 项目的**原创性审计材料**与**许可合规清单**。

## 目录结构

| 文件 | 说明 |
|------|------|
| `README.md` | 本文档：原创性检查流程说明 |
| `LICENSE_ALLOWLIST.md` | 许可白名单及与 MIT 的兼容性分析 |
| `AUDIT_REPORT.md` | 审计占位报告（待填充） |

## 检查工具

`python -m atst.tools.check_originality` — 基于 AST 的原创性检查器，
零外部依赖，仅使用 Python 标准库。

### 检查内容

1. **许可头检测** — 文件首部是否含预期的版权/许可注释
2. **SPDX 许可识别** — 从文件内容中识别 SPDX 许可 id，并与白名单比对
3. **样板 docstring** — 函数/类的 docstring 是否为常见占位符（`pass` / `TODO` / 空串 / 与函数名相同等）
4. **已知项目指纹** — 注释或字符串中提及的外部项目名（mootdx / pytdx / tdxpy 等）及移植声明
5. **外部导入审计** — 识别非标准库、非 atst 内部的第三方导入，列出供人工审阅

### 用法

```bash
# 检查整个源码树
python -m atst.tools.check_originality atst/

# CI 模式：任一问题返回非零退出码
python -m atst.tools.check_originality --strict atst/

# JSON 输出（供自动化流水线消费）
python -m atst.tools.check_originality --json atst/

# 自动补上缺失的许可头（仅 .py / .pyi）
python -m atst.tools.check_originality --fix atst/

# 检查单个文件
python -m atst.tools.check_originality atst/codec/framing.py

# 直接运行脚本（无需安装）
python atst/tools/check_originality.py --strict atst/
```

### 结果结构

每个文件产出一条 `OriginalityResult`：

| 字段 | 含义 |
|------|------|
| `file` | 文件路径 |
| `license_detected` | 检出的 SPDX 许可 id（`"unknown"` 表示未检出） |
| `license_ok` | 检出许可是否在 `LICENSE_ALLOWLIST.md` 白名单内 |
| `license_header` | 文件首部是否含预期的版权/许可头注释 |
| `suspicious_patterns` | 触发的启发式指纹描述列表 |
| `external_imports` | 非标准库、非 atst 内部的第三方导入列表 |
| `is_original` | 综合判断：`suspicious_patterns` 为空即为 `True` |

### 许可头格式

默认检查以下模式（任一命中即视为有头）：

```python
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License
```

以下变体同样通过：

- `# SPDX-License-Identifier: MIT`
- `# Licensed under the Apache License, Version 2.0`
- `# Copyright ... atst ...` + `# Licensed under ...`

`--fix` 模式将自动在文件首部插入默认许可头（保留 shebang 行位置）。

## 工作流

1. **日常开发**：`--fix` 模式自动补齐缺失的许可头
2. **提交前**：`--strict` 模式确保无告警
3. **定期审计**：运行完整检查，将结果填入 `AUDIT_REPORT.md`
4. **引入新依赖**：更新 `LICENSE_ALLOWLIST.md` 并重新运行检查

## 设计原则

- **零依赖**：仅使用 `ast` / `dataclasses` / `pathlib` / `json` / `re` / `sys`
- **AST 优先**：用 `ast` 模块提取 docstring 与 import，避免脆弱正则误判
- **启发式检测**：指纹库可随项目演进扩展（见 `check_originality.py` 顶部常量）
- **宽容策略**：无法判断的文件（二进制、不可读）不报告为抄袭，仅标记为未检出许可
- **CI 友好**：`--strict` 返回非零退出码，可直接接入 GitHub Actions / GitLab CI
