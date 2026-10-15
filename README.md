# Traceframe

Traceframe is a local research prototype for capturing gameplay video, processing a highlight, and checking an embedded provenance receipt. It does not prove that a human or enrolled device made the recording.

## Run the demo

Requirements: Python 3.10+, FFmpeg and FFprobe on PATH, and a current browser. Node.js is needed only for the optional Electron desktop shell.

~~~powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\venv\Scripts\python.exe run_demo.py
~~~

The demo starts a compatible local API if one is not already running. It checks the runtime build before reuse and selects an available local port if the default port is occupied by another service. It uses an FFmpeg-generated test clip and explicitly marked synthetic input; it is a pipeline demonstration, not proof of real gameplay capture. The output video and its companion receipt are written under outputs/. Crop selection uses a local motion heuristic by default and does not need Gemini or network access. Gemini is optional and is used only when `CROP_PROVIDER=gemini` is explicitly set; the local method is the fallback.

The first run of `python run_demo.py` asks you to create the initial administrator account; later runs ask you to sign in. On success it refreshes the reproducible research pack in `site/`: a generated CSV dataset, architecture graphic, metadata-only logistic-regression baseline chart, and a Markdown experiment report. All demo clips and controlled tampering trials are synthetic. The report records that they do not prove a human played the game or detect AI-generated video.

Open the dashboard at the URL printed by the demo. To start the site without running the sample pipeline:

~~~powershell
venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8007
~~~

## Run the real-video faculty demo

Launch the browser workflow with `.\start.ps1`, then sign in. Under **Verify, recommend, and preserve**, load an MP4 or bundled sample. The browser and server first compare the file SHA-256; only a match unlocks recommendation. A signed prior receipt is verified when present. For a new unsigned video, this establishes an upload baseline, not creator authenticity. Then Traceframe samples frames at 2 fps, measures low-resolution frame-to-frame visual change, and compares half-second audio RMS levels to the clip median. It scores 10-second windows using 75% normalized visual change and 25% audio prominence, selects the top window, and displays its range and relative score before processing. This deterministic activity heuristic is not a learned model or semantic kill/clutch/goal detector; loud music and scene transitions can rank highly. You can adjust the suggested range before creating the clip. The service signs the exact uploaded source bytes, cuts the selected time range, retains the full frame and audio, and embeds a signed receipt. Supported full-frame YUV formats are encoded at H.264 QP 0, preserving decoded video pixels exactly but often increasing file size; audio is re-encoded as AAC. Use the receipt auditor to verify the output, then alter a copy to show that verification rejects a changed file.

For full-frame video in a supported YUV pixel format, the output uses H.264 QP 0 and preserves decoded video pixels exactly; the trade-off is a potentially much larger file. Audio is re-encoded as AAC. Other pixel formats or cropped output use the labeled lossy fallback. Before recommendation, the browser and server compare SHA-256 digests. A matching digest verifies the uploaded bytes and subsequent processing input, but does not authenticate the original creator unless a prior trusted receipt exists. The activity score is a relative rank, not a probability or a guarantee of significance. The receipt records the suggested range and scoring method; it does not prove a human/device recorded the source. The receipt reports source/output dimensions, audio presence, duration, and byte sizes so you can explain exactly what changed.

## Run live screen capture

Launch the browser capture workflow:

~~~powershell
.\start.ps1
~~~

The live stream controls remain available for a short operator-marked rolling segment. For a faculty presentation that needs a specific event, use the real-video workflow above so you choose the exact time range. Synthetic demo footage is for pipeline checks only. Browser-reported environment details are self-reported and do not identify or attest a device.

The Electron desktop shell is optional and is not required for the browser workflow. To install its larger runtime only when needed, run `npm run desktop:setup`; then use `npm run desktop`.

## Optional AWS S3 archive

Processed clips and their JSON provenance receipts can be archived to Amazon S3 after local processing. The feature is disabled by default. Install dependencies with `python -m pip install -r requirements.txt`, then set `AWS_ARCHIVE_ENABLED=true`, `AWS_S3_BUCKET`, and optionally `AWS_REGION` and `AWS_S3_PREFIX` in `.env`. Configure credentials through the standard AWS credential chain (for example, an IAM role or `aws configure`); do not put AWS secrets in the site or commit them. Restart the runtime after changing settings.

When enabled and configured, each successful `/process-clip` operation uploads both output files and returns their S3 object keys and SHA-256 checksums in `aws_archive`. Optionally set `AWS_LAMBDA_FUNCTION` to asynchronously invoke an existing Lambda after both objects upload; the event contains the bucket, region, object keys, and hashes. Grant the runtime identity `s3:PutObject` and, when using the hook, `lambda:InvokeFunction` for only the required resources. The `/health` response reports whether archiving and the hook are configured without exposing resource names. S3 upload errors are returned in the processing response while keeping the local output available. This prototype does not configure bucket policies, versioning, Object Lock, encryption, retention, or access control; set those controls in AWS before using sensitive media.

## What the audit means

The API checks the input file hash, the client-supplied signed manifest, and its linked chain before processing. The output certificate is signed with a persistent local server key. The audit endpoint checks that signature against the server's own key and recomputes a SHA-256 digest of the MP4 with the embedded certificate JSON bytes masked. This covers the remaining container bytes, including the encoded media and trailing bytes.

The client signing key is ephemeral and is not enrolled to a person or device. The client supplies telemetry and chunk boundaries; the server recomputes each signed chunk hash against the uploaded bytes, but chunk timing is not tied to decoded frames. A valid receipt therefore proves that the bytes and receipt match the current local server's issuance; it does not prove capture identity, human play, or real-world truth. Video uploads are capped at 512 MiB.

## Share with devices on the same Wi-Fi

For trusted private networks, use [LAN_SHARING.md](LAN_SHARING.md). It starts an HTTPS endpoint so browser screen capture works on other devices. User accounts are required for the workspace and its processing/audit routes; do not expose this prototype to public networks or forward its port through your router.

## Accounts and administrator

The first account registered on a fresh host becomes the administrator. Later registrations receive standard user access. The administrator can view accounts, promote or demote administrators, disable or enable accounts, and delete accounts from the login page. Passwords are stored as salted scrypt hashes in the local SQLite database `.traceframe-auth.sqlite3`; session cookies are HttpOnly and expire after seven days. Back up this database securely if you need to preserve accounts. Each device reaching a shared host uses the same account database. Keep registration access limited to your trusted LAN because the prototype does not have email verification or account recovery.

## Security and deployment boundary

The API binds to loopback by default and rejects cross-origin browser writes. The optional LAN launcher uses HTTPS and local username/password accounts, but it has no rate or upload-size limits, email verification, or account recovery; do not expose it to a public network as-is. The local signing key and account database are excluded from Git. Back them up securely if existing certificates and accounts need to remain verifiable after moving the project.
