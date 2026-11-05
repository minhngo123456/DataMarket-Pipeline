"""Source enablement accepts JSON booleans without truthiness coercion."""

import pytest

from scripts.demo_source_pipeline import local_pipeline

INVALID_ENABLED = ["false", 0, 1, 0.0, None, [], {}]
INVALID_IDS = ["string", "zero", "one", "float", "null", "array", "object"]


@pytest.mark.parametrize("value", INVALID_ENABLED, ids=INVALID_IDS)
@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_non_boolean_enabled_cannot_change_an_existing_source(method, value):
    with local_pipeline() as pipeline:
        source = {
            "source_id": "enabled-validation", "kind": "api", "enabled": False,
            "endpoint": "https://enabled-validation.example.invalid/prices",
            "name": "Existing source",
        }
        assert pipeline.api("POST", "/sources", body=source)[0] == 201
        previous = pipeline.store.get("tenant-a", "SOURCE#enabled-validation")
        path = "/sources" if method == "POST" else "/sources/enabled-validation"

        status, response = pipeline.api(method, path, body={
            **source, "enabled": value, "name": "Must not be saved",
        })

        assert status == 400
        assert response["error"]["code"] == "invalid_source_payload"
        assert response["error"]["message"] == "enabled must be a boolean"
        assert pipeline.store.get("tenant-a", "SOURCE#enabled-validation") == previous
        assert list(pipeline.store.sources("tenant-b")) == []
        assert pipeline.dispatch() == {"sent": 0}


@pytest.mark.parametrize("value", INVALID_ENABLED, ids=INVALID_IDS)
def test_non_boolean_enabled_cannot_create_a_source(value):
    with local_pipeline() as pipeline:
        previous_meta = pipeline.store.get("tenant-a", "META")

        status, _ = pipeline.api("POST", "/sources", body={
            "source_id": "enabled-validation", "kind": "api", "enabled": value,
            "endpoint": "https://enabled-validation.example.invalid/prices",
        })

        assert status == 400
        assert pipeline.store.get("tenant-a", "SOURCE#enabled-validation") is None
        assert pipeline.store.get("tenant-a", "META") == previous_meta


@pytest.mark.parametrize("enabled", [False, True])
def test_boolean_enabled_is_saved_without_changing_omitted_patch_state(enabled):
    with local_pipeline() as pipeline:
        status, created = pipeline.api("POST", "/sources", body={
            "source_id": "enabled-validation", "kind": "api",
            "endpoint": "https://enabled-validation.example.invalid/prices",
        })
        assert status == 201
        assert created["enabled"] is True
        status, updated = pipeline.api("PATCH", "/sources/enabled-validation", body={
            "enabled": enabled,
        })
        assert status == 200
        assert updated["enabled"] is enabled

        status, renamed = pipeline.api("PATCH", "/sources/enabled-validation", body={
            "name": "Renamed without changing enablement",
        })

        assert status == 200
        assert renamed["enabled"] is enabled
        assert pipeline.store.get("tenant-a", "SOURCE#enabled-validation")["enabled"] is enabled
