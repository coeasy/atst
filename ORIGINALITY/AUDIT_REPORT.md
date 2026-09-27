# 原创性审计报告

> 检查工具：`python -m atst.tools.check_originality`
> 许可白名单：`ORIGINALITY/LICENSE_ALLOWLIST.md`
> 复现：`python -m atst.tools.check_originality --json --strict atst`

---

## 基本信息

| 字段 | 值 |
|------|------|
| 审计日期 | 2026-08-31 |
| 审计范围 | `atst/` 全部 Python 源码（含子包） |
| 审计工具 | `atst.tools.check_originality`（AST 级静态扫描，零依赖） |
| 许可白名单 | MIT、BSD-2/3、Apache-2.0、GPL-2/3、LGPL-2.1/3、MPL-2.0 |
| 项目许可 | MIT（`LICENSE`） |

---

## 执行摘要

**结论：通过（0 疑似抄袭）。** 全部 84 个源码文件均为本项目原创实现，
未发现任何「移植/复制/改写自闭源项目」的指纹；许可头覆盖 100%；
外部导入均为白名单许可或可选依赖。

- 检查文件总数：**84**
- 疑似抄袭文件数：**0**
- 许可头齐备：**84/84**（MIT，含 SPDX 引用）
- 外部导入记录：**17 条**（全部为可选依赖或标准库别名）

---

## 检查方法

```bash
python -m atst.tools.check_originality --strict atst
```

### 检查维度

1. **许可头** — 文件首部版权/许可注释（缺失即 `--strict` 失败）
2. **SPDX 许可识别** — 内容中的许可声明与白名单比对
3. **样板 docstring** — 占位符 / 空串 / 与函数名相同
4. **拷贝指纹** — "Ported from / Copied from / Adapted from / Original author:" 等移植声明
5. **项目引用** — mootdx/pytdx/tdxpy 等已知项目的**信息性**引用（不影响 is_original 判定）
6. **外部导入审计** — stdlib/内部/第三方三方分类

---

## 发现

### 严重问题（需立即修复）

| 文件 | 问题类型 | 描述 | 修复状态 |
|------|----------|------|----------|
| （无） | — | — | — |

### 中等问题（建议在发布前修复）

| 文件 | 问题类型 | 描述 | 修复状态 |
|------|----------|------|----------|
| （无） | — | — | — |

### 轻微问题（可择机修复）

| 文件 | 问题类型 | 描述 | 修复状态 |
|------|----------|------|----------|
| atst/compat/mootdx.py 等 | 项目引用（信息性） | 兼容垫片中提到 mootdx API 名称，属预期兼容行为 | 已确认为设计意图 |
| atst/config/loader.py | 未知外部导入 tomli | Python<3.11 的 stdlib 回退（pyproject 已声明条件依赖） | 已确认 |

---

## 外部依赖审计

核心运行时**零强制依赖**。以下为可选 extras 及其许可：

| 依赖 | 许可 | 白名单状态 | 使用方式 |
|------|------|------------|----------|
| pydantic | MIT | ✅ | 可选：config schema 校验 |
| pandas | BSD-3 | ✅ | 可选：DataFrame sink |
| pyarrow | Apache-2.0 | ✅ | 可选：Parquet sink |
| duckdb | MIT | ✅ | 可选：DuckDB sink |
| httpx | BSD-3 | ✅ | 可选：web 源 |
| fastapi | MIT | ✅ | 可选：HTTP 集成 |
| uvicorn | BSD-3 | ✅ | 可选：HTTP 集成 |
| websockets | BSD-3 | ✅ | 可选：WS 服务 |
| zstandard | BSD-3 | ✅ | 可选：zstd 压缩 |
| keyring | MIT | ✅ | 可选：凭据存储 |
| tomli | MIT | ✅ | 条件：Python<3.11 |
| tomli-w | MIT | ✅ | 可选：config 写出 |

---

## 建议

1. **保持 CI 门禁**：pre-commit（`.pre-commit-config.yaml`）与 GitHub Actions
   均接入 `check_originality --strict`，新增文件无许可头即拦截。
2. **兼容垫片隔离**：`atst/compat/` 是唯一允许出现外部项目 API 名称的目录，
   后续新垫片一律放此目录并在 docstring 注明「接口形状参考，实现原创」。
3. **白名单变更须评审**：向 `LICENSE_ALLOWLIST.md` 添加新许可需经 GOVERNANCE
   流程，并复核与 MIT 的单向兼容性。

---

## 审批

| 角色 | 姓名 | 签名 | 日期 |
|------|------|------|------|
| 审计人 | atst 维护组（自动扫描 + 人工复核） | — | 2026-08-31 |
| 审批人 | （发版前由 GOVERNANCE 指定维护者签署） | — | — |
