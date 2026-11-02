"""Upload apps/web/dist to an Amplify Hosting app using a manual deployment.

Run scripts.build_web_env and `pnpm build` first.

Usage:
    python -m scripts.deploy_web_amplify --app-id <appId> \
        --region ap-southeast-1 [--profile marketdata-dev] [--branch main]
"""

import argparse
import io
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3

DIST = Path(__file__).resolve().parents[1] / "apps/web/dist"
RUNNING = ("PENDING", "PROVISIONING", "RUNNING", "CANCELLING")


def zip_dist(dist):
    """Zip the folder contents with / separators and index.html at the root."""
    if not (dist / "index.html").is_file():
        raise FileNotFoundError(f"{dist / 'index.html'} not found; run pnpm build first")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(dist).as_posix())
    return buffer.getvalue()


def deploy(amplify, app_id, branch, data):
    deployment = amplify.create_deployment(appId=app_id, branchName=branch)
    request = urllib.request.Request(
        deployment["zipUploadUrl"], data=data, method="PUT", headers={"Content-Type": "application/zip"}
    )
    with urllib.request.urlopen(request, timeout=120):
        pass
    job_id = deployment["jobId"]
    amplify.start_deployment(appId=app_id, branchName=branch, jobId=job_id)
    while True:
        status = amplify.get_job(appId=app_id, branchName=branch, jobId=job_id)["job"]["summary"]["status"]
        if status not in RUNNING:
            return status
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    status = deploy(session.client("amplify"), args.app_id, args.branch, zip_dist(DIST))
    print(f"Deployment {status}: https://{args.branch}.{args.app_id}.amplifyapp.com")
    if status != "SUCCEED":
        sys.exit(1)


if __name__ == "__main__":
    main()
