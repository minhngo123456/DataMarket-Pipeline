import pytest

from scripts.build_web_env import render_env

OUTPUTS = {
    "HttpApiEndpoint": "https://api.example.com",
    "UserPoolId": "ap-southeast-1_pool",
    "UserPoolClientId": "client",
}


def test_render_env_uses_stack_outputs_and_disables_mocks():
    assert render_env(OUTPUTS, "ap-southeast-1").splitlines() == [
        "VITE_API_URL=https://api.example.com",
        "VITE_AWS_REGION=ap-southeast-1",
        "VITE_COGNITO_USER_POOL_ID=ap-southeast-1_pool",
        "VITE_COGNITO_CLIENT_ID=client",
        "VITE_ENABLE_MOCK_DATA=false",
        "VITE_ENABLE_MOCK_USER=false",
        "VITE_REPORT_SCHEDULE=DISABLED",
    ]


@pytest.mark.parametrize(("state", "flag"), [("ENABLED", "ENABLED"), ("DISABLED", "DISABLED"), ("bogus", "DISABLED")])
def test_render_env_passes_the_report_schedule_to_the_web(state, flag):
    assert f"VITE_REPORT_SCHEDULE={flag}" in render_env(OUTPUTS, "ap-southeast-1", state).splitlines()


def test_render_env_fails_when_an_output_is_missing():
    with pytest.raises(KeyError):
        render_env({"HttpApiEndpoint": "https://api.example.com"}, "ap-southeast-1")
