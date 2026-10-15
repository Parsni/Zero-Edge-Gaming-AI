"""Optional S3 archive for processed clips and their provenance receipts."""

import hashlib
import json
from pathlib import Path

from config import AWS_ARCHIVE_ENABLED, AWS_REGION, AWS_S3_BUCKET, AWS_S3_PREFIX, AWS_LAMBDA_FUNCTION


def archive_status() -> dict:
    configured = bool(AWS_ARCHIVE_ENABLED and AWS_S3_BUCKET)
    return {
        "enabled": AWS_ARCHIVE_ENABLED,
        "configured": configured,
        "provider": "Amazon S3" if configured else None,
        "lambda_hook_configured": bool(configured and AWS_LAMBDA_FUNCTION),
    }


def archive_artifacts(video_path: Path, receipt_path: Path) -> dict:
    """Upload a processed clip and receipt using the normal AWS credential chain."""
    if not AWS_ARCHIVE_ENABLED:
        return {"status": "disabled"}
    if not AWS_S3_BUCKET:
        return {"status": "not_configured", "detail": "Set AWS_S3_BUCKET to enable S3 archiving."}

    try:
        import boto3

        client = boto3.client("s3", region_name=AWS_REGION)
        uploaded = []
        for path, content_type in (
            (video_path, "video/mp4"),
            (receipt_path, "application/json"),
        ):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            key = "/".join(part for part in (AWS_S3_PREFIX, path.name) if part)
            client.upload_file(
                str(path),
                AWS_S3_BUCKET,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": digest},
                },
            )
            uploaded.append({"key": key, "sha256": digest})
        lambda_hook = {"status": "not_configured"}
        if AWS_LAMBDA_FUNCTION:
            event = {
                "source": "traceframe.processed_clip",
                "bucket": AWS_S3_BUCKET,
                "region": AWS_REGION,
                "objects": uploaded,
            }
            try:
                lambda_client = boto3.client("lambda", region_name=AWS_REGION)
                result = lambda_client.invoke(
                    FunctionName=AWS_LAMBDA_FUNCTION,
                    InvocationType="Event",
                    Payload=json.dumps(event).encode("utf-8"),
                )
                lambda_hook = {
                    "status": "queued" if result.get("StatusCode") == 202 else "failed",
                    "function": AWS_LAMBDA_FUNCTION,
                }
            except Exception as exc:
                lambda_hook = {
                    "status": "failed",
                    "function": AWS_LAMBDA_FUNCTION,
                    "detail": f"Lambda invocation failed: {type(exc).__name__}: {exc}",
                }
        return {
            "status": "archived",
            "bucket": AWS_S3_BUCKET,
            "region": AWS_REGION,
            "objects": uploaded,
            "lambda_hook": lambda_hook,
        }
    except Exception as exc:
        # Keep local processing available if cloud storage is temporarily unavailable.
        return {"status": "failed", "detail": f"S3 archive failed: {type(exc).__name__}: {exc}"}
