"""Write apps/web/.env.production from a stack's outputs before `pnpm build`.

Usage:
    python -m scripts.build_web_env --stack marketdata-dev \
        --region ap-southeast-1 [--profile marketdata-dev]
"""

import argparse
from pathlib import Path

import boto3

ENV_FILE = Path(__file__).resolve().parents[1] / "apps/web/.env.production"


def render_env(outputs, region, schedule_state="DISABLED"):
    lines = {
        "VITE_API_URL": outputs["HttpApiEndpoint"],
        "VITE_AWS_REGION": region,
        "VITE_COGNITO_USER_POOL_ID": outputs["UserPoolId"],
        "VITE_COGNITO_CLIENT_ID": outputs["UserPoolClientId"],
        # Vite also loads .env.local; these stop its mock settings reaching a deployed build.
        "VITE_ENABLE_MOCK_DATA": "false",
        "VITE_ENABLE_MOCK_USER": "false",
        # The 08:00 report holds the daily lock, so a manual trigger would only be refused.
        "VITE_REPORT_SCHEDULE": "ENABLED" if schedule_state == "ENABLED" else "DISABLED",
    }
    return "".join(f"{name}={value}\n" for name, value in lines.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    parameters = {item["ParameterKey"]: item.get("ParameterValue") for item in stack.get("Parameters", [])}
    env = render_env(outputs, args.region, parameters.get("ScheduleState", "DISABLED"))
    ENV_FILE.write_text(env, encoding="utf-8", newline="\n")
    print(f"Wrote {ENV_FILE}")


if __name__ == "__main__":
    main()
