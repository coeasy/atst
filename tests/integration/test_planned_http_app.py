from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tstdx.batch import BatchResult
from tstdx.domain.models import Quote
from tstdx.error_envelope import ErrorEnvelope
from tstdx.integration import http_app


class FakeApp:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.shutdown_handlers: list[Any] = []
        self.routes: dict[tuple[str, str], Any] = {}
        self.middlewares: list[tuple[str, Any]] = []
        self.exception_handlers: dict[type, Any] = {}

    def on_event(self, name: str):
        assert name == "shutdown"

        def register(fn):  # noqa: ANN001,ANN202
            self.shutdown_handlers.append(fn)
            return fn

        return register

    def get(self, path: str, **_: Any):
        def register(fn):  # noqa: ANN001,ANN202
            self.routes[("GET", path)] = fn
            return fn

        return register

    def middleware(self, kind: str):
        def register(fn):  # noqa: ANN001,ANN202
            self.middlewares.append((kind, fn))
            return fn

        return register

    def exception_handler(self, exc_type: type):
        def register(fn):  # noqa: ANN001,ANN202
            self.exception_handlers[exc_type] = fn
            return fn

        return register


def _install_fake_legacy_app(monkeypatch):  # noqa: ANN001,ANN202
    def fake_create_app(client):  # noqa: ANN001,ANN202
        app = FakeApp()
        app.state.client = client
        app.state.tasks = http_app._routes.TaskStore(max_tasks=4)
        return app

    monkeypatch.setattr(http_app._routes, "create_app", fake_create_app)


def test_official_factory_injects_planned_client_and_taskstore(monkeypatch) -> None:  # noqa: ANN001
    legacy_store = http_app._routes.TaskStore
    seen: dict[str, Any] = {}

    def fake_create_app(client):  # noqa: ANN001,ANN202
        seen["client"] = client
        seen["task_store_class"] = http_app._routes.TaskStore
        app = FakeApp()
        app.state.client = client
        app.state.tasks = http_app._routes.TaskStore(max_tasks=4)
        return app

    monkeypatch.setattr(http_app._routes, "create_app", fake_create_app)
    app = http_app.create_app()
    try:
        assert isinstance(seen["client"], http_app.PlannedProviderHttpClient)
        assert seen["task_store_class"] is http_app.PlannedTaskStore
        assert http_app._routes.TaskStore is legacy_store
        assert app.state.runtime == "planned-v12"
        assert ("GET", "/providers/{provider}/quotes/batch") in app.routes
    finally:
        app.state.tasks.close()
        seen["client"].close()


def test_f10_compatibility_methods_use_f10_channel_only() -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class F10:
        def download(self, *args: Any, **kwargs: Any) -> bytes:
            calls.append(("download", args))
            return b"ok"

        def parse_text(self, *args: Any, **kwargs: Any) -> str:
            calls.append(("parse_text", args))
            return "parsed"

    class Service:
        def __init__(self) -> None:
            self.tdx = SimpleNamespace(f10=F10())

        def close(self) -> None:
            pass

    client = http_app.PlannedProviderHttpClient(service_factory=Service)
    assert client.file_download("sh600519", "600519.txt") == b"ok"
    assert client.parse_text(b"raw") == "parsed"
    assert calls == [
        ("download", ("sh600519", "600519.txt")),
        ("parse_text", (b"raw",)),
    ]
    client.close()


def test_planned_taskstore_cancelled_running_work_remains_active() -> None:
    import threading
    import time

    store = http_app.PlannedTaskStore(max_tasks=1, max_workers=1)
    entered = threading.Event()
    release = threading.Event()

    def work() -> str:
        entered.set()
        release.wait(timeout=2.0)
        return "done"

    task_id = store.submit(work)
    assert entered.wait(timeout=1.0)
    assert store.cancel(task_id) is True
    assert store.active_count == 1

    try:
        store.submit(lambda: "second")
    except http_app._routes.TaskStoreFull:
        pass
    else:
        raise AssertionError("cancel_requested running task must still consume capacity")

    release.set()
    deadline = time.time() + 1.0
    while store.active_count and time.time() < deadline:
        time.sleep(0.01)
    assert store.active_count == 0
    store.close()


def test_provider_registry_exposes_capability_specific_batch_limits(monkeypatch) -> None:  # noqa: ANN001
    _install_fake_legacy_app(monkeypatch)

    class FakeService:
        def close(self) -> None:
            pass

    monkeypatch.setattr(http_app, "UnifiedMarketDataService", FakeService)
    client = SimpleNamespace(service=FakeService(), close=lambda: None)
    app = http_app.create_app(client)
    try:
        payload = app.routes[("GET", "/providers/{provider}")]("tdx")
        quotation = next(
            channel for channel in payload["channels"] if channel["id"] == "quotation"
        )
        assert quotation["batch_limits"] == {"quotes": 60}
        assert "batch_limit" not in quotation
    finally:
        app.state.tasks.close()


def test_partial_provider_quotes_endpoint_returns_batch_result_json(monkeypatch) -> None:  # noqa: ANN001
    _install_fake_legacy_app(monkeypatch)

    class FakeService:
        def quotes_batch(self, symbols, *, provider: str, deadline_ms: int):  # noqa: ANN001,ANN201
            assert list(symbols) == ["sh600519", "sz000001"]
            assert provider == "tdx"
            assert deadline_ms == 2500
            return BatchResult(
                items=(Quote(code="sh600519", price=10.0),),
                errors={
                    "sz000001": ErrorEnvelope(
                        code="E7050",
                        type="SourceUnavailable",
                        message="missing",
                        phase="normalize",
                        capability="quotes",
                        provider="tdx",
                        channel="quotation",
                    )
                },
                requested=("sh600519", "sz000001"),
                partial=True,
                meta={"provider": "tdx", "channel": "quotation"},
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(http_app, "UnifiedMarketDataService", FakeService)
    client = SimpleNamespace(service=FakeService(), close=lambda: None)
    app = http_app.create_app(client)
    try:
        payload = app.routes[("GET", "/providers/{provider}/quotes/batch")](
            "tdx", "sh600519,sz000001", 2500
        )
        assert payload["partial"] is True
        assert payload["requested"] == ["sh600519", "sz000001"]
        assert payload["items"][0]["code"] == "sh600519"
        assert payload["errors"]["sz000001"]["code"] == "E7050"
        assert payload["meta"]["provider"] == "tdx"
    finally:
        app.state.tasks.close()
