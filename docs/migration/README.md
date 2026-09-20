# 迁移指南

从其他 TDX / 行情库迁移到 tstdx：

1. [从 easyquotation 迁移](easyquotation.md) — HTTP Web 实时行情
2. [从 mootdx 迁移](mootdx.md) — TDX 协议客户端
3. [从 easy_tdx / eltdx 迁移](easy_tdx.md) — 轻量 TDX 封装

## 迁移总览

| 原库 | 建议目标 API | 对照表 |
|---|---|---|
| easyquotation | `Client` 的 `quotes`（唯一业务入口）/ `tstdx.web.WebQuoteClient` / `get_quotes` | frequency/category → period |
| mootdx | `TdxClient` / `AsyncTdxClient` / `tstdx.reader.formats` | frequency → period |
| easy_tdx | `TdxClient` / `AsyncTdxClient` | category → period |
| eltdx | `TdxClient` / `AsyncTdxClient` | frequency → period |

> **垫片状态**：v1.0 时代提供的 `tstdx.compat.*` / `tstdx.web.easyquotation` 兼容垫片已在
> v1.2.0 清理批次中移除（与已删除的 `_async_bridge` 同类归并）。各迁移文档的
> 「垫片」小节已改写为原生 API 指引，对照表保留。
