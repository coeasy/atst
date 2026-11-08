# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""交易登录口令混淆（P2-1 · 洁净室占位实现）。

**状态说明（务必阅读）**
------------------------
真实券商侧的登录口令加密算法封装于闭源 ``trade.dll``，且公开资料高度
不可靠（大量伪造/HMAC/JWT 类说法）。因此本模块提供的是**自洽、可逆、
确定性的占位混淆**，用于验证协议帧能正确携带非明文口令，并让模拟器完成
全链路回路。它**不是**真实通达信交易服务器的口令算法 —— 真机样本定标
后应整体替换本模块的实现。

设计
----
口令以 ``utf-8`` 编码后与固定密钥流逐字节 XOR（密钥循环复用）。
密钥流与实现绑定，保证同一明文 → 同一密文（可复现、可单测）。
"""

from __future__ import annotations

__all__ = [
    "OBFUSCATION_KEY",
    "obfuscate_password",
    "deobfuscate_password",
]

#: 固定密钥流（占位；真实算法待真机定标后替换）。
OBFUSCATION_KEY = b"atst-trade-infer"


def obfuscate_password(plain: str) -> bytes:
    """把明文口令混淆为字节（确定性、可逆）。

    >>> obfuscate_password("123456") == obfuscate_password("123456")
    True
    >>> obfuscate_password("123456") != b"123456"
    True
    """
    data = plain.encode("utf-8")
    key = OBFUSCATION_KEY
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def deobfuscate_password(obfuscated: bytes) -> str:
    """把混淆字节还原为明文口令（:func:`obfuscate_password` 的逆变换）。"""
    key = OBFUSCATION_KEY
    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(obfuscated))
    return raw.decode("utf-8")
