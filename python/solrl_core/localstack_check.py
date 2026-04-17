from __future__ import annotations

import os
from pathlib import Path

import boto3


def main() -> int:
    endpoint = os.environ.get("LOCALSTACK_ENDPOINT", "http://localstack:4566")
    bucket = os.environ.get("SOLRL_ARTIFACT_BUCKET", "solrl-artifacts-local")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)
    body = b"solrl-localstack-artifact\n"
    key = "artifacts/localstack-check.txt"
    client.put_object(Bucket=bucket, Key=key, Body=body, Metadata={"protocol": "solrl"})
    out = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if out != body:
        raise RuntimeError("LocalStack S3 round-trip mismatch")
    Path("artifacts/logs").mkdir(parents=True, exist_ok=True)
    Path("artifacts/logs/localstack-check.txt").write_text(
        f"s3://{bucket}/{key}\n",
        encoding="utf-8",
    )
    print(f"LocalStack S3 OK: s3://{bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

