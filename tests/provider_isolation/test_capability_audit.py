from tstdx.catalog.capability_audit import audit_capability_bindings


def test_capability_catalog_has_execution_surface() -> None:
    report = audit_capability_bindings()

    assert report.migrated_capabilities > 0
    assert report.executable_bindings > 0
