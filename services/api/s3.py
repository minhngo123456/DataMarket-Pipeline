"""S3 client for presigned URLs the browser calls directly."""

import os

import boto3
from botocore.config import Config


def presign_client():
    """Regional SigV4 client.

    The default client signs for the global s3.amazonaws.com endpoint; S3 answers a
    freshly created bucket there with a 307 redirect, which browsers refuse on a CORS
    upload. Pinning the region makes the URL point at the bucket's regional endpoint.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
