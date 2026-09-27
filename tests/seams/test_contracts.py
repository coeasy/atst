# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""跨域契约缝钉（编排者门禁）。

七个修复域并行推进，域间互相依赖的**契约缝**是最易被无意破坏而各域测试
全绿的部位。本文件把关键缝固化为断言：任何一侧改形态，集成阶段立刻红
且报错信息直指契约。均为廉价静态/签名检查，不触发网络与协议 IO。
"""

from __future__ import annotations

import inspect


def test_seam_client_bars_accepts_index_ctx() -> None:
    """S↔P 缝：TdxClient.bars 必须暴露 index 关键字并透传 dispatch ctx。"""
    from atst.client import TdxClient

    sig = inspect.signature(TdxClient.bars)
    assert "index" in sig.parameters, (
        "TdxClient.bars 缺 index 参数（S 域任务 4：P1a 指数 K 线 ctx 透传）"
    )
    p = sig.parameters["index"]
    assert p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    assert p.default is False, "bars(index=) 默认必须 False（普通 K 线语义不变）"


def test_seam_symbol_split_shape_stable() -> None:
    """S/G↔G 缝：domain.symbol.split_symbol 返回形态是流式归一化的依赖底座。"""
    from atst.domain.symbol import split_symbol

    out = split_symbol("sh600519")
    assert isinstance(out, tuple) and len(out) == 2, (
        f"split_symbol 返回形态漂移（现 {out!r}），流式/push 消费点将失配"
    )
    market, code = out
    assert str(market).lower() in {"sh", "sz", "bj"}, f"市场位形态漂移: {out!r}"
    assert code == "600519"
    # 中证指数裸码裁决（G 任务 1 / §2-3）：协议层市场号 000300→沪(1)、000001→深(0)
    from atst.domain.symbol import to_tdx_market

    assert to_tdx_market("000300")[0] == 1, "000300 中证指数仍被误判深市（§2-3）"
    assert to_tdx_market("000001")[0] == 0, "000001 平安银行必须仍归深市"


def test_seam_metrics_public_api_intact() -> None:
    """T↔V 缝：observability 导出的 metrics 单例具备 T 埋点依赖的公共方法。"""
    from atst.observability import metrics

    for name in ("record_reconnect", "record_request", "record_parse"):
        assert callable(getattr(metrics, name, None)), (
            f"metrics 单例缺 {name}()（T 埋点依赖，公共 API 不得移除）"
        )


def test_seam_pool_uses_connection_lock_or_busy_removed() -> None:
    """C2 缝：Slot.busy 死字段不得作为『假实现』存活（T 已删除，注释提及豁免）。"""
    import threading

    import atst.transport.pool as pool_mod

    slot = getattr(pool_mod, "Slot", None)
    fields = set(getattr(slot, "__dataclass_fields__", {}) or {})
    assert "busy" not in fields, (
        "Slot.busy 死字段仍在（C2 裁决：落实借还协议或删除字段，禁止第三态）"
    )
    # 连接级租约锁应作为实例属性在构造时建立。此处按**行为**断言而非源码
    # 文本：构造实现已内联到 transport.base 的 TcpConnection 中，源码检索
    # 随实现形态漂移而误红，实例属性检查才是稳定的跨实现契约。
    from atst.transport.base import TcpConnection

    conn = TcpConnection("127.0.0.1", 7709)
    lock = getattr(conn, "_lock", None)
    assert isinstance(lock, type(threading.RLock())), (
        "C2 连接级租约锁未在 TcpConnection 落地（应为可重入 RLock 实例）"
    )
    # 可重入性：request/ping 持锁期间会经 connect/read_frame 再次加锁，
    # 同线程重复获取必须成功，否则首帧请求即自锁。
    assert lock.acquire(blocking=False) is True
    try:
        assert lock.acquire(blocking=False) is True
        lock.release()
    finally:
        lock.release()


def test_seam_async_client_method_parity() -> None:
    """A↔F0 缝：AsyncClient 协程方法集必须在同步 Client 上存在（防重命名漂移）。"""
    from atst.client.api import AsyncClient, Client

    bridged = {
        n
        for n, m in vars(AsyncClient).items()
        if inspect.iscoroutinefunction(m) and not n.startswith("_")
    }
    assert bridged, "AsyncClient 协程方法集为空（结构漂移）"
    for name in bridged - {"aclose"}:
        assert hasattr(Client, name), f"异步方法 {name} 找不到同步 Client 入口（F0-1 契约破坏）"


def test_seam_web_source_encoding_hook() -> None:
    """W 缝：BaseWebSource 声明按源编码（W10），且默认 gbk 向后兼容。"""
    from atst.web.base import BaseWebSource

    assert getattr(BaseWebSource, "encoding", None) == "gbk", (
        "BaseWebSource.encoding 类属性缺失或非默认 gbk（W10 契约）"
    )


def test_seam_ratelimit_process_bucket() -> None:
    """W5 缝：跨实例共享限流桶（进程级按源名注册）。"""
    import atst.web._base_http as web_http

    src = inspect.getsource(web_http)
    # 结构性弱断言：模块级注册表存在（实现命名不限；P10-3 自 base.py 拆至 _base_http）
    assert "_BUCKET" in src or "_RATE_REGISTRY" in src or "_LIMITERS" in src, (
        "未发现进程级限流桶注册表（W5：跨实例共享）"
    )
