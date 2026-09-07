# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P#1/P#8 回归：ProtocolSniffer 归档闭环接线 + 并发防覆盖。

旧实现零调用点——registry 模块头、generic 模块头与 errors.UnknownCommand
的恢复建议承诺的「未知/低置信样本自动归档」从未发生；且秒级时间戳直写
会静默覆盖同秒样本。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.protocol.generic import ProtocolSniffer, get_sniffer
from tstdx.protocol.registry import ParseResult, dispatch


def _frame(method: int, payload: bytes) -> ResponseFrame:
    return ResponseFrame(
        magic=0x5217A9F1,
        zip_flag=0,
        seq=1,
        method=method,
        zip_size=len(payload),
        unzip_size=len(payload),
        body=b"",
        payload=payload,
        header_raw=b"\x00" * 16,
    )


def _make_sniffer(tmp_path: Path, *, enabled: bool = True) -> ProtocolSniffer:
    return ProtocolSniffer(root=tmp_path / "_sniffer", enabled=enabled)


@pytest.mark.unit
class TestSnifferArchiveLoop:
    """归档闭环与防覆盖。"""

    def test_archive_writes_bin_meta_draft(self, tmp_path: Path) -> None:
        payload = b"\x02\x00" + b"\x01\x00\x00\x00" * 4
        sniffer = _make_sniffer(tmp_path)
        result = ParseResult(command=0x0600, name="probe", tier="L3", confidence=0.0)
        out = sniffer.archive(_frame(0x0601, payload), result, "STANDARD")
        assert out is not None and out.exists()
        family_dir = tmp_path / "_sniffer" / "STANDARD" / "0601"
        assert (family_dir / "DRAFT.yaml").exists()
        metas = list(family_dir.glob("*.meta.json"))
        assert len(metas) == 1
        assert (
            metas[0].with_suffix("").with_suffix(".bin").exists()
            or (family_dir / out.name).exists()
        )

    def test_same_second_samples_do_not_overwrite(self, tmp_path: Path) -> None:
        """P#8：同秒多条样本必须共存（序号后缀），禁止静默覆盖。"""
        sniffer = _make_sniffer(tmp_path)
        result = ParseResult(command=0x0600, name="probe", tier="L3", confidence=0.0)
        for i in range(3):
            out = sniffer.archive(_frame(0x0602, bytes([i]) * 32), result, "STANDARD")
            assert out is not None
        bins = list((tmp_path / "_sniffer" / "STANDARD" / "0602").glob("*.bin"))
        assert len(bins) == 3, f"3 条样本应共存，实际 {len(bins)}"

    def test_concurrent_archive_no_crosswrite(self, tmp_path: Path) -> None:
        """多线程并发归档：bin/meta 成对且无异常。"""
        sniffer = _make_sniffer(tmp_path)
        result = ParseResult(command=0x0600, name="probe", tier="L3", confidence=0.0)
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                sniffer.archive(_frame(0x0603, bytes([i]) * 32), result, "STANDARD")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        d = tmp_path / "_sniffer" / "STANDARD" / "0603"
        assert len(list(d.glob("*.bin"))) == 8
        assert len(list(d.glob("*.meta.json"))) == 8

    def test_disabled_sniffer_writes_nothing(self, tmp_path: Path) -> None:
        sniffer = _make_sniffer(tmp_path, enabled=False)
        result = ParseResult(command=0x0600, name="probe", tier="L3", confidence=0.0)
        assert sniffer.archive(_frame(0x0604, b"\x00" * 32), result, "STANDARD") is None
        assert not (tmp_path / "_sniffer").exists()


@pytest.mark.unit
class TestDispatchSnifferWiring:
    """P#1：dispatch 的 L2/L3 路径触发归档（接线回归）。"""

    def test_l3_dispatch_archives_sample(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无解析器命令走 L3 透传时，样本进入 sniffer。"""
        from tstdx.protocol import generic as generic_mod

        sniffer = _make_sniffer(tmp_path)
        monkeypatch.setattr(generic_mod, "_default_sniffer", sniffer)
        payload = b"\xff\xff" + b"\xaa" * 30  # 无法推断布局 → L3
        dispatch(_frame(0x0FA0, payload), family="STANDARD")
        assert (tmp_path / "_sniffer" / "STANDARD" / "0fa0").exists()

    def test_archive_failure_never_breaks_parse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """归档落盘失败（如只读目录）不影响解析结果。"""
        from tstdx.protocol import generic as generic_mod

        sniffer = _make_sniffer(tmp_path)

        def _boom(*a: object, **kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(generic_mod, "_default_sniffer", sniffer)
        monkeypatch.setattr(sniffer, "archive", _boom)
        payload = b"\xff\xff" + b"\xaa" * 30
        result = dispatch(_frame(0x0FA1, payload), family="STANDARD")
        assert result.tier in ("L2", "L3")  # 解析照常返回

    def test_global_sniffer_disabled_by_conftest(self) -> None:
        """conftest 隔离：默认 sniffer 在测试中被禁用（防仓库污染）。"""
        assert get_sniffer().enabled is False
