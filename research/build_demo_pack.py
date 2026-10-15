"""Build a reproducible, explicitly synthetic Traceframe research demo pack."""
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
SITE = ROOT / "site"

def audit(client, name, data):
    return client.post("/verify-clip", files={"video": (name, data, "video/mp4")})

def video_features(path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height,bit_rate:format=duration,size,bit_rate", "-of", "json", str(path)
    ], capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    stream = info["streams"][0]
    fmt = info["format"]
    return {
        "codec": stream.get("codec_name", "unknown"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "duration_seconds": float(fmt.get("duration", 0)),
        "file_size_bytes": int(fmt.get("size", path.stat().st_size)),
        "bitrate_bps": int(fmt.get("bit_rate", stream.get("bit_rate", 0)) or 0),
    }

def sigmoid(x):
    if x < -40: return 0.0
    if x > 40: return 1.0
    return 1.0 / (1.0 + math.exp(-x))

def fit_tiny_logistic(rows, train_groups):
    fields = ["file_size_bytes", "width", "height", "duration_seconds", "bitrate_bps"]
    train = [r for r in rows if r["source_file"] in train_groups]
    means = [sum(float(r[k]) for r in train) / len(train) for k in fields]
    scales = []
    for k, mean in zip(fields, means):
        variance = sum((float(r[k]) - mean) ** 2 for r in train) / len(train)
        scales.append(math.sqrt(variance) or 1.0)
    def vector(row): return [(float(row[k]) - m) / s for k, m, s in zip(fields, means, scales)]
    weights = [0.0] * len(fields)
    bias = 0.0
    rate = 0.08
    for _ in range(1200):
        grad = [0.0] * len(fields)
        gb = 0.0
        for row in train:
            x = vector(row)
            p = sigmoid(bias + sum(w * v for w, v in zip(weights, x)))
            error = p - int(row["tampered_label"])
            gb += error
            for j, value in enumerate(x): grad[j] += error * value
        bias -= rate * gb / len(train)
        weights = [w - rate * g / len(train) for w, g in zip(weights, grad)]
    return fields, means, scales, weights, bias

def main():
    clips = sorted(OUT.glob("highlight_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    temp_db = Path(tempfile.mktemp(suffix=".sqlite3"))
    server.AUTH_DB_PATH = temp_db
    client = TestClient(server.app)
    registered = client.post("/auth/register", json={"username":"research-pack-admin", "password":"temporary-demo-password-9876"})
    if registered.status_code != 200:
        raise RuntimeError(f"Could not initialize isolated research verification session: {registered.text}")

    rows = []
    verified_clips = []
    try:
        for path in clips:
            raw = path.read_bytes()
            good = audit(client, path.name, raw)
            if good.status_code != 200:
                continue
            cert_path = path.with_name(path.name + ".provenance.json")
            cert = json.loads(cert_path.read_text(encoding="utf-8")) if cert_path.exists() else {}
            features = video_features(path)
            mdat = raw.find(b"mdat")
            if mdat < 0 or mdat + 1028 >= len(raw):
                continue
            changed = bytearray(raw)
            changed[mdat + 1024] ^= 1
            bad = audit(client, path.name, bytes(changed))
            if bad.status_code == 200:
                raise RuntimeError(f"Tamper experiment unexpectedly passed for {path.name}")
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video:
                temp_video.write(changed)
                temp_video_path = Path(temp_video.name)
            try:
                mutated_features = video_features(temp_video_path)
                for key in ("codec", "width", "height", "duration_seconds", "file_size_bytes", "bitrate_bps"):
                    if mutated_features[key] != features[key]:
                        raise RuntimeError(f"The controlled mutation changed ffprobe metadata for {path.name}: {key}")
            finally:
                temp_video_path.unlink(missing_ok=True)
            common = {
                "source_file": path.name,
                "source_origin_note": "known_synthetic_ffmpeg_demo" if path.name == "highlight_255081d8.mp4" else "capture_origin_not_attested",
                "codec": features["codec"],
                "width": features["width"],
                "height": features["height"],
                "duration_seconds": round(features["duration_seconds"], 3),
                "file_size_bytes": features["file_size_bytes"],
                "bitrate_bps": features["bitrate_bps"],
                "certificate_version": cert.get("certificate_version", "unknown"),
                "chain_length": cert.get("temporal_chain_length", ""),
                "crop_selection_method": cert.get("crop_selection_method", "legacy_unknown"),
            }
            rows.append({**common, "trial": "unaltered", "tampered_label": 0, "crypto_audit_pass": 1})
            rows.append({**common, "trial": "one_payload_byte_flipped", "tampered_label": 1, "crypto_audit_pass": 0})
            verified_clips.append((path, cert, features, good.json()))
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(temp_db) + suffix)
            if candidate.exists(): candidate.unlink()

    if len(verified_clips) < 2:
        raise RuntimeError("Need at least two currently verifiable demo clips to build the pack.")

    columns = ["source_file", "source_origin_note", "codec", "width", "height", "duration_seconds", "file_size_bytes", "bitrate_bps", "certificate_version", "chain_length", "crop_selection_method", "trial", "tampered_label", "crypto_audit_pass"]
    with (SITE / "demo_dataset.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)

    groups = sorted({r["source_file"] for r in rows})
    split = max(1, int(len(groups) * 0.8))
    train_groups, test_groups = set(groups[:split]), set(groups[split:])
    fields, means, scales, weights, bias = fit_tiny_logistic(rows, train_groups)
    def predict(row):
        score = bias + sum(w * ((float(row[k]) - m) / s) for w, k, m, s in zip(weights, fields, means, scales))
        return int(sigmoid(score) >= 0.5)
    test = [r for r in rows if r["source_file"] in test_groups]
    model_accuracy = sum(predict(r) == int(r["tampered_label"]) for r in test) / len(test) if test else 0
    crypto_accuracy = sum(int(r["crypto_audit_pass"]) == 1 - int(r["tampered_label"]) for r in rows) / len(rows)
    # Paired inputs have identical metadata features and opposite labels. This establishes
    # an information limit for the specified baseline independently of a lucky split.
    paired_exact = all(
        all(a[k] == b[k] for k in fields)
        for source in groups
        for a, b in [([r for r in rows if r["source_file"] == source and r["trial"] == "unaltered"][0],
                      [r for r in rows if r["source_file"] == source and r["trial"] != "unaltered"][0])]
    )
    sample, cert, features, audit_result = verified_clips[0]
    demo = {
        "video": sample.name,
        "dataset": "demo_dataset.csv",
            "source_count": len(verified_clips),
        "trial_count": len(rows),
        "verified_video_bytes": features["file_size_bytes"],
        "width": features["width"], "height": features["height"],
        "duration_seconds": round(features["duration_seconds"], 2),
        "codec": features["codec"],
        "chain_length": cert.get("temporal_chain_length"),
        "crop_selection_method": cert.get("crop_selection_method", "legacy_unknown"),
        "container_sha256": audit_result.get("output_container_sha256"),
        "model_test_accuracy": round(model_accuracy, 3),
        "crypto_audit_accuracy": round(crypto_accuracy, 3),
        "paired_metadata_identical": paired_exact,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    (SITE / "demo_data.json").write_text(json.dumps(demo, indent=2), encoding="utf-8")

    SITE.joinpath("architecture.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 480" role="img" aria-labelledby="title desc"><title id="title">Traceframe evidence pipeline</title><desc id="desc">Edge capture sends a signed clip manifest to a local motion-framing and media-processing service, which issues an MP4 receipt for audit. Cloud storage and a future transparency ledger are optional or proposed.</desc><defs><linearGradient id="g" x1="0" x2="1"><stop stop-color="#c6f36b"/><stop offset="1" stop-color="#57d9c3"/></linearGradient><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#a9bc94"/></marker></defs><rect width="1200" height="480" rx="24" fill="#121712"/><text x="54" y="61" fill="#c6f36b" font-family="monospace" font-size="15" letter-spacing="3">EDGE → CLOUD → AUDIT · PROTOTYPE DATA FLOW</text><g font-family="Arial,sans-serif"><rect x="55" y="125" width="300" height="230" rx="14" fill="#1d261d" stroke="#566747"/><text x="82" y="165" fill="#c6f36b" font-size="14">01 / EDGE CAPTURE</text><text x="82" y="213" fill="#f2f4ee" font-size="24" font-weight="700">Capture a moment</text><text x="82" y="249" fill="#aab4a2" font-size="15">Screen stream + browser timing</text><text x="82" y="276" fill="#aab4a2" font-size="15">Chunk hashes + ephemeral signature</text><text x="82" y="324" fill="#d0d6c8" font-family="monospace" font-size="12">CLIENT KEY · NOT DEVICE ENROLLED</text><rect x="450" y="125" width="300" height="230" rx="14" fill="#1b2526" stroke="#496a63"/><text x="477" y="165" fill="#80e5cb" font-size="14">02 / LOCAL MOTION + FFMPEG</text><text x="477" y="213" fill="#f2f4ee" font-size="24" font-weight="700">Frame the highlight</text><text x="477" y="249" fill="#aab4a2" font-size="15">Local motion heuristic (default)</text><text x="477" y="276" fill="#aab4a2" font-size="15">FFmpeg portrait transcode</text><text x="477" y="324" fill="#d0d6c8" font-family="monospace" font-size="12">OFFLINE DEFAULT · AWS OPTIONAL</text><rect x="845" y="125" width="300" height="230" rx="14" fill="#25251b" stroke="#746c46"/><text x="872" y="165" fill="#e8d782" font-size="14">03 / AUDIT + DISTRIBUTION</text><text x="872" y="213" fill="#f2f4ee" font-size="24" font-weight="700">Verify the receipt</text><text x="872" y="249" fill="#aab4a2" font-size="15">Signed MP4 + companion JSON</text><text x="872" y="276" fill="#aab4a2" font-size="15">Byte changes rejected by verifier</text><text x="872" y="324" fill="#d0d6c8" font-family="monospace" font-size="12">ON-CHAIN LEDGER · PROPOSED</text><path d="M365 240H432" stroke="url(#g)" stroke-width="3" marker-end="url(#a)"/><path d="M760 240H827" stroke="url(#g)" stroke-width="3" marker-end="url(#a)"/><text x="55" y="415" fill="#8f9a88" font-family="monospace" font-size="13">DEMO LIMIT: synthetic source video and telemetry. Integrity check ≠ proof of human gameplay or device identity.</text></g></svg>''', encoding="utf-8")

    chart = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 430" role="img" aria-labelledby="t d"><title id="t">Integrity audit versus metadata-only machine learning baseline</title><desc id="d">On this controlled byte-flip dataset, cryptographic audit detects every altered file; the metadata-only logistic baseline cannot distinguish paired files with identical metadata.</desc><rect width="900" height="430" rx="18" fill="#141a15"/><text x="42" y="54" fill="#eaf0e5" font-family="Arial" font-size="22" font-weight="bold">What does each method actually detect?</text><text x="42" y="83" fill="#98a392" font-family="Arial" font-size="13">Controlled, synthetic byte-flip trials · {len(rows)} files · {len(verified_clips)} source clips</text><g font-family="Arial" font-size="13" fill="#c5cebd"><text x="42" y="154">Metadata-only logistic baseline</text><text x="42" y="258">Cryptographic verifier</text></g><rect x="42" y="171" width="610" height="38" rx="6" fill="#263026"/><rect x="42" y="171" width="{610*model_accuracy:.1f}" height="38" rx="6" fill="#d9b85b"/><text x="680" y="198" fill="#f4e7b9" font-family="monospace" font-size="20">{model_accuracy:.0%}</text><rect x="42" y="275" width="610" height="38" rx="6" fill="#263026"/><rect x="42" y="275" width="{610*crypto_accuracy:.1f}" height="38" rx="6" fill="#c6f36b"/><text x="680" y="302" fill="#c6f36b" font-family="monospace" font-size="20">{crypto_accuracy:.0%}</text><text x="42" y="364" fill="#98a392" font-family="Arial" font-size="13">Paired clean/tampered examples have identical codec, dimensions, duration, file size and bitrate.</text><text x="42" y="389" fill="#98a392" font-family="Arial" font-size="13">The ML baseline intentionally excludes the cryptographic result; it is not a deepfake detector.</text></svg>'''
    SITE.joinpath("research_chart.svg").write_text(chart, encoding="utf-8")

    report = f'''# Traceframe research demo report

**Generated:** {demo['created_utc']}  
**Evidence class:** reproducible synthetic engineering demonstration, not real gameplay.

## Research question

Can ordinary media metadata distinguish a valid processed clip from the same clip after a single encoded-payload byte is changed? This controlled experiment compares a metadata-only logistic-regression baseline with the runtime's signed receipt and container-digest audit.

## Workflow demonstrated

1. The featured synthetic test video is represented by a client-signed manifest with a five-link byte-chunk chain. The server recomputes every declared byte range and hash against the uploaded file. Additional dataset samples are outputs accepted by the current host; their original capture source is not attested.
2. The local runtime verifies the submitted source and produces an H.264 portrait MP4. Crop selection method is recorded in the receipt (`{demo['crop_selection_method']}` for this sample); the default is a local frame-difference motion heuristic, not a trained model.
3. The output embeds a server-signed receipt. The untouched sample passed the live verifier and produced digest `{demo['container_sha256']}`.
4. The experiment flips one byte inside the MP4 `mdat` payload. The file remains the same size and retains the same ffprobe codec, dimensions, duration, and bitrate, but the cryptographic auditor rejects it.
5. A small logistic-regression baseline is trained on the generated dataset using only container metadata features (file size, dimensions, duration, bitrate). Data are grouped by source clip for an 80/20 split.

## Results

- Source clips: **{len(verified_clips)}** currently valid generated outputs.
- Paired records: **{len(rows)}** (unaltered and one-byte payload mutation per source).
- Cryptographic audit agreement with the controlled mutation labels: **{crypto_accuracy:.0%}**.
- Metadata-only logistic baseline holdout accuracy: **{model_accuracy:.0%}**.
- Paired metadata vectors identical before/after mutation: **{paired_exact}**.
- Demo output: `{sample.name}`, {features['width']} × {features['height']}, {features['duration_seconds']:.2f} s, {features['codec']}, {features['file_size_bytes']:,} bytes.

The metadata-only model cannot recover information that its input features do not contain. The paired byte-flip examples deliberately have identical metadata, while the digest detects the changed byte. This is an integrity demonstration, **not** a model for human authenticity or AI-generated-video detection.

## Dataset and reproducibility

Download `demo_dataset.csv`. The labels mean `0=unaltered sample accepted by this host`, `1=known one-byte payload mutation`. `crypto_audit_pass` is the actual runtime verifier response. Rows are derived from files in the local `outputs/` folder and regenerated by `python research/build_demo_pack.py` after a successful pipeline run. The featured video and its run telemetry are synthetic; other sample capture origins are not attested. None are represented as verified BGMI/PUBG footage or measured human input.

## Limits and next experiment

The client key is not device enrolled, and telemetry remains client supplied. The byte chain is integrity checked but not bound to decoded-frame timing. The default crop method is a simple local motion heuristic, not ML. Gemini is optional and used only when explicitly selected; this report makes no model-based authenticity claim. AWS S3/Lambda integration is optional, and the project does not mint blockchain transactions. A stronger research test should compare frame-bound event-to-output mappings against C2PA and ordinary file hashing, with replay, frame-reorder, timeline-drift, and crop-transform attacks.

**Conclusion:** this build demonstrates a runnable capture-to-transform-to-audit path and a narrow byte-integrity property. It does not establish gameplay authenticity or a cost-saving percentage.
'''
    SITE.joinpath("research_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(demo, indent=2))

if __name__ == "__main__":
    main()
