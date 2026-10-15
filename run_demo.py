import os
import sys
import time
import json
import base64
import hashlib
import subprocess
import webbrowser
import socket
import getpass
from pathlib import Path
from urllib.parse import urlparse

# Ensure UTF-8 stdout
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:8007").rstrip("/")
RUNTIME_BUILD = "2.6.0"

def runtime_is_ready(url):
    try:
        response = httpx.get(f"{url}/health", timeout=1.5)
        return response.status_code < 400 and response.json().get("runtime_build") == RUNTIME_BUILD
    except Exception:
        return False

def free_local_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return listener.getsockname()[1]

def start_runtime():
    global SERVER_URL
    parsed = urlparse(SERVER_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000
    for existing_port in range(port, min(port + 11, 65536)):
        existing_url = f"http://{host}:{existing_port}"
        if runtime_is_ready(existing_url):
            SERVER_URL = existing_url
            return
    for attempt in range(2):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", host, "--port", str(port)],
            cwd=Path(__file__).resolve().parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags
        )
        candidate_url = f"http://{host}:{port}"
        for _ in range(30):
            if runtime_is_ready(candidate_url):
                SERVER_URL = candidate_url
                return
            time.sleep(0.5)
        if attempt == 0:
            port = free_local_port(host)
    raise RuntimeError("Could not start the Traceframe runtime on the requested or fallback port.")

