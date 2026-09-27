# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""全局测试夹具。

P#1 归档接线后的测试隔离：``ProtocolSniffer`` 已在 dispatch 的 L2/L3 路径
自动归档样本（默认 root 指向仓库内 ``PROTOCOL_SPEC/_sniffer/``）——测试大量
触发 L3 回落，若不隔离会向仓库写入垃圾样本。本夹具把默认 sniffer 禁用；
需要验证归档行为的测试自行构造独立 ``ProtocolSniffer(root=tmp_path)``。
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _disable_default_sniffer():
    """禁用全局 sniffer，防止 dispatch 测试向仓库目录写归档样本。"""
    from atst.protocol.generic import get_sniffer

    sniffer = get_sniffer()
    original = sniffer.enabled
    sniffer.enabled = False
    yield
    sniffer.enabled = original


@pytest.fixture
def seed_pool_health():
    """把进程内运行时健康写到池当前 generation 持有的 HostEntry 上。

    ``_pool_family_hardening`` 让「直接构造连接池」开启一段全新的运行时健康
    生命周期：池可以继承选择器身份与后台探测延迟，但绝不继承调用方传入的
    live RTT / 失败计数 / 熔断状态 / HALF_OPEN 探测令牌（见 commit
    ``c6616ea`` "fix: reset runtime health on new pool construction"）。

    因此凡是验证 ``update_hosts`` / bestip 溯源不变式的用例，都必须在**构造之后**
    把运行时健康「种」到池自己持有的 generation host 上——这正是池的请求 /
    心跳路径本来会做的事。本夹具就是这一动作的唯一入口。
    """

    def _seed(pool: Any, index: int = 0, **fields: Any) -> Any:
        host = pool._slots[index].host
        for name, value in fields.items():
            setattr(host, name, value)
        return host

    return _seed
