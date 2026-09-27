# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""登录口令混淆测试（P2-1）。

验证占位混淆的确定性 / 可逆性 / 非明文特性 —— 注意这是**洁净室占位实现**
（真实券商算法封装于闭源 ``trade.dll``），测试只承诺内部自洽。
"""

from __future__ import annotations

import pytest

from atst.trade.security import (
    OBFUSCATION_KEY,
    deobfuscate_password,
    obfuscate_password,
)

pytestmark = pytest.mark.unit


class TestPasswordObfuscation:
    def test_roundtrip(self) -> None:
        for plain in ("123456", "AbC@123", "pass", ""):
            assert deobfuscate_password(obfuscate_password(plain)) == plain

    def test_deterministic(self) -> None:
        assert obfuscate_password("123456") == obfuscate_password("123456")

    def test_not_plaintext(self) -> None:
        assert obfuscate_password("123456") != b"123456"

    def test_same_length(self) -> None:
        assert len(obfuscate_password("123456")) == len("123456")

    def test_key_nonempty(self) -> None:
        assert OBFUSCATION_KEY
