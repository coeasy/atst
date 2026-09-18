from tstdx.catalog.provider_audit import audit_provider_registry


def test_provider_registry_audit_has_bindings() -> None:
    report = audit_provider_registry()

    assert report.providers > 0
    assert report.bindings > 0
