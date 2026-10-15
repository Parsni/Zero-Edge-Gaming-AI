const healthDot = document.querySelector('#health-dot');
const healthText = document.querySelector('#health-text');
const healthTime = document.querySelector('#health-time');
const serviceLabel = document.querySelector('#audit-service');
const fileInput = document.querySelector('#clip-file');
const dropZone = document.querySelector('#drop-zone');
const result = document.querySelector('#audit-result');

async function refreshHealth() {
  try {
    const response = await fetch('/health', { cache: 'no-store' });
    if (!response.ok) throw new Error('Runtime is not responding');
    const data = await response.json();
    healthDot.className = 'status-dot online';
    healthText.textContent = data.service + ' · online';
    healthTime.textContent = new Date().toLocaleTimeString();
    serviceLabel.textContent = 'ONLINE';
    document.querySelector('.live-tag i').classList.add('online');
    const awsStatus = document.querySelector('#aws-status');
    if (awsStatus) {
      awsStatus.textContent = data.aws_archive?.configured ? 'S3 READY ↗' : 'S3 NOT CONFIGURED';
      awsStatus.setAttribute('aria-label', data.aws_archive?.configured ? 'Amazon S3 archiving is configured' : 'Amazon S3 archiving is not configured');
    }
  } catch {
    healthDot.className = 'status-dot offline';
    healthText.textContent = 'Local runtime unavailable';
    healthTime.textContent = '';
    serviceLabel.textContent = 'OFFLINE';
    document.querySelector('.live-tag i').classList.remove('online');
    const awsStatus = document.querySelector('#aws-status');
    if (awsStatus) awsStatus.textContent = 'RUNTIME OFFLINE';
  }
}

function showResult(title, detail, ok) {
  result.replaceChildren();
  const box = document.createElement('div');
  box.className = 'result-box ' + (ok ? 'good' : 'bad');
  const heading = document.createElement('span');
  heading.className = 'result-title';
  heading.textContent = title;
  const message = document.createElement('span');
  message.textContent = detail;
  box.append(heading, message);
  result.append(box);
}

async function hasActiveSession() {
  try {
    const response = await fetch('/auth/me', {cache:'no-store'});
    return Boolean(response.ok && (await response.json()).authenticated);
  } catch { return false; }
}

async function audit(file) {
  if (!file) return;
  if (!await hasActiveSession()) {
    showResult('SIGN IN REQUIRED', 'The demo is public. Sign in to upload a file to the live auditor.', false);
    return;
  }
  if (!file.name.toLowerCase().endsWith('.mp4') && file.type !== 'video/mp4') {
    showResult('FILE NOT ACCEPTED', 'Choose an MP4 file.', false);
    return;
  }
  showResult('CHECKING CERTIFICATE…', 'Uploading to this local runtime’s verifier.', true);
  const form = new FormData();
  form.append('video', file, file.name);
  try {
    const response = await fetch('/verify-clip', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The verifier rejected this file.');
    showResult('SIGNATURE + CONTAINER DIGEST VALID', 'Crop method: ' + (data.crop_selection_method || 'legacy unknown') + ' · SHA-256 ' + data.output_container_sha256 + ' · Receipt issued ' + new Date(data.issued_at * 1000).toLocaleString(), true);
  } catch (error) {
    showResult('AUDIT REJECTED', error.message || 'The runtime could not verify this file.', false);
  }
}

fileInput.addEventListener('change', (event) => audit(event.target.files?.[0]));
for (const type of ['dragenter', 'dragover']) {
  dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.add('dragover');
  });
}
for (const type of ['dragleave', 'drop']) {
  dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    dropZone.classList.remove('dragover');
  });
}
dropZone.addEventListener('drop', (event) => audit(event.dataTransfer?.files?.[0]));
refreshHealth();
setInterval(refreshHealth, 15000);

