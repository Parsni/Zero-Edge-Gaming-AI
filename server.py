import os
import json
import base64
import time
import uuid
import hashlib
import hmac
import math
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature, decode_dss_signature
from cryptography.exceptions import InvalidSignature

from config import CROP_PROVIDER, HOST, PORT, OUTPUT_DIR
from aws_archive import archive_artifacts, archive_status
from video_utils import get_video_info, extract_metadata_comment
from highlight_analyzer import suggest_highlight
from gemini_orchestrator import CropCoordinates, analyze_and_crop
from ffmpeg_engine import process_highlight

app = FastAPI(title="Traceframe Media Provenance Runtime", version="2.11.0", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def local_write_origin_guard(request: Request, call_next):
    request.state.user = None
    public_paths = {"/login", "/login/", "/site/login.js", "/auth/register", "/auth/login", "/auth/logout", "/auth/me", "/health", "/favicon.ico"}
    public_landing = request.method == "GET" and request.url.path == "/"
    public_site_read = request.method == "GET" and request.url.path.startswith("/site/")
    public_demo_read = request.method == "GET" and request.url.path == "/demo-video"
    if request.url.path not in public_paths and not public_landing and not public_demo_read and not public_site_read and not request.url.path.startswith("/auth/"):
        request.state.user = get_session_user(request.cookies.get(SESSION_COOKIE))
        if not request.state.user:
            if request.url.path == "/" or request.url.path.startswith(("/capture", "/site/")):
                return RedirectResponse(url="/login", status_code=303)
            return JSONResponse(status_code=401, content={"detail": "Sign in is required."})
    if request.url.path.startswith("/admin/") and (not request.state.user or request.state.user["role"] != "admin"):
        return JSONResponse(status_code=403, content={"detail": "Administrator access is required."})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        fetch_site = request.headers.get("sec-fetch-site")
        if (origin and origin not in {"null", expected_origin}) or fetch_site == "cross-site":
            return JSONResponse(status_code=403, content={"detail": "Cross-origin writes are not accepted by this local runtime."})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response

# Local SQLite account store. Passwords use scrypt; only random session-token hashes
# are persisted. The first registered account becomes the initial administrator.
import sqlite3
import secrets
import hmac
from datetime import datetime, timedelta, timezone

AUTH_DB_PATH = Path(__file__).parent / ".traceframe-auth.sqlite3"
SESSION_COOKIE = "traceframe_session"
SESSION_SECONDS = 60 * 60 * 24 * 7
MAX_VIDEO_BYTES = 512 * 1024 * 1024

async def read_bounded_upload(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=413, detail="Video upload exceeds the 512 MiB limit.")
    return data

def auth_db():
    conn = sqlite3.connect(AUTH_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at)")
    return conn

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()

def check_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt_b64, hash_b64 = stored.split("$", 2)
        if algorithm != "scrypt": return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def get_session_user(token: Optional[str]):
    if not token or len(token) > 200: return None
    now = datetime.now(timezone.utc).isoformat()
    with auth_db() as conn:
        row = conn.execute("""SELECT u.id, u.username, u.role FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
            (hashlib.sha256(token.encode()).hexdigest(), now)).fetchone()
    return dict(row) if row else None

def issue_session(response, user_id: int, request: Request):
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)).isoformat()
    with auth_db() as conn:
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, expiry))
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (datetime.now(timezone.utc).isoformat(),))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
        samesite="strict", max_age=SESSION_SECONDS, path="/")

@app.get("/login", include_in_schema=False)
@app.get("/login/", include_in_schema=False)
def login_page():
    return FileResponse(str(Path(__file__).parent / "site" / "login.html"))

@app.get("/auth/me")
def auth_me(request: Request):
    user = get_session_user(request.cookies.get(SESSION_COOKIE))
    if not user:
        with auth_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return {"authenticated": False, "registration_open": count == 0}
    return {"authenticated": True, "user": user}

@app.post("/auth/register")
async def auth_register(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not 3 <= len(username) <= 64 or not all(c.isascii() and (c.isalnum() or c in "._-@+") for c in username):
        raise HTTPException(400, "Username must be 3–64 characters using letters, numbers, @, dot, dash, underscore, or plus.")
    if len(password) < 12 or len(password) > 256:
        raise HTTPException(400, "Password must be at least 12 characters.")
    with auth_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        role = "admin" if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
        try:
            cur = conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                (username, hash_password(password), role, datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is already registered.")
        user_id = cur.lastrowid
    response = JSONResponse({"ok": True, "id": user_id, "username": username, "role": role})
    issue_session(response, user_id, request)
    return response

@app.post("/auth/login")
async def auth_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    with auth_db() as conn:
        row = conn.execute("SELECT id,username,password_hash,role,active FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
    if not row or not row["active"] or not check_password(password, row["password_hash"]):
        raise HTTPException(401, "Username or password is incorrect.")
    response = JSONResponse({"ok": True, "id": row["id"], "username": row["username"], "role": row["role"]})
    issue_session(response, row["id"], request)
    return response

@app.post("/auth/logout")
def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with auth_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return response

@app.get("/admin/users")
def admin_users():
    with auth_db() as conn:
        return [dict(row) for row in conn.execute("SELECT id,username,role,active,created_at FROM users ORDER BY id")]

@app.patch("/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request):
    data = await request.json()
    current = request.state.user
    if user_id == current["id"] and (data.get("active") is False or data.get("role") == "user"):
        raise HTTPException(400, "You cannot disable or demote your own administrator account.")
    updates, values = [], []
    if "active" in data and isinstance(data["active"], bool): updates.append("active=?"); values.append(int(data["active"]))
    if data.get("role") in {"user", "admin"}: updates.append("role=?"); values.append(data["role"])
    if not updates: raise HTTPException(400, "No valid account changes provided.")
    values.append(user_id)
    with auth_db() as conn:
        target = conn.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Account not found.")
        will_be_admin = data.get("role", target["role"]) == "admin"
        will_be_active = data.get("active", bool(target["active"]))
        if target["role"] == "admin" and target["active"] and not (will_be_admin and will_be_active):
            admins = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "At least one active administrator must remain.")
        cur = conn.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", values)
        if not cur.rowcount: raise HTTPException(404, "Account not found.")
        if "active" in data and not data["active"]:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"ok": True}

@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request):
    if user_id == request.state.user["id"]: raise HTTPException(400, "You cannot delete your own account.")
    with auth_db() as conn:
        target = conn.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
        if target and target["role"] == "admin" and target["active"]:
            admins = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "At least one active administrator must remain.")
        cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        if not cur.rowcount: raise HTTPException(404, "Account not found.")
    return {"ok": True}

# Keep the signing key outside every served directory. Reuse it across restarts so
# certificates retain a stable verifier identity.
SERVER_KEY_PATH = Path(__file__).parent / ".server-signing-key.pem"
if SERVER_KEY_PATH.exists():
    server_private_key = serialization.load_pem_private_key(SERVER_KEY_PATH.read_bytes(), password=None)
else:
    server_private_key = ec.generate_private_key(ec.SECP256R1())
    SERVER_KEY_PATH.write_bytes(server_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
server_public_key = server_private_key.public_key()
server_public_key_pem = server_public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode("utf-8")

def canonicalize_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))

def sign_server_payload(payload: dict) -> str:
    der_sig = server_private_key.sign(canonicalize_json(payload).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")

def container_hash_with_certificate_mask(video_bytes: bytes, cert_json: str) -> str:
    cert_bytes = cert_json.encode("utf-8")
    offset = video_bytes.find(cert_bytes)
    if offset < 0:
        raise HTTPException(status_code=401, detail="Forensic Violation: Certificate bytes are not present in the uploaded MP4 container.")
    masked = video_bytes[:offset] + (b"\0" * len(cert_bytes)) + video_bytes[offset + len(cert_bytes):]
    return hashlib.sha256(masked).hexdigest()

def verify_provenance(video_bytes: bytes, manifest: dict, signature_b64: str, public_key_pem: str) -> bool:
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="Manifest must be a JSON object.")
    # 1. Raw Video SHA-256 Integrity Verification
    computed_video_hash = hashlib.sha256(video_bytes).hexdigest()
    if computed_video_hash != manifest.get("video_sha256"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Zero-Trust Violation: Raw video SHA-256 mismatch (Expected {manifest.get('video_sha256')}, Got {computed_video_hash})."
        )

    # 2. Sequential Temporal Kinetic-Visual Merkle Chain Verification
    prev_hash = "0" * 64
    temporal_chain = manifest.get("temporal_chain", [])
    declared_length = manifest.get("temporal_chain_length")
    if not isinstance(temporal_chain, list) or not temporal_chain or len(temporal_chain) > 120:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Zero-Trust Violation: Empty temporal chain."
        )

    if declared_length != len(temporal_chain):
        raise HTTPException(status_code=401, detail="Zero-Trust Violation: Temporal chain length mismatch.")

    byte_offset = 0
    for expected_index, link in enumerate(temporal_chain):
        if not isinstance(link, dict) or link.get("index") != expected_index:
            raise HTTPException(status_code=401, detail="Zero-Trust Violation: Invalid temporal chain ordering.")
        chunk_size = link.get("chunk_size_bytes")
        chunk_hash = link.get("chunk_sha256")
        if not isinstance(chunk_size, int) or chunk_size <= 0 or byte_offset + chunk_size > len(video_bytes):
            raise HTTPException(status_code=401, detail=f"Zero-Trust Violation: Invalid chunk size at index {expected_index}.")
        actual_chunk_hash = hashlib.sha256(video_bytes[byte_offset:byte_offset + chunk_size]).hexdigest()
        if not isinstance(chunk_hash, str) or not hmac.compare_digest(actual_chunk_hash, chunk_hash):
            raise HTTPException(status_code=401, detail=f"Zero-Trust Violation: Uploaded video chunk mismatch at index {expected_index}.")
        byte_offset += chunk_size

        entropy_obj = link.get("kinetic_metrics", {})
        if not isinstance(entropy_obj, dict):
            raise HTTPException(status_code=400, detail="Temporal chain metrics must be objects.")
        entropy_canonical = canonicalize_json(entropy_obj)
        entropy_hash = hashlib.sha256(entropy_canonical.encode("utf-8")).hexdigest()
        if link.get("entropy_hash") != entropy_hash:
            raise HTTPException(status_code=401, detail=f"Zero-Trust Violation: Entropy digest mismatch at index {expected_index}.")
        timestamp = link.get("timestamp")
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise HTTPException(status_code=400, detail=f"Temporal chain timestamp is invalid at index {expected_index}.")
        
        expected_link_hash = hashlib.sha256(
            f"{prev_hash}:{chunk_hash}:{entropy_hash}:{timestamp}".encode("utf-8")
        ).hexdigest()

        if expected_link_hash != link.get("chain_link_hash"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Zero-Trust Violation: Temporal Merkle Chain broken at index {link.get('index')}."
            )
        prev_hash = expected_link_hash

    if byte_offset != len(video_bytes):
        raise HTTPException(status_code=401, detail="Zero-Trust Violation: Chunk ranges do not cover the complete uploaded video.")

    if prev_hash != manifest.get("temporal_merkle_root"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Zero-Trust Violation: Temporal Merkle Root mismatch (Expected {manifest.get('temporal_merkle_root')}, Computed {prev_hash})."
        )

    # 3. Edge ECDSA NIST P-256 Signature Verification (Support DER & IEEE P1363)
    try:
        pub_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        canonical_manifest_bytes = canonicalize_json(manifest).encode("utf-8")
        sig_bytes = base64.b64decode(signature_b64)
        
        try:
            pub_key.verify(sig_bytes, canonical_manifest_bytes, ec.ECDSA(hashes.SHA256()))
        except Exception:
            if len(sig_bytes) == 64:
                r = int.from_bytes(sig_bytes[:32], byteorder='big')
                s = int.from_bytes(sig_bytes[32:], byteorder='big')
                der_sig = encode_dss_signature(r, s)
                pub_key.verify(der_sig, canonical_manifest_bytes, ec.ECDSA(hashes.SHA256()))
            else:
                raise
    except InvalidSignature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Zero-Trust Violation: Edge ECDSA cryptographic signature is invalid."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Zero-Trust Violation: Key parsing or signature validation error ({str(e)})."
        )

    return True

@app.get("/")
def serve_index():
    return FileResponse(str(Path(__file__).parent / "site" / "index.html"))

@app.get("/capture", include_in_schema=False)
def capture_redirect():
    return RedirectResponse(url="/capture/", status_code=307)


@app.get("/capture/", include_in_schema=False)
def capture_workspace():
    return FileResponse(str(Path(__file__).parent / "index.html"), media_type="text/html")


@app.get("/capture/{asset_name}", include_in_schema=False)
def capture_asset(asset_name: str):
    # Serve only the browser capture UI's explicit assets; never expose the project root.
    assets = {"styles.css", "capture.js", "renderer.js"}
    if asset_name not in assets:
        raise HTTPException(status_code=404, detail="Capture asset not found.")
    return FileResponse(str(Path(__file__).parent / asset_name))

@app.get("/test-videos/{filename}", include_in_schema=False)
def bundled_test_video(filename: str):
    test_dir = Path(__file__).parent / "outputs" / "test-videos"
    if filename == "ATTRIBUTION.csv":
        path = test_dir / filename
        media_type = "text/csv"
    elif re.fullmatch(r"[A-Za-z0-9_-]{11}\.mp4", filename):
        path = test_dir / filename
        media_type = "video/mp4"
    else:
        raise HTTPException(status_code=404, detail="Test clip not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Test clip not found.")
    return FileResponse(str(path), media_type=media_type, filename=filename,
                        headers={"Cache-Control": "private, max-age=300"})

@app.post("/suggest-highlight")
async def suggest_highlight_endpoint(video: UploadFile = File(...), expected_sha256: str = Form(...)):
    video_bytes = await read_bounded_upload(video)
    actual_sha256 = hashlib.sha256(video_bytes).hexdigest()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) or not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
        raise HTTPException(status_code=401, detail="Integrity check failed: uploaded video bytes do not match the hash from the inspection step.")
    suffix = Path(video.filename or "source.mp4").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".mkv", ".m4v"}:
        suffix = ".mp4"
    temp_path = OUTPUT_DIR / f"suggest_{uuid.uuid4().hex}{suffix}"
    try:
        temp_path.write_bytes(video_bytes)
        info = get_video_info(str(temp_path))
        stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
        if not stream:
            raise HTTPException(status_code=400, detail="Uploaded file does not contain a video stream.")
        duration = float(info.get("format", {}).get("duration") or stream.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise HTTPException(status_code=400, detail="Video duration could not be determined.")
        suggestion = suggest_highlight(str(temp_path), duration, target_duration=10.0)
        suggestion["video_duration_seconds"] = duration
        suggestion["audio_present"] = any(item.get("codec_type") == "audio" for item in info.get("streams", []))
        suggestion["source_sha256"] = actual_sha256
        return suggestion
    finally:
        temp_path.unlink(missing_ok=True)

@app.post("/inspect-video")
async def inspect_video(video: UploadFile = File(...), expected_sha256: str = Form(...)):
    """Compare the browser's file hash with an independent server-side hash before recommendations."""
    video_bytes = await read_bounded_upload(video)
    actual_sha256 = hashlib.sha256(video_bytes).hexdigest()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) or not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
        raise HTTPException(status_code=401, detail="Integrity check failed: browser and server SHA-256 values do not match.")

    suffix = Path(video.filename or "source.mp4").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".mkv", ".m4v"}:
        suffix = ".mp4"
    temp_path = OUTPUT_DIR / f"inspect_{uuid.uuid4().hex}{suffix}"
    try:
        temp_path.write_bytes(video_bytes)
        info = get_video_info(str(temp_path))
        stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
        if not stream:
            raise HTTPException(status_code=400, detail="Uploaded file does not contain a video stream.")
        duration = float(info.get("format", {}).get("duration") or stream.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise HTTPException(status_code=400, detail="Video duration could not be determined.")

        receipt_status = "no_prior_receipt"
        receipt_note = "No earlier signed receipt was found; this check establishes the upload baseline, not who created the video."
        receipt_text = extract_metadata_comment(str(temp_path))
        if receipt_text:
            try:
                receipt_obj = json.loads(receipt_text)
            except Exception:
                receipt_obj = None
            if isinstance(receipt_obj, dict) and receipt_obj.get("certificate_version"):
                from starlette.datastructures import UploadFile as StarletteUploadFile
                import io
                try:
                    await verify_clip(StarletteUploadFile(file=io.BytesIO(video_bytes), filename=Path(video.filename or "upload.mp4").name))
                except HTTPException as error:
                    raise HTTPException(status_code=401, detail=f"Integrity check failed: the embedded receipt did not verify ({error.detail}).")
                receipt_status = "signed_receipt_verified"
                receipt_note = "The embedded Traceframe receipt and output-container digest both verified."

        return {
            "integrity_passed": True,
            "integrity_method": "browser_server_sha256_match",
            "sha256": actual_sha256,
            "file_size_bytes": len(video_bytes),
            "width": int(stream.get("width", 0)),
            "height": int(stream.get("height", 0)),
            "duration_seconds": round(duration, 3),
            "video_codec": stream.get("codec_name", "unknown"),
            "pixel_format": stream.get("pix_fmt", "unknown"),
            "audio_present": any(item.get("codec_type") == "audio" for item in info.get("streams", [])),
            "prior_receipt_status": receipt_status,
            "provenance_note": receipt_note,
        }
    finally:
        temp_path.unlink(missing_ok=True)

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "traceframe-provenance-runtime",
        "runtime_build": app.version,
        "crop_provider": "local_motion_heuristic" if CROP_PROVIDER != "gemini" else "gemini_with_local_fallback",
        "aws_archive": archive_status(),
    }