def authenticated_client():
    client = httpx.Client(timeout=60.0, follow_redirects=True)
    state = client.get(f"{SERVER_URL}/auth/me").json()
    if state.get("authenticated"):
        return client
    if state.get("registration_open"):
        print("\nNo Traceframe account exists yet. Create the first account (it becomes the administrator).")
        username = input("Admin username: ").strip()
        password = getpass.getpass("Password (at least 12 characters): ")
        confirm = getpass.getpass("Repeat password: ")
        if password != confirm:
            raise RuntimeError("Passwords did not match.")
        response = client.post(f"{SERVER_URL}/auth/register", json={"username": username, "password": password})
    else:
        print("\nSign in with your Traceframe account to run the protected demo pipeline.")
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        response = client.post(f"{SERVER_URL}/auth/login", json={"username": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"Traceframe authentication failed: {response.text}")
    return client

def canonicalize(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def create_action_highlight_sample(output_path: Path):
    print("[1/5] Rendering High-Definition 1080p Action Video (WebM VP9)...")
    # Generate animated video with timer, moving bounding boxes and gradient
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "testsrc2=duration=5:size=1920x1080:rate=30",
        "-c:v", "libvpx-vp9",
        "-b:v", "2000k",
        str(output_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg generation failed: {res.stderr}")

def main():
    print("=" * 75)
    print("TRACEFRAME — SYNTHETIC DEMO PIPELINE")
    print("=" * 75)

    # Health check and start a compatible local runtime when needed.
    if runtime_is_ready(SERVER_URL):
        print("[+] FastAPI Cloud Gate: Online & Ready (200 OK)")
    else:
        print("[+] Finding or starting a compatible local Traceframe runtime...")
        start_runtime()
        print(f"[+] FastAPI Gate: Online at {SERVER_URL}")

    api = authenticated_client()

    # 1. Generate Clip
    raw_video_path = Path("./outputs/demo_raw_capture.webm")
    raw_video_path.parent.mkdir(parents=True, exist_ok=True)
    create_action_highlight_sample(raw_video_path)
    video_bytes = raw_video_path.read_bytes()
    video_sha = sha256_hex(video_bytes)
    print(f"[+] Raw Video Captured: {len(video_bytes)} bytes | SHA-256: {video_sha[:16]}...")

    # 2. Edge Provenance: Temporal Kinetic Merkle Chain & Biomechanical Telemetry
    print("\n[2/5] Building a five-link signed byte-chunk manifest (synthetic input)...")
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")

    chunk_count = 5
    chunk_size = len(video_bytes) // chunk_count
    base_time = int(time.time() * 1000)

    prev_hash = "0" * 64
    chain = []
    for i in range(chunk_count):
        c_bytes = video_bytes[i*chunk_size : (i+1)*chunk_size if i < chunk_count-1 else len(video_bytes)]
        c_hash = sha256_hex(c_bytes)
        t_stamp = base_time + (i * 1000)
        entropy = {
            "mouse_delta_variance": round(18.42 + (i * 2.3), 4),
            "keystroke_count": 3 + (i * 2),
            "frame_jitter_variance_ms": round(0.0125 + (i * 0.003), 4),
            "frame_samples": 60,
            "mouse_events_count": 52 + (i * 8),
            "snapshot_perf_now": round(500.0 + (i * 1000), 2)
        }
        entropy_canonical = canonicalize(entropy)
        entropy_hash = sha256_hex(entropy_canonical.encode("utf-8"))
        link_hash = sha256_hex(f"{prev_hash}:{c_hash}:{entropy_hash}:{t_stamp}".encode("utf-8"))

        chain.append({
            "chain_link_hash": link_hash,
            "chunk_sha256": c_hash,
            "chunk_size_bytes": len(c_bytes),
            "entropy_hash": entropy_hash,
            "index": i,
            "kinetic_metrics": entropy,
            "timestamp": t_stamp
        })
        prev_hash = link_hash
        print(f"    ├─ Link #{i+1}: Chunk={c_hash[:8]}... | Entropy ΔVar={entropy['mouse_delta_variance']} | LinkHash={link_hash[:12]}...")

    merkle_root = prev_hash
    print(f"[+] Signed input chunk-chain root: {merkle_root}")

    manifest = {
        "capture_mode": "synthetic_demo",
        "hardware_telemetry": {
            "source": "synthetic_demo_fixture",
            "values_are_measured": False
        },
        "temporal_chain": chain,
        "temporal_chain_length": len(chain),
        "temporal_merkle_root": merkle_root,
        "timestamp": base_time + 5000,
        "version": "2.1.0-synthetic-demo",
        "video_sha256": video_sha
    }

    # Sign Manifest with Edge ECDSA Key
    manifest_bytes = canonicalize(manifest).encode("utf-8")
    sig_raw = private_key.sign(manifest_bytes, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(sig_raw).decode("utf-8")
    print(f"[+] Local ECDSA NIST P-256 Signature Generated ({len(sig_b64)} chars)")

    # 3. Transmit to Cloud Zero-Trust Gate
    print("\n[3/5] Submitting the signed clip to the local processing service...")
    files = {"video": ("demo_raw.webm", video_bytes, "video/webm")}
    data = {
        "manifest": json.dumps(manifest),
        "signature": sig_b64,
        "public_key": public_key_pem
    }

    resp = api.post(f"{SERVER_URL}/process-clip", files=files, data=data, timeout=60.0)
    if resp.status_code != 200:
        print(f"[-] Cloud Gate Rejected: {resp.status_code} - {resp.text}")
        return

    res_data = resp.json()
    print("[+] Input hash, signed chunk chain, and client signature verified.")
    print(f"[+] Local crop parameters: {res_data['ai_crop_box']}")
    print(f"[+] Server Bipartite Signature: {res_data['server_signature'][:24]}...")

    output_rel = res_data["output_url"].lstrip("/")
    final_mp4 = Path(".") / output_rel
    receipt_json = Path(f"{final_mp4}.provenance.json")

    print(f"[+] Final Certified MP4 Created: {final_mp4} ({final_mp4.stat().st_size} bytes)")
    print(f"[+] Companion Provenance Receipt: {receipt_json}")

    # 4. Third-Party Forensic Audit
    print("\n[4/5] Executing Independent Third-Party Forensic Audit (POST /verify-clip)...")
    with open(final_mp4, "rb") as vf:
        verify_resp = api.post(f"{SERVER_URL}/verify-clip", files={"video": (final_mp4.name, vf.read(), "video/mp4")}, timeout=10.0)

    if verify_resp.status_code == 200:
        v_data = verify_resp.json()
        print(f"[+] FORENSIC AUDIT PASSED: {v_data['message']}")
        print(f"    • Verified Merkle Root: {v_data['temporal_merkle_root']}")
        print(f"    • Verified Edge Hash:   {v_data['edge_video_sha256']}")
    else:
        print(f"[-] Forensic Audit Failed: {verify_resp.text}")

    print("\n[+] Rebuilding the dataset, ML baseline, architecture graphic, and report...")
    pack = subprocess.run([sys.executable, "research/build_demo_pack.py"], cwd=Path(__file__).resolve().parent, capture_output=True, text=True)
    if pack.returncode == 0:
        print("[+] Research pack refreshed under site/: CSV, model chart, architecture graphic, and report.")
    else:
        print(f"[!] Research pack refresh skipped: {pack.stderr[-1200:]}")

    # 5. Launch UI & Output
    print("\n[5/5] Launching Visual Results & Web Dashboard...")
    print(f"\n👉 Dashboard URL: {SERVER_URL}")
    print(f"👉 Certified MP4: {final_mp4.resolve()}")

    if os.getenv("DEMO_NO_OPEN") != "1":
        try:
            webbrowser.open(SERVER_URL)
            if sys.platform == "win32":
                os.startfile(str(final_mp4.resolve()))
        except Exception as e:
            print(f"Notice: {e}")
    api.close()

    print("\n" + "=" * 75)
    print("DEMO COMPLETE: CAPTURE → SIGNED RECEIPT → INTEGRITY AUDIT")
    print("=" * 75)

if __name__ == "__main__":
    main()