const demoVideo = document.querySelector('#demo-video');
const demoStatus = document.querySelector('#demo-result');
let demoArtifactUrl = '';
function showDemoStatus(message, state='') {
  demoStatus.textContent = message;
  demoStatus.className = `demo-result ${state}`.trim();
}
async function loadDemoPack() {
  try {
    const response = await fetch('/site/demo_data.json', {cache:'no-store'});
    if (!response.ok) throw new Error('Demo pack is not available yet.');
    const data = await response.json();
    demoArtifactUrl = '/demo-video';
    demoVideo.src = demoArtifactUrl;
    document.querySelector('#download-demo').href = demoArtifactUrl;
    document.querySelector('#demo-resolution').textContent = `${data.width}×${data.height}`;
    document.querySelector('#demo-duration').textContent = Number(data.duration_seconds).toFixed(2);
    document.querySelector('#demo-size').textContent = `${(data.verified_video_bytes / 1048576).toFixed(2)} MB`;
    document.querySelector('#demo-links').textContent = data.chain_length ?? '—';
    document.querySelector('#demo-crop-method').textContent = data.crop_selection_method || 'unknown';
    document.querySelector('#demo-digest').textContent = data.container_sha256;
    const proofLinks = document.querySelector('#proof-links');
    const proofResolution = document.querySelector('#proof-resolution');
    const proofDuration = document.querySelector('#proof-duration');
    const proofSize = document.querySelector('#proof-size');
    if (proofLinks) proofLinks.textContent = data.chain_length ?? '—';
    if (proofResolution) proofResolution.textContent = `${data.width}×${data.height}`;
    if (proofDuration) proofDuration.textContent = Number(data.duration_seconds).toFixed(2);
    if (proofSize) proofSize.textContent = `${(data.verified_video_bytes / 1048576).toFixed(2)} MB`;
    showDemoStatus(`Loaded ${data.video}. Crop method: ${data.crop_selection_method || 'unknown'}. Synthetic test footage; browser activity is not proof of gameplay or identity.`, 'good');
  } catch (error) { showDemoStatus(error.message, 'bad'); }
}
async function submitDemoFile(file) {
  const form = new FormData();
  form.append('video', file, file.name || 'traceframe-demo.mp4');
  const response = await fetch('/verify-clip', {method:'POST', body:form});
  let data = {};
  try { data = await response.json(); } catch {}
  return {response, data};
}
document.querySelector('#verify-demo').addEventListener('click', async () => {
  if (!await hasActiveSession()) { showDemoStatus('Sign in to run the live receipt verification.', 'bad'); return; }
  showDemoStatus('Sending the untouched demo to the live receipt verifier…');
  try {
    const response = await fetch(demoArtifactUrl, {cache:'no-store'});
    if (!response.ok) throw new Error('Could not load the demo MP4.');
    const file = new File([await response.blob()], 'traceframe-demo.mp4', {type:'video/mp4'});
    const {response: auditResponse, data} = await submitDemoFile(file);
    if (!auditResponse.ok) throw new Error(data.detail || 'The verifier rejected the demo.');
    showDemoStatus(`PASS · server signature and MP4 container digest verified · SHA-256 ${data.output_container_sha256}`, 'good');
  } catch (error) { showDemoStatus(`ERROR · ${error.message}`, 'bad'); }
});
document.querySelector('#tamper-demo').addEventListener('click', async () => {
  if (!await hasActiveSession()) { showDemoStatus('Sign in to run the live tamper check.', 'bad'); return; }
  showDemoStatus('Flipping one byte inside the encoded video payload, then submitting it to the live verifier…');
  try {
    const response = await fetch(demoArtifactUrl, {cache:'no-store'});
    if (!response.ok) throw new Error('Could not load the demo MP4.');
    const bytes = new Uint8Array(await response.arrayBuffer());
    let mdat = -1;
    for (let i=0; i<bytes.length-4; i++) {
      if (bytes[i]===109 && bytes[i+1]===100 && bytes[i+2]===97 && bytes[i+3]===116) { mdat=i; break; }
    }
    if (mdat < 0 || mdat + 1028 >= bytes.length) throw new Error('Could not locate a safe sample payload byte.');
    bytes[mdat+1028] ^= 1;
    const file = new File([bytes], 'traceframe-demo-tampered.mp4', {type:'video/mp4'});
    const {response:auditResponse, data} = await submitDemoFile(file);
    if (auditResponse.ok) throw new Error('Unexpected result: the verifier accepted a byte-modified file.');
    showDemoStatus(`EXPECTED REJECTION · the one-byte payload change failed verification · ${data.detail || `HTTP ${auditResponse.status}`}`, 'good');
  } catch (error) { showDemoStatus(`ERROR · ${error.message}`, 'bad'); }
});
loadDemoPack();
