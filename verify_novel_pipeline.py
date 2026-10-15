import os
import sys
import time
import json
import base64
import hashlib
import subprocess
from pathlib import Path

# Ensure UTF-8 stdout on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

BASE_URL = "http://127.0.0.1:8000"

def canonicalize(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def generate_edge_keys():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    return private_key, pub_pem

def sign_manifest(manifest: dict, private_key) -> str:
    canonical_bytes = canonicalize(manifest).encode("utf-8")
    sig = private_key.sign(canonical_bytes, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode("utf-8")

def create_synthetic_video(filepath: Path, duration: int = 3):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size=1920x1080:rate=30",
        "-c:v", "libvpx-vp9",
        "-b:v", "1000k",
        str(filepath)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg synthetic video generation failed: {res.stderr}")

def build_valid_manifest(video_bytes: bytes):
    full_video_hash = sha256_hex(video_bytes)
    base_time = int(time.time() * 1000)

    chunk_len = len(video_bytes) // 3
    chunks = [
        video_bytes[:chunk_len],
        video_bytes[chunk_len:2*chunk_len],
        video_bytes[2*chunk_len:]
    ]

    telemetry = {
        "arch": "x64",
        "cpu_cores": 16,
        "cpu_model": "Intel/AMD Verification Test CPU",
        "platform": "win32",
        "release": "10.0.22631",
        "system_mac": "00:11:22:33:44:55",
        "total_mem_bytes": 34359738368
    }

    prev_hash = "0" * 64
    chain = []

    for i, c_bytes in enumerate(chunks):
        c_hash = sha256_hex(c_bytes)
        t_stamp = base_time + (i * 1000)
        entropy_obj = {
            "frame_jitter_variance_ms": 0.0421 + (i * 0.01),
            "frame_samples": 60,
            "keystroke_count": 2 + i,
            "mouse_delta_variance": 14.821 + (i * 3.5),
            "mouse_events_count": 45 + (i * 10),
            "snapshot_perf_now": 1000.0 + (i * 1000)
        }
        entropy_canonical = canonicalize(entropy_obj)
        entropy_hash = sha256_hex(entropy_canonical.encode("utf-8"))

        link_hash = sha256_hex(f"{prev_hash}:{c_hash}:{entropy_hash}:{t_stamp}".encode("utf-8"))

        chain.append({
            "chain_link_hash": link_hash,
            "chunk_sha256": c_hash,
            "entropy_hash": entropy_hash,
            "index": i,
            "kinetic_metrics": entropy_obj,
            "timestamp": t_stamp
        })
        prev_hash = link_hash

    manifest = {
        "hardware_telemetry": telemetry,
        "temporal_chain": chain,
        "temporal_chain_length": len(chain),
        "temporal_merkle_root": prev_hash,
        "timestamp": base_time + 3000,
        "version": "2.0.0-kinetic-merkle",
        "video_sha256": full_video_hash
    }

    return manifest

def main():
    print("=" * 70)
    print("[*] ZERO-TRUST KINETIC-VISUAL MEDIA PROVENANCE FORENSIC TEST SUITE")
    print("=" * 70)

    try:
        health_resp = httpx.get(f"{BASE_URL}/health", timeout=5.0)
        assert health_resp.status_code == 200, f"Health check returned {health_resp.status_code}"
        print("[+] Health Check: Server is active and operational.\n")
    except Exception as e:
        print(f"[-] Server connection failed: {e}")
        print("Ensure FastAPI server is running on http://127.0.0.1:8000")
        sys.exit(1)

    temp_video = Path("./outputs/test_synthetic.webm")
    temp_video.parent.mkdir(parents=True, exist_ok=True)
    create_synthetic_video(temp_video, duration=3)
    video_bytes = temp_video.read_bytes()
    private_key, public_key_pem = generate_edge_keys()

    passed_count = 0

    # -------------------------------------------------------------------------
    # TEST 1: Valid End-to-End Provenance + AI Crop
    # -------------------------------------------------------------------------
    print("--- [STAGE 1] Valid End-to-End Provenance & Multimodal Crop ---")
    manifest = build_valid_manifest(video_bytes)
    signature = sign_manifest(manifest, private_key)

    files = {"video": ("test_synthetic.webm", video_bytes, "video/webm")}
    data = {
        "manifest": json.dumps(manifest),
        "signature": signature,
        "public_key": public_key_pem
    }

    resp1 = httpx.post(f"{BASE_URL}/process-clip", files=files, data=data, timeout=60.0)
    if resp1.status_code == 200:
        res_json = resp1.json()
        assert res_json.get("provenance_verified") is True
        assert "server_signature" in res_json
        assert "output_url" in res_json
        out_rel_path = res_json["output_url"].lstrip("/")
        generated_mp4 = Path(".") / out_rel_path
        assert generated_mp4.exists(), f"Output MP4 file {generated_mp4} was not found."
        
        ffprobe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(generated_mp4)
        ]
        info = json.loads(subprocess.run(ffprobe_cmd, capture_output=True, text=True).stdout)
        v_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert int(v_stream["width"]) == 1080 and int(v_stream["height"]) == 1920, "Output resolution must be 1080x1920"

        print(f"[+] STAGE 1 PASSED: Processed {generated_mp4} at 1080x1920 with valid server signature.")
        passed_count += 1
        saved_mp4_path = generated_mp4
    else:
        print(f"[-] STAGE 1 FAILED: {resp1.status_code} - {resp1.text}")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # TEST 2: Temporal Merkle Chain Tamper Rejection
    # -------------------------------------------------------------------------
    print("\n--- [STAGE 2] Temporal Merkle Chain Tamper Rejection ---")
    tampered_manifest = build_valid_manifest(video_bytes)
    tampered_manifest["temporal_chain"][1]["kinetic_metrics"]["mouse_delta_variance"] = 9999.99
    tampered_sig = sign_manifest(tampered_manifest, private_key)

    files = {"video": ("test_synthetic.webm", video_bytes, "video/webm")}
    data = {
        "manifest": json.dumps(tampered_manifest),
        "signature": tampered_sig,
        "public_key": public_key_pem
    }

    resp2 = httpx.post(f"{BASE_URL}/process-clip", files=files, data=data, timeout=10.0)
    if resp2.status_code == 401:
        print(f"[+] STAGE 2 PASSED: Tampered Merkle Link successfully rejected (HTTP 401: {resp2.json().get('detail')}).")
        passed_count += 1
    else:
        print(f"[-] STAGE 2 FAILED: Expected 401 Unauthorized, got {resp2.status_code} - {resp2.text}")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # TEST 3: Raw Video Byte-Tamper Rejection
    # -------------------------------------------------------------------------
    print("\n--- [STAGE 3] Raw Video Byte-Tamper Rejection ---")
    tampered_bytes = bytearray(video_bytes)
    tampered_bytes[100] = (tampered_bytes[100] + 1) % 256
    tampered_bytes = bytes(tampered_bytes)

    valid_manifest = build_valid_manifest(video_bytes)
    valid_sig = sign_manifest(valid_manifest, private_key)

    files = {"video": ("test_synthetic.webm", tampered_bytes, "video/webm")}
    data = {
        "manifest": json.dumps(valid_manifest),
        "signature": valid_sig,
        "public_key": public_key_pem
    }

    resp3 = httpx.post(f"{BASE_URL}/process-clip", files=files, data=data, timeout=10.0)
    if resp3.status_code == 401:
        print(f"[+] STAGE 3 PASSED: Tampered Video Payload successfully rejected (HTTP 401: {resp3.json().get('detail')}).")
        passed_count += 1
    else:
        print(f"[-] STAGE 3 FAILED: Expected 401 Unauthorized, got {resp3.status_code} - {resp3.text}")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # TEST 4: Third-Party Forensic MP4 Audit via /verify-clip
    # -------------------------------------------------------------------------
    print("\n--- [STAGE 4] Third-Party Forensic MP4 Audit ---")
    with open(saved_mp4_path, "rb") as vf:
        verify_files = {"video": (saved_mp4_path.name, vf.read(), "video/mp4")}

    resp4 = httpx.post(f"{BASE_URL}/verify-clip", files=verify_files, timeout=10.0)
    if resp4.status_code == 200:
        ver_json = resp4.json()
        assert ver_json.get("forensic_authentic") is True
        print(f"[+] STAGE 4 PASSED: Output MP4 verified forensically: {ver_json.get('message')}")
        passed_count += 1
    else:
        print(f"[-] STAGE 4 FAILED: Expected 200 OK, got {resp4.status_code} - {resp4.text}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"[SUCCESS] ALL {passed_count}/4 CRYPTOGRAPHIC & FORENSIC TESTS PASSED (4/4)!")
    print("=" * 70)

if __name__ == "__main__":
    main()
