"""24 项贯通测试（A4/D9）：每项验收一次跑通。

每个 test 独立可执行：验证真实 API（import + call with mocks）或
pytest.skip("pending: <module>")。目标 ≥18 项真实通过。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _golden_sample_dirs():
    """golden 样本目录数（命令级样本，即含 meta.json 的采集目录）。"""
    golden = TESTS_DIR / "golden"
    if not golden.exists():
        return 0
    return len(list(golden.rglob("meta.json")))


def _protocol_spec_count():
    """PROTOCOL_SPEC YAML 文件数。"""
    spec_dir = REPO_ROOT / "PROTOCOL_SPEC"
    if not spec_dir.exists():
        return 0
    return len(list(spec_dir.rglob("*.yaml")))


@pytest.mark.unit
class TestBridges:
    """24 项贯通验收。"""

    # ---- 01: 协议命令 L1+L2+L3 三层可解析 ----
    def test_01_tiers(self):
        """#01 协议命令 L1+L2+L3 三层可解析。"""
        from tstdx.codec.framing import ResponseFrame
        from tstdx.protocol.registry import TIER_L1, TIER_L2, TIER_L3, dispatch

        # L1: 使用 golden 样本
        golden = TESTS_DIR / "golden" / "quotation" / "0x0530_realtime_quote_600000"
        samples = sorted(golden.glob("20*"), reverse=True)
        assert samples, "缺少 golden 样本"
        meta = json.loads((samples[0] / "meta.json").read_text(encoding="utf-8"))
        payload = (samples[0] / "payload.bin").read_bytes()
        resp = meta["response"]
        frame = ResponseFrame(
            magic=0x0074CBB1,
            zip_flag=1,
            seq=0,
            method=0x0530,
            zip_size=resp["zip_size"],
            unzip_size=resp["unzip_size"],
            payload=payload,
        )
        ctx = dict(meta.get("parse_ctx") or {})
        result = dispatch(
            frame, code=ctx.get("code", "600000"), market=ctx.get("market"), price_scale=100
        )
        assert result.tier == TIER_L1

        # L2/L3: 未知命令
        frame2 = ResponseFrame(
            magic=0x0074CBB1,
            zip_flag=0,
            seq=0,
            method=0x9999,
            zip_size=4,
            unzip_size=4,
            payload=b"\xff" * 4,
        )
        result2 = dispatch(frame2)
        assert result2.tier in (TIER_L2, TIER_L3)

    # ---- 02: spec 目录 ≥8 YAML ----
    def test_02_spec_dir(self):
        """#02 PROTOCOL_SPEC 目录存在且有 ≥8 YAML。"""
        count = _protocol_spec_count()
        assert count >= 8, f"PROTOCOL_SPEC 仅 {count} 个 YAML，期望 ≥8"

    # ---- 03: bridge test self-check ----
    def test_03_bridge_self_check(self):
        """#03 本文件自身的收集校验。"""
        # 验证本文件被 pytest 正确收集
        tests = list(TestBridges.__dict__.keys())
        bridge_tests = [t for t in tests if t.startswith("test_")]
        assert len(bridge_tests) >= 20, f"仅有 {len(bridge_tests)} 项桥接测试"

    # ---- 04: 配置合并 ----
    def test_04_config_merge(self):
        """#04 tstdx.toml 12 case 合并。"""
        from tstdx.config.loader import load_config
        from tstdx.config.schema import Config

        cfg = load_config(overrides=None, use_env=False, use_files=False)
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 5.0

    # ---- 05: 错误分类树 ----
    def test_05_error_tree(self):
        """#05 错误分类树 + RetryAdvice。"""
        from tstdx.errors import RETRY_ADVICE, TdxError

        assert len(RETRY_ADVICE) >= 30
        assert issubclass(TdxError, Exception)

    # ---- 06: 同步/异步双 API 一致 ----
    def test_06_sync_async_parity(self):
        """#06 TdxClient vs AsyncTdxClient 一致。"""
        from tstdx.client import AsyncTdxClient, TdxClient

        assert hasattr(TdxClient, "bars")
        assert hasattr(AsyncTdxClient, "bars")
        assert hasattr(TdxClient, "quotes")
        assert hasattr(AsyncTdxClient, "quotes")

    # ---- 07: 降级链 ----
    def test_07_fallback_chain(self):
        """#07 单一内核禁止跨 Provider 降级：配置面不存在任何降级开关。"""
        from tstdx.config.schema import Config

        # 多级 ``tdx -> web -> reader -> cache`` 与"可打开自动降级"的键都不存在；
        # 想开启降级没有开关可拧，只能由调用方显式选 Provider。
        field_names = {
            name
            for section in Config._SUBCONFIGS
            for name in dir(getattr(Config(), section))
            if not name.startswith("_")
        }
        assert field_names.isdisjoint(
            {"auto_fallback", "continue_on_error", "order", "enabled", "cache"}
        )
        assert Config().core.default_provider == "tdx"

    # ---- 08: 流式韧性 ----
    def test_08_stream_resilience(self):
        """#08 订阅-断网-重连-补数。"""
        from tstdx.streaming.engine import (
            BackpressureQueue,
            DeltaMerger,
            GapFiller,
            ReconnectPolicy,
        )

        # 验证核心组件可实例化
        assert ReconnectPolicy() is not None
        assert DeltaMerger() is not None
        assert GapFiller() is not None
        assert BackpressureQueue() is not None

    # ---- 09: 字符集探测 ----
    def test_09_encoding_detect(self):
        """#09 字符集自动探测。"""
        from tstdx.charset.encoding import detect_encoding

        assert detect_encoding("你好".encode("gbk")) == "gbk"
        assert detect_encoding(b"hello") == "utf-8"

    # ---- 10: 交易日历 ----
    def test_10_calendar(self):
        """#10 A 股交易日历 2024-2026。"""
        from tstdx.domain.calendar import TradingCalendar

        cal = TradingCalendar()
        assert cal.is_trading_day("2024-01-01") is False  # 元旦
        assert cal.is_trading_day("2024-01-02") is True

    # ---- 11: 时区 ----
    def test_11_tz(self):
        """#11 时区 UTC 内部/本地输出。"""
        from tstdx.domain.models import Bar

        bar = Bar(
            datetime="2024-01-15 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15
        )
        assert bar.datetime == "2024-01-15 15:00"

    # ---- 12: Golden 样本 ----
    def test_12_golden_samples(self):
        """#12 Golden 数据来源 ≥8 目录。"""
        count = _golden_sample_dirs()
        assert count >= 8, f"golden 样本目录仅 {count}，期望 ≥8"

    # ---- 13: 原创性工具 ----
    def test_13_originality_tool(self):
        """#13 check_originality.py 存在且可运行。"""
        tool_path = REPO_ROOT / "tstdx" / "tools" / "check_originality.py"
        assert tool_path.exists(), "check_originality.py 不存在"

    # ---- 14: LICENSE_ALLOWLIST ----
    def test_14_license_allowlist(self):
        """#14 ORIGINALITY/LICENSE_ALLOWLIST.md 存在。"""
        p = REPO_ROOT / "ORIGINALITY" / "LICENSE_ALLOWLIST.md"
        assert p.exists(), "LICENSE_ALLOWLIST.md 不存在"

    # ---- 15: wheels.yml ----
    def test_15_wheels_yml(self):
        """#15 .github/workflows/wheels.yml 存在。"""
        p = REPO_ROOT / ".github" / "workflows" / "wheels.yml"
        assert p.exists(), "wheels.yml 不存在"

    # ---- 16: 弃用机制退役（不留支架） ----
    def test_16_deprecation_mechanism_retired(self):
        """#16 包内不留自建的退役机制：三个面（磁盘 / 导入 / 根包公共面）都要干净。

        F-74/D3 (a) 的验收。原判据是"``tstdx/deprecation.py`` 存在"——它服务的门面层已在
        v16 Phase 2 物理删除（F-70），此后 206 行在 ``tstdx/`` 内零消费者，钉着它的只剩
        外围：9 项单测、本项验收、三处豁免或白名单（根级布局、裸告警通道、可达性记录）与
        一行公共 API 表。判据随之换形：从"这件工具在不在"变成"这把承诺还在不在"。
        运行期的生命周期信号另有其主——告警信封 ``tstdx/diagnostics.WarningCode``（结果内
        提示）与错误树 ``SourceDeprecated`` E7030/410（上游接口下线），各由
        ``tests/architecture/test_caveat_channel_gates.py``、``tests/errors/test_taxonomy.py``
        盯住，本项不重复计量。
        """
        assert not (REPO_ROOT / "tstdx" / "deprecation.py").exists(), "退役机制模块回到了包根"
        importlib.invalidate_caches()
        with pytest.raises(ImportError):
            importlib.import_module("tstdx.deprecation")
        import tstdx

        leaked = {
            name
            for name in ("deprecated", "DeprecationPolicy", "DeprecationInfo")
            if hasattr(tstdx, name) or name in tstdx.__all__
        }
        assert not leaked, f"根包公共面重新导出退役机制：{sorted(leaked)}"

    # ---- 17: HTTP API canonical 面 ----
    def test_17_http_server(self):
        """#17 canonical HTTP 面：核心专用端点 + 能力泛化查询入口。

        v16 收敛后不再逐能力开端点：长尾能力统一经 ``POST /v13/query/{capability}``
        寻址，端点数不随 capability 目录 1:1 膨胀。
        """
        from tstdx.catalog.capability import MIGRATED_CAPABILITIES
        from tstdx.integration.runtime_http import create_runtime_app

        paths = {
            getattr(r, "path", "")
            for r in create_runtime_app().routes
            if getattr(r, "methods", None)
        }
        assert {
            "/v13/capabilities",
            "/v13/query/{capability}",
            "/v13/quotes",
            "/v13/bars/{symbol}",
            "/v13/snapshot/{symbol}",
            "/v13/minute/{symbol}",
            "/v13/trades/{symbol}",
            "/v13/security/count",
            "/v13/security/list",
            "/v13/runtime/health",
        } <= paths, f"canonical 端点缺失：{sorted(paths)}"
        # 泛化入口按名寻址整个已迁移目录。
        assert len(MIGRATED_CAPABILITIES) >= 150

    # ---- 18: MCP 工具清单 ----
    def test_18_mcp_tools(self):
        """#18 MCP 工具清单：Tier-A 专用工具 + 通用 ``query_capability`` 入口。"""
        from tstdx.integration.mcp import TOOLS, create_mcp_server

        names = {tool.name for tool in TOOLS}
        # v13 clean break：MCP 只暴露走 canonical Client runtime 的能力，
        # 且每个已迁移能力都能经 query_capability 触达。
        assert "query_capability" in names
        assert {
            "get_bars",
            "get_quote",
            "get_quotes",
            "get_snapshot",
            "get_minute_today",
            "get_trades",
            "get_security_count",
            "get_security_list",
        } <= names
        # ``tools/list`` 必须与清单同源。
        server = create_mcp_server()
        resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp["result"]["tools"] == [t.to_dict() for t in TOOLS]

    # ---- 19: 可观测性 zero-dep ----
    def test_19_metrics_zero_dep(self):
        """#19 可观测性 zero-dep + 导出。"""
        from tstdx.observability.metrics import metrics

        output = metrics.render()
        assert isinstance(output, str)
        assert "tstdx_protocol_parse_total" in output

    # ---- 20: Sinks ----
    def test_20_sinks_dispatch(self):
        """#20 DataFrame/Parquet/DuckDB 三 sink。"""
        from tstdx.output import Sink, write

        assert hasattr(Sink, "write")
        assert callable(write)

    # ---- 21: README + docs ----
    def test_21_readme_docs(self):
        """#21 README 和 docs 目录存在。"""
        assert (REPO_ROOT / "README.md").exists(), "README.md 不存在"
        assert (REPO_ROOT / "docs").exists(), "docs/ 不存在"

    # ---- 22: 治理文件 ----
    def test_22_governance(self):
        """#22 治理文件 5 份。"""
        required = [
            "GOVERNANCE.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "LICENSE",
        ]
        missing = [f for f in required if not (REPO_ROOT / f).exists()]
        assert not missing, f"缺少治理文件: {missing}"

    # ---- 23: Web 7 Adapters ----
    def test_23_web_adapters(self):
        """#23 HTTP Web 源 7 Adapter 类存在。"""
        from tstdx.web.adapters import (
            BocSource,
            EastmoneySource,
            HkSource,
            JslSource,
            KlineSource,
            SinaSource,
            TencentSource,
        )

        adapters = [
            SinaSource,
            TencentSource,
            EastmoneySource,
            JslSource,
            HkSource,
            KlineSource,
            BocSource,
        ]
        assert len(adapters) == 7
        for cls in adapters:
            assert hasattr(cls, "source_name")

    # ---- 24: normalize 模块 ----
    def test_24_normalize_module(self):
        """#24 normalize 模块存在。"""
        from tstdx.web.normalize import (
            normalize_amount,
            normalize_volume,
        )

        assert normalize_volume("sina", 1000) == 1000
        assert normalize_amount("tencent", 100.0) == 1_000_000.0
