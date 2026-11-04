"""Safe defaults and evidence output for the opt-in live API smoke script."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import smoke_api_aws


def test_api_smoke_requires_write_flag_before_aws_session(monkeypatch):
    session = Mock(side_effect=AssertionError("must not call AWS"))
    monkeypatch.setattr(smoke_api_aws, "new_session", session)
    with pytest.raises(SystemExit) as error:
        smoke_api_aws.main([])
    assert error.value.code == 2
    session.assert_not_called()


def test_node_receives_credentials_through_stdin_but_evidence_excludes_them():
    payload = {"users": {"admin_a": {"password": "never-log-this"}}}
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "status": "passed", "mode": "real_cognito_srp_and_http_api", "password": "never-log-this",
        "token": "secret-token", "checks": [{"name": f"isolation-{i}", "passed": True,
            "actual_status": 403, "expected_status": 403, "token": "secret-token"} for i in range(15)],
    })))
    result = smoke_api_aws.node_check("node", payload, runner=run)
    assert "never-log-this" in run.call_args.kwargs["input"]
    assert "never-log-this" not in str(run.call_args.args)
    assert "secret-token" not in json.dumps(result)
    assert "never-log-this" not in json.dumps(result)


def test_node_parse_error_does_not_include_raw_subprocess_output():
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout="sensitive-output"))
    with pytest.raises(RuntimeError, match="did_not_return_json") as error:
        smoke_api_aws.node_check("node", {}, runner=run)
    assert "sensitive-output" not in str(error.value)


def test_node_cannot_claim_success_without_recorded_checks():
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"status":"passed","checks":[]}'))
    with pytest.raises(RuntimeError, match="checks_missing"):
        smoke_api_aws.node_check("node", {}, runner=run)


def test_cleanup_touches_only_owned_tenants_and_users(store):
    store.put("tenant-a", "META", {"status": "active"})
    cognito = Mock()
    result = smoke_api_aws.cleanup_fixtures(store, cognito, "pool", {
        "tenants": ["tenant-a"], "users": [{"username": "own@example.invalid", "tenant_id": "tenant-a"}],
    })
    assert result["status"] == "completed"
    assert store.get("tenant-a", "META") is None
    assert store.get("tenant-b", "META") is not None
    cognito.admin_delete_user.assert_called_once_with(UserPoolId="pool", Username="own@example.invalid")