@app.get("/demo-video", include_in_schema=False)
def public_demo_video():
    """Serve only the generated demo artifact; user outputs remain authenticated."""
    site_root = Path(__file__).parent / "site"
    try:
        demo = json.loads((site_root / "demo_data.json").read_text(encoding="utf-8"))
        filename = Path(str(demo.get("video", ""))).name
    except Exception:
        raise HTTPException(status_code=404, detail="Demo video is not available.")
    if not re.fullmatch(r"highlight_[0-9a-f]{8}\.mp4", filename):
        raise HTTPException(status_code=404, detail="Demo video is not available.")
    video_path = OUTPUT_DIR / filename
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Demo video is not available.")
    return FileResponse(str(video_path), media_type="video/mp4", filename=filename,
                        headers={"Cache-Control": "public, max-age=300"})

@app.post("/process-clip")
async def process_clip(
    video: UploadFile = File(...),
    manifest: str = Form(...),
    signature: str = Form(...),
    public_key: str = Form(...)
):
    try:
        manifest_dict = json.loads(manifest)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON manifest.")

    video_bytes = await read_bounded_upload(video)

    # Zero-Trust Gate Verification
    verify_provenance(video_bytes, manifest_dict, signature, public_key)
    if manifest_dict.get("capture_mode") == "uploaded_video":
        integrity_record = (manifest_dict.get("event_marker") or {}).get("source_integrity")
        if not isinstance(integrity_record, dict) or integrity_record.get("status") != "passed" or not hmac.compare_digest(
            str(integrity_record.get("sha256", "")), str(manifest_dict.get("video_sha256", ""))
        ):
            raise HTTPException(status_code=401, detail="Integrity gate failed: uploaded-video manifest is missing a matching pre-analysis SHA-256 check.")

    # Write raw video to temporary file
    temp_id = uuid.uuid4().hex
    suffix = Path(video.filename or "capture.webm").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".mkv", ".m4v"}:
        suffix = ".webm"
    temp_input_path = OUTPUT_DIR / f"temp_{temp_id}{suffix}"
    with open(temp_input_path, "wb") as f:
        f.write(video_bytes)

    out_path = None
    receipt_path = None
    try:
        video_info = get_video_info(str(temp_input_path))
        video_stream = next((item for item in video_info.get("streams", []) if item.get("codec_type") == "video"), None)
        if not video_stream:
            raise HTTPException(status_code=400, detail="Uploaded file does not contain a video stream.")
        width, height = int(video_stream.get("width", 0)), int(video_stream.get("height", 0))
        source_duration = float(video_info.get("format", {}).get("duration") or video_stream.get("duration") or 0)
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=400, detail="Video dimensions could not be determined.")
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise HTTPException(status_code=400, detail="Uploaded video duration could not be determined.")
        event_marker = manifest_dict.get("event_marker") or {}
        if event_marker.get("kind") in {"operator_selected_time_range", "algorithm_suggested_activity_window", "operator_adjusted_activity_window"}:
            try:
                start = float(event_marker.get("start_time_seconds"))
                end = float(event_marker.get("end_time_seconds"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Selected start and end times must be numbers.")
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > source_duration + 0.05:
                raise HTTPException(status_code=400, detail="Selected time range is outside the uploaded video's duration.")
            if event_marker.get("preserve_full_frame") is True:
                coords = CropCoordinates(start_time=start, duration=end-start, crop_x=0, crop_y=0,
                                         crop_w=width, crop_h=height)
                if event_marker.get("kind") == "algorithm_suggested_activity_window":
                    crop_selection_method = "automatic_activity_suggested_range_full_frame"
                elif event_marker.get("kind") == "operator_adjusted_activity_window":
                    crop_selection_method = "operator_adjusted_activity_suggested_range_full_frame"
                else:
                    crop_selection_method = "operator_selected_range_full_frame"
            else:
                motion_coords, _ = analyze_and_crop(str(temp_input_path), width, height, source_duration)
                coords = CropCoordinates(start_time=start, duration=end-start, crop_x=motion_coords.crop_x,
                                         crop_y=motion_coords.crop_y, crop_w=motion_coords.crop_w,
                                         crop_h=motion_coords.crop_h)
                crop_selection_method = "operator_selected_range_motion_crop"
        else:
            coords, crop_selection_method = analyze_and_crop(str(temp_input_path), width, height, source_duration)

        # Build Bipartite Transformation Certificate
        cert_payload = {
            "certificate_version": "2.0.0-bipartite",
            "capture_mode": manifest_dict.get("capture_mode", "unknown"),
            "event_marker": manifest_dict.get("event_marker"),
            "edge_video_sha256": manifest_dict["video_sha256"],
            "temporal_merkle_root": manifest_dict["temporal_merkle_root"],
            "temporal_chain_length": manifest_dict.get("temporal_chain_length", len(manifest_dict.get("temporal_chain", []))),
            "edge_public_key": public_key,
            "edge_signature": signature,
            "edge_manifest_canonical_sha256": hashlib.sha256(canonicalize_json(manifest_dict).encode("utf-8")).hexdigest(),
            "ai_crop_box": {
                "start_time": coords.start_time,
                "duration": coords.duration,
                "crop_x": coords.crop_x,
                "crop_y": coords.crop_y,
                "crop_w": coords.crop_w,
                "crop_h": coords.crop_h
            },
            "crop_selection_method": crop_selection_method,
            "source_video": {
                "width": width,
                "height": height,
                "duration_seconds": source_duration,
                "audio_present": any(item.get("codec_type") == "audio" for item in video_info.get("streams", [])),
            },
            "output_video": {
                "width": width if (coords.crop_x == 0 and coords.crop_y == 0 and coords.crop_w == width and coords.crop_h == height) else 1080,
                "height": height if (coords.crop_x == 0 and coords.crop_y == 0 and coords.crop_w == width and coords.crop_h == height) else 1920,
                "duration_seconds": round(coords.duration, 3),
                "audio_present": any(item.get("codec_type") == "audio" for item in video_info.get("streams", [])),
                "codec": "h264",
                "encoding": (
                    f"H.264 QP 0 lossless for decoded {video_stream.get('pix_fmt')} pixels; full frame preserved"
                    if coords.crop_x == 0 and coords.crop_y == 0 and coords.crop_w == width and coords.crop_h == height
                    and video_stream.get("pix_fmt") in {"yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"}
                    else "H.264 CRF 18 lossy fallback; decoded graphics may differ"
                ),
                "source_bytes": len(video_bytes),
                "output_bytes_hex": "0" * 16,
            },
            "server_public_key": server_public_key_pem,
            "issued_at": time.time(),
            "output_container_sha256": "0" * 64
        }

        server_sig = sign_server_payload(cert_payload)

        final_cert = {
            **cert_payload,
            "server_signature": server_sig
        }
        final_cert_json = canonicalize_json(final_cert)

        # Process Highlight with FFmpeg and Embed Metadata
        out_filename = f"highlight_{temp_id[:8]}.mp4"
        out_path = OUTPUT_DIR / out_filename
        process_highlight(str(temp_input_path), str(out_path), coords, final_cert_json)
        cert_payload["output_video"]["output_bytes_hex"] = f"{out_path.stat().st_size:016x}"

        # The embedded certificate is masked during hashing to avoid a self-referential
        # digest. All other MP4 bytes, including the media payload and trailing bytes,
        # remain covered. P1363 signatures keep the replacement certificate byte-length stable.
        output_bytes = out_path.read_bytes()
        cert_payload["output_container_sha256"] = container_hash_with_certificate_mask(output_bytes, final_cert_json)
        final_cert = {**cert_payload, "server_signature": sign_server_payload(cert_payload)}
        updated_cert_json = canonicalize_json(final_cert)
        if len(updated_cert_json.encode("utf-8")) != len(final_cert_json.encode("utf-8")):
            raise RuntimeError("Certificate replacement changed the MP4 metadata byte length.")
        cert_bytes = final_cert_json.encode("utf-8")
        cert_offset = output_bytes.find(cert_bytes)
        if cert_offset < 0:
            raise RuntimeError("FFmpeg output does not contain the expected provenance certificate.")
        out_path.write_bytes(output_bytes[:cert_offset] + updated_cert_json.encode("utf-8") + output_bytes[cert_offset + len(cert_bytes):])
        final_cert_json = updated_cert_json

        # Companion receipt
        receipt_path = OUTPUT_DIR / f"{out_filename}.provenance.json"
        with open(receipt_path, "w", encoding="utf-8") as rf:
            rf.write(final_cert_json)

        aws_archive = archive_artifacts(out_path, receipt_path)

        return {
            "provenance_verified": True,
            "temporal_merkle_root": manifest_dict["temporal_merkle_root"],
            "ai_crop_box": cert_payload["ai_crop_box"],
            "crop_selection_method": crop_selection_method,
            "server_signature": final_cert["server_signature"],
            "output_url": f"/outputs/{out_filename}",
            "certificate": final_cert,
            "aws_archive": aws_archive
        }
    except Exception:
        for partial_path in (out_path, receipt_path):
            if partial_path is not None and partial_path.exists():
                try:
                    partial_path.unlink()
                except Exception:
                    pass
        raise
    finally:
        if temp_input_path.exists():
            try:
                temp_input_path.unlink()
            except Exception:
                pass

@app.post("/verify-clip")
async def verify_clip(
    video: UploadFile = File(...)
):
    temp_id = uuid.uuid4().hex
    safe_filename = Path(video.filename or "upload.mp4").name
    temp_verify_path = OUTPUT_DIR / f"verify_{temp_id}_{safe_filename}"
    video_bytes = await read_bounded_upload(video)
    with open(temp_verify_path, "wb") as f:
        f.write(video_bytes)

    try:
        raw_cert_str = extract_metadata_comment(str(temp_verify_path))
        
        if not raw_cert_str:
            companion_path = OUTPUT_DIR / f"{safe_filename}.provenance.json"
            if companion_path.exists():
                with open(companion_path, "r", encoding="utf-8") as cf:
                    raw_cert_str = cf.read()

        if not raw_cert_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Forensic Violation: No cryptographic provenance certificate found in MP4 metadata."
            )

        try:
            cert_obj = json.loads(raw_cert_str)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Forensic Violation: Malformed provenance certificate JSON."
            )

        server_sig_b64 = cert_obj.get("server_signature")
        if not server_sig_b64:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Forensic Violation: Missing server signature on provenance certificate."
            )

        expected_container_hash = cert_obj.get("output_container_sha256")
        if not expected_container_hash or container_hash_with_certificate_mask(video_bytes, raw_cert_str) != expected_container_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Forensic Violation: MP4 container SHA-256 mismatch; media or container bytes changed."
            )

        unsigned_cert_payload = {k: v for k, v in cert_obj.items() if k != "server_signature"}
        canonical_unsigned = canonicalize_json(unsigned_cert_payload)

        signing_pub_key_pem = server_public_key_pem
        try:
            pub_key = serialization.load_pem_public_key(signing_pub_key_pem.encode("utf-8"))
            sig_bytes = base64.b64decode(server_sig_b64)
            try:
                pub_key.verify(sig_bytes, canonical_unsigned.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
            except Exception:
                if len(sig_bytes) == 64:
                    r = int.from_bytes(sig_bytes[:32], byteorder='big')
                    s = int.from_bytes(sig_bytes[32:], byteorder='big')
                    der_sig = encode_dss_signature(r, s)
                    pub_key.verify(der_sig, canonical_unsigned.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
                else:
                    raise
        except InvalidSignature:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Forensic Violation: Bipartite Transformation Certificate signature is invalid or tampered."
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Forensic Violation: Cryptographic verification failure ({str(e)})."
            )

        return {
            "forensic_authentic": True,
            "certificate_version": cert_obj.get("certificate_version"),
            "edge_video_sha256": cert_obj.get("edge_video_sha256"),
            "output_container_sha256": expected_container_hash,
            "temporal_merkle_root": cert_obj.get("temporal_merkle_root"),
            "ai_crop_box": cert_obj.get("ai_crop_box"),
            "crop_selection_method": cert_obj.get("crop_selection_method", "legacy_unknown"),
            "issued_at": cert_obj.get("issued_at"),
            "message": "Zero-Trust Bipartite Lineage & Cryptographic Transformation Certificate Authenticated."
        }
    finally:
        if temp_verify_path.exists():
            try:
                temp_verify_path.unlink()
            except Exception:
                pass

app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")
@app.get("/site/{asset_path:path}")
def serve_site_asset(asset_path: str):
    site_root = (Path(__file__).parent / "site").resolve()
    requested = (site_root / asset_path).resolve()
    if site_root not in requested.parents or not requested.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(requested))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
