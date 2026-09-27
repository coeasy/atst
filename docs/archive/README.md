# 归档区：历史计划与历史语境

这里放的是**已经不作数的文档**：每一版重构/优化计划、每一次对标审计、每一份被后继取代的设计稿。
保留它们不是为了查阅，而是为了让代码里的出处指针（`atst/**` 的注释、`tests/**` 的口径说明）
仍然指得到当时写下的那份证据。

## 阅读规则

- **本目录下的任何文件都不是现行契约。** 想知道某个接口今天长什么样，读
  [../api/interfaces.md](../api/interfaces.md)；想知道架构今天是什么形状，读
  [../ARCHITECTURE.md](../ARCHITECTURE.md)。
- 归档文档以现在时叙述的内容（门面类名、方法计数、端点清单、批次编号）多半已经失效。
  新入档且确实会误导当代读者的，在文件顶部加"归档说明"标注；更早入档的按原文留存，不逐条订正。
- 计划文档记录的是**当时的判断**，不是结论。判断被推翻时不删旧文，另写新文并写明取代关系
  （`REFACTOR_PLAN_V17_CLOSURE.md` → `REFACTOR_PLAN_V18_RESTRUCTURE.md` 就是这个形状）。

## 门禁口径

归档区整体落在文档门禁的射程之外，因此这里出现的死路径、幻影类名、过期数字不会让 CI 变红：

| 判据 | 豁免位置 |
|---|---|
| 文档代码示例可执行性 | `tests/architecture/test_doc_code_consistency.py` 的 `EXCLUDED_PARTS` |
| 幻影错误类名 | `tests/architecture/test_error_promises.py` 的 `AUDIT_DOC_PREFIXES` |
| 围栏代码块真实可调用 | `tests/architecture/test_doc_code_examples.py` 复用同一份 `active_docs()` |

反向的判据**不**豁免归档区：代码注释里写出的 `docs/**.md` 出处必须真实存在
（`test_code_cites_only_docs_paths_that_exist`），所以把文档移进本目录时，要同步改掉指向它的代码注释。
