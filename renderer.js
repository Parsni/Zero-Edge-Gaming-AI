const screenShareBtn = document.getElementById('screenShareBtn');
const simStreamBtn = document.getElementById('simStreamBtn');
const stopCaptureBtn = document.getElementById('stopCaptureBtn');
const triggerHighlightBtn = document.getElementById('triggerHighlightBtn');
const previewVideo = document.getElementById('previewVideo');
const noStreamOverlay = document.getElementById('noStreamOverlay');
const serverStatus = document.getElementById('serverStatus');
const bufferStats = document.getElementById('bufferStats');
const merkleChainStream = document.getElementById('merkleChainStream');
const merkleRootBadge = document.getElementById('merkleRootBadge');

const mouseVarianceVal = document.getElementById('mouseVarianceVal');
const keystrokeVal = document.getElementById('keystrokeVal');
const jitterVal = document.getElementById('jitterVal');

const resultPanel = document.getElementById('resultPanel');
const outputVideo = document.getElementById('outputVideo');
const downloadLink = document.getElementById('downloadLink');
const certJsonDisplay = document.getElementById('certJsonDisplay');
const sourceVideoFile = document.getElementById('sourceVideoFile');
const rangeStartInput = document.getElementById('rangeStart');
const rangeEndInput = document.getElementById('rangeEnd');
const sourceVideoPreview = document.getElementById('sourceVideoPreview');
const sourceVideoInfo = document.getElementById('sourceVideoInfo');
const processSelectedVideo = document.getElementById('processSelectedVideo');
const selectedVideoStatus = document.getElementById('selectedVideoStatus');
const sampleClipSelect = document.getElementById('sampleClipSelect');
const loadSampleClip = document.getElementById('loadSampleClip');

const forensicDropZone = document.getElementById('forensicDropZone');
const forensicFileInput = document.getElementById('forensicFileInput');
const browseForensicBtn = document.getElementById('browseForensicBtn');
const forensicAuditResult = document.getElementById('forensicAuditResult');
const MIN_CAPTURE_CHUNKS = 1;

const SERVER_BASE = window.location.origin.startsWith('http') ? window.location.origin : 'http://127.0.0.1:8007';
function sizeText(value) { return `${(Number(value) / 1024 / 1024).toFixed(2)} MiB`; }
let selectedSourceUrl = null;
let selectedVideoFile = null;
let activeSuggestion = null;
let verifiedSourceDigest = null;

