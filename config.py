import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8007"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()
CROP_PROVIDER = os.getenv("CROP_PROVIDER", "local").strip().lower()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs")).resolve()
AWS_ARCHIVE_ENABLED = os.getenv("AWS_ARCHIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "").strip()
AWS_REGION = os.getenv("AWS_REGION", "").strip() or None
AWS_S3_PREFIX = os.getenv("AWS_S3_PREFIX", "traceframe/receipts").strip("/")
AWS_LAMBDA_FUNCTION = os.getenv("AWS_LAMBDA_FUNCTION", "").strip()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