function chooseSourceVideo(file) {
  selectedVideoFile = file || null;
  activeSuggestion = null;
  verifiedSourceDigest = null;
  processSelectedVideo.disabled = true;
  selectedVideoStatus.textContent = '';
  const suggestionLabel = document.getElementById('activitySuggestion');
  if (suggestionLabel) suggestionLabel.textContent = file ? 'Waiting for video metadata before ranking activity…' : 'The app will recommend a 10-second window after you load a video.';
  if (!file) return;
  if (selectedSourceUrl) URL.revokeObjectURL(selectedSourceUrl);
  selectedSourceUrl = URL.createObjectURL(file);
  sourceVideoPreview.src = selectedSourceUrl;
  sourceVideoPreview.style.display = 'block';
  sourceVideoInfo.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MiB`;
  sourceVideoPreview.onloadedmetadata = async () => {
    if (selectedVideoFile !== file) return;
    rangeStartInput.max = sourceVideoPreview.duration.toFixed(2);
    rangeEndInput.max = sourceVideoPreview.duration.toFixed(2);
    rangeStartInput.value = '0.0';
    rangeEndInput.value = Math.min(10, sourceVideoPreview.duration).toFixed(1);
    sourceVideoInfo.textContent = `${file.name} · ${sourceVideoPreview.videoWidth}×${sourceVideoPreview.videoHeight} · ${sourceVideoPreview.duration.toFixed(1)}s · ${(file.size / 1024 / 1024).toFixed(1)} MiB`;
    await inspectAndRecommend(file);
  };
}

async function inspectAndRecommend(file) {
  const integrityReport = document.getElementById('integrityReport');
  const suggestionLabel = document.getElementById('activitySuggestion');
  if (integrityReport) integrityReport.textContent = 'Step 1/3 · Comparing browser and server SHA-256…';
  if (suggestionLabel) suggestionLabel.textContent = 'Recommendation is locked until the integrity check passes.';
  selectedVideoStatus.textContent = 'Checking file integrity before analyzing the video.';
  processSelectedVideo.disabled = true;
  try {
    const fileBytes = await file.arrayBuffer();
    const clientDigest = await window.captureController.cryptoProvider.sha256Hex(fileBytes);
    const form = new FormData();
    form.append('video', file, file.name);
    form.append('expected_sha256', clientDigest);
    const response = await fetch(`${SERVER_BASE}/inspect-video`, { method: 'POST', body: form });
    const report = await response.json();
    if (!response.ok || report.integrity_passed !== true) throw new Error(report.detail || 'Integrity check failed. Recommendation is blocked.');
    if (selectedVideoFile !== file) return;
    verifiedSourceDigest = report.sha256;
    const integrityLabel = report.prior_receipt_status === 'signed_receipt_verified' ? 'SIGNED RECEIPT VERIFIED' : 'UPLOAD HASH MATCHED';
    if (integrityReport) integrityReport.textContent = `PASS · ${integrityLabel} · SHA-256 ${report.sha256} · ${report.width}×${report.height} · ${Number(report.duration_seconds).toFixed(1)}s · ${sizeText(report.file_size_bytes)} · ${report.video_codec}/${report.pixel_format} · audio ${report.audio_present ? 'present' : 'absent'} · ${report.provenance_note}`;
    if (suggestionLabel) suggestionLabel.textContent = 'Step 2/3 · Integrity passed. Ranking 10-second windows…';
    selectedVideoStatus.textContent = 'Integrity passed. Asking the activity ranker for the strongest candidate window.';

    const analysisForm = new FormData();
    analysisForm.append('video', file, file.name);
    analysisForm.append('expected_sha256', report.sha256);
    const analysisResponse = await fetch(`${SERVER_BASE}/suggest-highlight`, { method: 'POST', body: analysisForm });
    const suggestion = await analysisResponse.json();
    if (!analysisResponse.ok) throw new Error(suggestion.detail || 'Recommendation analysis failed.');
    if (selectedVideoFile !== file || suggestion.source_sha256 !== verifiedSourceDigest) return;
    activeSuggestion = suggestion;
    rangeStartInput.value = Number(suggestion.start_time_seconds).toFixed(1);
    rangeEndInput.value = Number(suggestion.end_time_seconds).toFixed(1);
    const range = `${Number(suggestion.start_time_seconds).toFixed(1)}–${Number(suggestion.end_time_seconds).toFixed(1)}s`;
    if (suggestion.selection_method === 'center_fallback') {
      if (suggestionLabel) suggestionLabel.textContent = `Could not analyze visual activity; selected the center ${Number(suggestion.duration_seconds).toFixed(1)}s as a fallback. ${suggestion.reason}`;
      selectedVideoStatus.textContent = 'Integrity passed. Fallback range selected; you can adjust it before creating the clip.';
    } else {
      const alternatives = (suggestion.alternatives || []).map(item => `${Number(item.start_time_seconds).toFixed(1)}–${Number(item.end_time_seconds).toFixed(1)}s`).join(' and ');
      if (suggestionLabel) suggestionLabel.textContent = `Suggested ${range} · activity rank ${Number(suggestion.activity_score).toFixed(2)} (relative, not probability). ${suggestion.reason}${alternatives ? ` Other high-activity windows: ${alternatives}.` : ''}`;
      selectedVideoStatus.textContent = 'Integrity passed. Automatic suggestion ready; adjust the range if needed, then create the clip.';
    }
    processSelectedVideo.disabled = false;
  } catch (error) {
    if (selectedVideoFile !== file) return;
    activeSuggestion = null;
    if (integrityReport && !verifiedSourceDigest) integrityReport.textContent = `FAIL · ${error.message}`;
    if (suggestionLabel) suggestionLabel.textContent = verifiedSourceDigest ? `Integrity passed, but recommendation failed: ${error.message}` : 'Recommendation blocked because the integrity check did not pass.';
    selectedVideoStatus.textContent = `Workflow stopped: ${error.message}`;
    processSelectedVideo.disabled = true;
  }
}

sourceVideoFile.addEventListener('change', () => chooseSourceVideo(sourceVideoFile.files?.[0]));

loadSampleClip.addEventListener('click', async () => {
  const filename = sampleClipSelect.value;
  if (!filename) return;
  loadSampleClip.disabled = true;
  selectedVideoStatus.textContent = 'Loading the bundled sample…';
  try {
    const response = await fetch(`${SERVER_BASE}/test-videos/${encodeURIComponent(filename)}`);
    if (!response.ok) throw new Error('Could not load the selected sample video.');
    const file = new File([await response.blob()], filename, { type: 'video/mp4' });
    chooseSourceVideo(file);
    selectedVideoStatus.textContent = 'Sample loaded. Automatically ranking candidate moments…';
  } catch (error) {
    selectedVideoStatus.textContent = error.message;
  } finally {
    loadSampleClip.disabled = false;
  }
});

processSelectedVideo.addEventListener('click', async () => {
  const file = selectedVideoFile;
  const start = Number(rangeStartInput.value);
  const end = Number(rangeEndInput.value);
  if (!verifiedSourceDigest || !activeSuggestion) {
    selectedVideoStatus.textContent = 'Cannot create a clip until the SHA-256 integrity check and recommendation both pass.';
    return;
  }
  if (!file || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > sourceVideoPreview.duration) {
    selectedVideoStatus.textContent = 'Choose a video and a valid start/end range inside its duration.';
    return;
  }
  if (file.size > 512 * 1024 * 1024) {
    selectedVideoStatus.textContent = 'This prototype accepts videos up to 512 MiB.';
    return;
  }
  processSelectedVideo.disabled = true;
  processSelectedVideo.textContent = 'Creating suggested clip…';
  selectedVideoStatus.textContent = 'Preparing signed evidence from the exact source file. Longer files take more time to hash.';
  try {
    const proof = await window.captureController.attestUploadedVideo(file, start, end, activeSuggestion, verifiedSourceDigest);
    if (proof.manifest.video_sha256 !== verifiedSourceDigest) throw new Error('Source bytes changed after the integrity check; load the video again.');
    const form = new FormData();
    const ext = file.name.match(/\.[a-z0-9]+$/i)?.[0] || '.mp4';
    form.append('video', new Blob([proof.videoBuffer], { type: file.type || 'application/octet-stream' }), `source${ext}`);
    form.append('manifest', JSON.stringify(proof.manifest));
    form.append('signature', proof.signature);
    form.append('public_key', proof.public_key);
    const response = await fetch(`${SERVER_BASE}/process-clip`, { method: 'POST', body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Processing failed');
    selectedVideoStatus.textContent = 'Clip created. Running the independent receipt and output-byte integrity audit…';
    const outputResponse = await fetch(`${SERVER_BASE}${result.output_url}`, { cache: 'no-store' });
    if (!outputResponse.ok) throw new Error('Could not reload the processed output for its integrity audit.');
    const outputBlob = await outputResponse.blob();
    const auditForm = new FormData();
    auditForm.append('video', outputBlob, result.output_url.split('/').pop());
    const auditResponse = await fetch(`${SERVER_BASE}/verify-clip`, { method: 'POST', body: auditForm });
    const audit = await auditResponse.json();
    if (!auditResponse.ok || audit.forensic_authentic !== true) throw new Error(audit.detail || 'The output failed its signed receipt integrity audit.');
    merkleRootBadge.textContent = `Root: ${proof.manifest.temporal_merkle_root.slice(0, 12)}…`;
    outputVideo.src = `${SERVER_BASE}${result.output_url}`;
    downloadLink.href = `${SERVER_BASE}${result.output_url}`;
    certJsonDisplay.textContent = JSON.stringify({ output_integrity_audit: audit, certificate: result.certificate, aws_archive: result.aws_archive || { status: 'disabled' } }, null, 2);
    resultPanel.classList.add('visible');
    resultPanel.scrollIntoView({ behavior: 'smooth' });
    const media = result.certificate?.output_video || {};
    const source = result.certificate?.source_video || {};
    const sizeText = value => `${(value / 1024 / 1024).toFixed(2)} MiB`;
    const outputBytes = Number.parseInt(media.output_bytes_hex || '0', 16);
    selectedVideoStatus.textContent = `Done. Input SHA-256 passed; output receipt and byte-integrity audit passed. Marked ${start.toFixed(1)}–${end.toFixed(1)}s · ${source.width}×${source.height} preserved at ${media.width}×${media.height} · audio ${media.audio_present ? 'included' : 'not present'} · ${sizeText(media.source_bytes)} → ${sizeText(outputBytes)}. ${media.encoding}.`;
  } catch (error) {
    selectedVideoStatus.textContent = `Could not create the clip: ${error.message}`;
  } finally {
    processSelectedVideo.disabled = false;
    processSelectedVideo.textContent = 'Create Suggested Clip';
  }
});

async function checkServerHealth() {
  try {
    const res = await fetch(`${SERVER_BASE}/health`);
    if (res.ok) {
      serverStatus.querySelector('.status-dot').className = 'status-dot online';
      serverStatus.querySelector('.status-label').textContent = 'FastAPI Gate: Online (200 OK)';
    } else {
      throw new Error();
    }
  } catch {
    serverStatus.querySelector('.status-dot').className = 'status-dot offline';
    serverStatus.querySelector('.status-label').textContent = 'FastAPI Gate: Offline';
  }
}

function onStreamStarted(stream) {
  previewVideo.srcObject = stream;
  noStreamOverlay.style.display = 'none';
  screenShareBtn.style.display = 'none';
  simStreamBtn.style.display = 'none';
  stopCaptureBtn.style.display = 'inline-flex';
  triggerHighlightBtn.disabled = true;

  window.captureController.onBufferUpdate = updateBufferUI;
}

function onStreamStopped() {
  window.captureController.stopCapture();
  previewVideo.srcObject = null;
  noStreamOverlay.style.display = 'flex';
  screenShareBtn.style.display = 'inline-flex';
  simStreamBtn.style.display = 'inline-flex';
  stopCaptureBtn.style.display = 'none';
  triggerHighlightBtn.disabled = true;
  bufferStats.textContent = 'Buffer: 0s / 15s (0 Chunks)';
}

screenShareBtn.addEventListener('click', async () => {
  try {
    const stream = window.electronAPI
      ? await window.captureController.startElectronCapture()
      : await window.captureController.startBrowserCapture();
    onStreamStarted(stream);
  } catch (err) {
    alert(`Capture Error: ${err.message || 'The selected screen or window could not be captured.'}`);
  }
});

simStreamBtn.addEventListener('click', () => {
  const stream = window.captureController.startSyntheticSimulation();
  onStreamStarted(stream);
});

stopCaptureBtn.addEventListener('click', onStreamStopped);

function updateBufferUI(buffer) {
  const len = buffer.length;
  bufferStats.textContent = `Buffer: ${Math.min(len, 15)}s / 15s (${len} Chunks)`;
  triggerHighlightBtn.disabled = len < MIN_CAPTURE_CHUNKS;

  if (len === 0) return;

  const latest = buffer[buffer.length - 1];
  mouseVarianceVal.textContent = latest.entropy.mouse_delta_variance.toFixed(4);
  keystrokeVal.textContent = `${latest.entropy.keystroke_count} / sec`;
  jitterVal.textContent = latest.entropy.frame_jitter_variance_ms.toFixed(4);

  // Render chain stream
  merkleChainStream.innerHTML = '';
  buffer.forEach((c, idx) => {
    const node = document.createElement('div');
    node.className = 'merkle-node';
    node.innerHTML = `
      <div class="node-row">
        <span>Link #${idx + 1} (${new Date(c.timestamp).toLocaleTimeString()})</span>
        <span class="node-hash">${c.chunk_sha256.substring(0, 16)}...</span>
      </div>
      <div class="node-entropy">
        Entropy: ΔVar=${c.entropy.mouse_delta_variance} | Keys=${c.entropy.keystroke_count} | Jitter=${c.entropy.frame_jitter_variance_ms}ms
      </div>
    `;
    merkleChainStream.appendChild(node);
  });
  merkleChainStream.scrollTop = merkleChainStream.scrollHeight;
}

async function triggerHighlightPipeline() {
  if (!window.captureController.isRecording) {
    alert("Please start the screen capture or simulator first.");
    return;
  }
  if (window.captureController.rollingBuffer.length < MIN_CAPTURE_CHUNKS) {
    alert("Capture is starting. Wait until at least one chunk appears in the buffer, then mark the event.");
    return;
  }

  triggerHighlightBtn.disabled = true;
  triggerHighlightBtn.innerHTML = '<span class="btn-icon">⚡</span> Sealing marked segment…';

  try {
    const { videoBlob, manifest, public_key, signature } = await window.captureController.exportAndSignHighlight();

    merkleRootBadge.textContent = `Root: ${manifest.temporal_merkle_root.substring(0, 12)}...`;

    const formData = new FormData();
    formData.append('video', videoBlob, 'capture.webm');
    formData.append('manifest', JSON.stringify(manifest));
    formData.append('signature', signature);
    formData.append('public_key', public_key);

    const res = await fetch(`${SERVER_BASE}/process-clip`, {
      method: 'POST',
      body: formData
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Processing failed');
    }

    const data = await res.json();

    // Display Results
    resultPanel.classList.add('visible');
    outputVideo.src = `${SERVER_BASE}${data.output_url}`;
    downloadLink.href = `${SERVER_BASE}${data.output_url}`;
    certJsonDisplay.textContent = JSON.stringify({
      certificate: data.certificate,
      aws_archive: data.aws_archive || { status: 'disabled' }
    }, null, 2);

    resultPanel.scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    alert(`Zero-Trust Pipeline Error: ${err.message}`);
  } finally {
    triggerHighlightBtn.disabled = window.captureController.rollingBuffer.length < MIN_CAPTURE_CHUNKS;
    triggerHighlightBtn.innerHTML = '<span class="btn-icon">⚡</span> Mark Event & Create Clip (F9)';
  }
}

triggerHighlightBtn.addEventListener('click', triggerHighlightPipeline);

// Listen for F9 keydown in browser window
window.addEventListener('keydown', (e) => {
  if (e.key === 'F9') {
    e.preventDefault();
    triggerHighlightPipeline();
  }
});

if (window.electronAPI && window.electronAPI.onTriggerCapture) {
  window.electronAPI.onTriggerCapture(triggerHighlightPipeline);
}

// Forensic File Verification
browseForensicBtn.addEventListener('click', () => forensicFileInput.click());
forensicFileInput.addEventListener('change', (e) => {
  if (e.target.files.length > 0) {
    performForensicAudit(e.target.files[0]);
  }
});

forensicDropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  forensicDropZone.classList.add('dragover');
});

forensicDropZone.addEventListener('dragleave', () => {
  forensicDropZone.classList.remove('dragover');
});

forensicDropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  forensicDropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    performForensicAudit(e.dataTransfer.files[0]);
  }
});

async function performForensicAudit(file) {
  forensicAuditResult.textContent = 'Auditing cryptographic seals...';

  const formData = new FormData();
  formData.append('video', file);

  try {
    const res = await fetch(`${SERVER_BASE}/verify-clip`, {
      method: 'POST',
      body: formData
    });

    const data = await res.json();

    if (res.ok && data.forensic_authentic) {
      const result = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = '✔ RECEIPT AUTHENTICATED';
      result.appendChild(title);
      for (const [label, value] of [
        ['Container digest', data.output_container_sha256],
        ['Source SHA-256', data.edge_video_sha256],
        ['Crop parameters', JSON.stringify(data.ai_crop_box)],
        ['Status', data.message]
      ]) {
        const row = document.createElement('div');
        row.textContent = `${label}: ${value ?? 'unavailable'}`;
        result.appendChild(row);
      }
      forensicAuditResult.replaceChildren(result);
    } else {
      const detail = data.detail || 'The signed receipt could not be verified.';
      const result = document.createElement('div');
      const title = document.createElement('strong');
      const explanation = document.createElement('div');
      if (/no cryptographic provenance certificate found/i.test(detail)) {
        title.textContent = 'ℹ NO TRACEFRAME RECEIPT';
        explanation.textContent = 'This source video has no Traceframe receipt, so this receipt auditor cannot authenticate it. Use the upload integrity check above to compare its SHA-256 hash.';
      } else {
        title.textContent = '✖ SIGNED RECEIPT CHECK FAILED';
        explanation.textContent = detail;
      }
      result.append(title, explanation);
      forensicAuditResult.replaceChildren(result);
      return;
    }
  } catch (err) {
    const result = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = '✖ RECEIPT REJECTED';
    const detail = document.createElement('div');
    detail.textContent = err.message;
    result.append(title, detail);
    forensicAuditResult.replaceChildren(result);
  }
}

// Check server status immediately
checkServerHealth();
setInterval(checkServerHealth, 5000);
