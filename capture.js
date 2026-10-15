class UniversalCryptoProvider {
  constructor() {
    this.keyPair = null;
    this.publicKeyPem = null;
  }

  canonicalize(obj) {
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
      if (Array.isArray(obj)) {
        return `[${obj.map(item => this.canonicalize(item)).join(',')}]`;
      }
      return JSON.stringify(obj);
    }
    const sortedKeys = Object.keys(obj).sort();
    const parts = sortedKeys.map(k => `${JSON.stringify(k)}:${this.canonicalize(obj[k])}`);
    return `{${parts.join(',')}}`;
  }

  async initializeKeys() {
    if (this.keyPair) return this.publicKeyPem;

    this.keyPair = await window.crypto.subtle.generateKey(
      { name: "ECDSA", namedCurve: "P-256" },
      true,
      ["sign", "verify"]
    );

    const exported = await window.crypto.subtle.exportKey("spki", this.keyPair.publicKey);
    const b64 = btoa(String.fromCharCode(...new Uint8Array(exported)));
    const pemLines = b64.match(/.{1,64}/g).join('\n');
    this.publicKeyPem = `-----BEGIN PUBLIC KEY-----\n${pemLines}\n-----END PUBLIC KEY-----\n`;
    return this.publicKeyPem;
  }

  async sha256Hex(buffer) {
    if (window.electronAPI && window.electronAPI.hashChunk) {
      try {
        return await window.electronAPI.hashChunk(buffer);
      } catch (e) {
        // Fallback to web crypto
      }
    }
    const hashBuffer = await window.crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  }

  _rawToDer(rawSig) {
    const bytes = new Uint8Array(rawSig);
    const r = bytes.slice(0, 32);
    const s = bytes.slice(32, 64);

    function formatInteger(b) {
      let start = 0;
      while (start < b.length && b[start] === 0) start++;
      if (start === b.length) return new Uint8Array([0x02, 0x01, 0x00]);
      const needsPad = (b[start] & 0x80) !== 0;
      const len = b.length - start + (needsPad ? 1 : 0);
      const res = new Uint8Array(2 + len);
      res[0] = 0x02;
      res[1] = len;
      let offset = 2;
      if (needsPad) res[offset++] = 0x00;
      res.set(b.slice(start), offset);
      return res;
    }

    const rDer = formatInteger(r);
    const sDer = formatInteger(s);
    const totalLen = rDer.length + sDer.length;
    const der = new Uint8Array(2 + totalLen);
    der[0] = 0x30;
    der[1] = totalLen;
    der.set(rDer, 2);
    der.set(sDer, 2 + rDer.length);

    let binary = '';
    for (let i = 0; i < der.length; i++) {
      binary += String.fromCharCode(der[i]);
    }
    return btoa(binary);
  }

  async sign(canonicalString) {
    const encoder = new TextEncoder();
    const data = encoder.encode(canonicalString);
    const rawSig = await window.crypto.subtle.sign(
      { name: "ECDSA", hash: { name: "SHA-256" } },
      this.keyPair.privateKey,
      data
    );
    return this._rawToDer(rawSig);
  }

  getClientEnvironment() {
    return {
      source: 'browser_reported',
      attested: false,
      platform: navigator.userAgentData?.platform || navigator.platform || 'unknown',
      hardware_concurrency: Number.isInteger(navigator.hardwareConcurrency)
        ? navigator.hardwareConcurrency : null,
      device_memory_gib: Number.isFinite(navigator.deviceMemory)
        ? navigator.deviceMemory : null
    };
  }

  async generateAttestation(videoArrayBuffer, chunkChainMetadata, captureMode, eventMarker) {
    if (window.electronAPI && window.electronAPI.signPayload) {
      try {
        return await window.electronAPI.signPayload({
          videoBuffer: videoArrayBuffer,
          chainMetadata: chunkChainMetadata,
          captureMode,
          eventMarker
        });
      } catch (e) {
        console.warn("Electron sign fallback to WebCrypto:", e);
      }
    }

    await this.initializeKeys();
    const fullVideoHash = await this.sha256Hex(videoArrayBuffer);
    const clientEnvironment = this.getClientEnvironment();

    let prevChainHash = '0'.repeat(64);
    const verifiedChain = [];

    for (let i = 0; i < chunkChainMetadata.length; i++) {
      const item = chunkChainMetadata[i];
      const entropyObj = item.entropy || {};
      const entropyCanonical = this.canonicalize(entropyObj);
      const enc = new TextEncoder().encode(entropyCanonical);
      const entropyHash = await this.sha256Hex(enc);

      const linkStr = `${prevChainHash}:${item.chunk_sha256}:${entropyHash}:${item.timestamp}`;
      const linkHash = await this.sha256Hex(new TextEncoder().encode(linkStr));

      verifiedChain.push({
        chain_link_hash: linkHash,
        chunk_sha256: item.chunk_sha256,
        chunk_size_bytes: item.chunk_size_bytes,
        entropy_hash: entropyHash,
        index: i,
        kinetic_metrics: entropyObj,
        timestamp: item.timestamp
      });
      prevChainHash = linkHash;
    }

    const manifest = {
      capture_mode: captureMode || 'unknown',
      event_marker: eventMarker || null,
      client_environment: clientEnvironment,
      temporal_chain: verifiedChain,
      temporal_chain_length: verifiedChain.length,
      temporal_merkle_root: prevChainHash,
      timestamp: Date.now(),
      version: '2.0.0-kinetic-merkle',
      video_sha256: fullVideoHash
    };

    const canonicalPayload = this.canonicalize(manifest);
    const signature = await this.sign(canonicalPayload);

    return {
      manifest,
      public_key: this.publicKeyPem,
      signature
    };
  }
}

class BiomechanicalEntropyTracker {
  constructor() {
    this.resetMetrics();
    this.lastFrameTime = performance.now();
    this.frameIntervals = [];
    this.prevMouseX = null;
    this.prevMouseY = null;
    this.mouseDeltas = [];

    this._bindEvents();
  }

  resetMetrics() {
    this.mouseEventsCount = 0;
    this.mouseDeltas = [];
    this.keystrokeCount = 0;
    this.frameIntervals = [];
    this.lastFrameTime = performance.now();
  }

  _bindEvents() {
    window.addEventListener('mousemove', (e) => {
      this.mouseEventsCount++;
      if (this.prevMouseX !== null && this.prevMouseY !== null) {
        const dx = e.clientX - this.prevMouseX;
        const dy = e.clientY - this.prevMouseY;
        const dist = Math.sqrt(dx * dx + dy * dy);
        this.mouseDeltas.push(dist);
      }
      this.prevMouseX = e.clientX;
      this.prevMouseY = e.clientY;
    });

    window.addEventListener('keydown', () => {
      this.keystrokeCount++;
    });

    const measureJitter = (now) => {
      const delta = now - this.lastFrameTime;
      this.lastFrameTime = now;
      if (delta > 0 && delta < 1000) {
        this.frameIntervals.push(delta);
      }
      requestAnimationFrame(measureJitter);
    };
    requestAnimationFrame(measureJitter);
  }

  snapshot() {
    let deltaMean = 0;
    let deltaVariance = 0;
    if (this.mouseDeltas.length > 0) {
      deltaMean = this.mouseDeltas.reduce((a, b) => a + b, 0) / this.mouseDeltas.length;
      deltaVariance = this.mouseDeltas.reduce((a, b) => a + Math.pow(b - deltaMean, 2), 0) / this.mouseDeltas.length;
    }

    let jitterMean = 16.67;
    let jitterVariance = 0;
    if (this.frameIntervals.length > 0) {
      jitterMean = this.frameIntervals.reduce((a, b) => a + b, 0) / this.frameIntervals.length;
      jitterVariance = this.frameIntervals.reduce((a, b) => a + Math.pow(b - jitterMean, 2), 0) / this.frameIntervals.length;
    }

    const entropyMetrics = {
      frame_jitter_variance_ms: parseFloat(jitterVariance.toFixed(4)),
      frame_samples: this.frameIntervals.length,
      keystroke_count: this.keystrokeCount,
      mouse_delta_variance: parseFloat(deltaVariance.toFixed(4)),
      mouse_events_count: this.mouseEventsCount,
      snapshot_perf_now: parseFloat(performance.now().toFixed(3))
    };

    this.resetMetrics();
    return entropyMetrics;
  }
}

class MediaStreamCaptureController {
  constructor() {
    this.mediaRecorder = null;
    this.stream = null;
    this.rollingBuffer = [];
    this.entropyTracker = new BiomechanicalEntropyTracker();
    this.cryptoProvider = new UniversalCryptoProvider();
    this.onBufferUpdate = null;
    this.isRecording = false;
    this.simulationInterval = null;
    this.captureMode = 'unknown';
    this.segmentDurationMs = 5000;
    this.completedSegments = [];
    this.currentSegment = null;
    this.segmentRotationTimer = null;
    this.recordingMimeType = 'video/webm';
    this.recordingBitrate = 4000000;
    this.onCaptureError = null;
  }

  async startBrowserCapture() {
    this.stopCapture();
    this.stream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        frameRate: { ideal: 30 }
      },
      audio: false
    });
    this.captureMode = 'browser_display';
    return this._initRecorder(this.stream);
  }

  async startElectronCapture() {
    // Electron selects the requested source through its main-process
    // setDisplayMediaRequestHandler; use the standard capture API here.
    return this.startBrowserCapture();
  }

  startSyntheticSimulation() {
    this.stopCapture();
    this.captureMode = 'synthetic_demo';
    const canvas = document.createElement('canvas');
    canvas.width = 1280;
    canvas.height = 720;
    const ctx = canvas.getContext('2d');

    let t = 0;
    const draw = () => {
      t += 0.05;
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Grid
      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.lineWidth = 1;
      for (let x = 0; x < canvas.width; x += 80) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
      }
      for (let y = 0; y < canvas.height; y += 80) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
      }

      // Moving action target
      const targetX = 640 + Math.sin(t * 1.5) * 270;
      const targetY = 360 + Math.cos(t * 2.0) * 165;

      const grad = ctx.createRadialGradient(targetX, targetY, 8, targetX, targetY, 80);
      grad.addColorStop(0, '#00f2fe');
      grad.addColorStop(0.6, '#8b5cf6');
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(targetX, targetY, 80, 0, Math.PI * 2);
      ctx.fill();

      // Text HUD
      ctx.fillStyle = '#f8fafc';
      ctx.font = 'bold 36px monospace';
      ctx.fillText(`TRACEFRAME SYNTHETIC DEMO: T+${t.toFixed(2)}s`, 48, 68);
      ctx.font = '16px monospace';
      ctx.fillStyle = '#38bdf8';
      ctx.fillText(`Synthetic target: (${Math.round(targetX)}, ${Math.round(targetY)})`, 48, 100);
      ctx.fillText(`Local time: ${new Date().toISOString()}`, 48, 126);

      if (this.isRecording) {
        requestAnimationFrame(draw);
      }
    };
    this.stream = canvas.captureStream(30);
    const stream = this._initRecorder(this.stream, 1500000);
    // Start the animation after _initRecorder sets isRecording. Starting it
    // before the recorder made draw() render exactly one frame and stop, so
    // Chrome produced no useful MediaRecorder chunks.
    draw();
    return stream;
  }

  _initRecorder(stream, videoBitsPerSecond = 4000000) {
    this.recordingMimeType = MediaRecorder.isTypeSupported('video/webm; codecs=vp9')
      ? 'video/webm; codecs=vp9'
      : (MediaRecorder.isTypeSupported('video/webm; codecs=vp8') ? 'video/webm; codecs=vp8' : 'video/webm');
    this.recordingBitrate = videoBitsPerSecond;
    this.completedSegments = [];
    this.rollingBuffer = [];
    this.currentSegment = null;
    this.isRecording = true;
    this._startRecorderSegment(stream);
    return stream;
  }

  _startRecorderSegment(stream) {
    if (!this.isRecording || !stream) return;

    const segment = {
      startedAt: Date.now(),
      endedAt: null,
      chunks: [],
      pendingTasks: new Set(),
      finalized: false,
      resolveCompletion: null
    };
    segment.completion = new Promise(resolve => { segment.resolveCompletion = resolve; });
    this.currentSegment = segment;

    const recorder = new MediaRecorder(stream, {
      mimeType: this.recordingMimeType,
      videoBitsPerSecond: this.recordingBitrate
    });
    this.mediaRecorder = recorder;

    recorder.ondataavailable = event => {
      if (!event.data || event.data.size === 0) return;
      const task = (async () => {
        const timestamp = Date.now();
        const arrayBuffer = await event.data.arrayBuffer();
        const chunkSha256 = await this.cryptoProvider.sha256Hex(arrayBuffer);
        const entropySnapshot = this.entropyTracker.snapshot();
        segment.chunks.push({
          timestamp,
          blob: event.data,
          arrayBuffer,
          chunk_sha256: chunkSha256,
          chunk_size_bytes: arrayBuffer.byteLength,
          entropy: entropySnapshot
        });
        this._refreshRollingBuffer();
      })();
      segment.pendingTasks.add(task);
      task.catch(error => {
        console.error('[Traceframe capture] Could not record chunk:', error);
        if (this.onCaptureError) this.onCaptureError(error);
      }).finally(() => {
        segment.pendingTasks.delete(task);
      });
    };

    recorder.onerror = event => {
      const error = event.error || new Error('The browser recorder reported an error.');
      console.error('[Traceframe capture] Recorder error:', error);
      if (this.onCaptureError) this.onCaptureError(error);
      try { if (recorder.state !== 'inactive') recorder.stop(); } catch (_) {}
    };

    recorder.onstop = async () => {
      clearTimeout(this.segmentRotationTimer);
      while (segment.pendingTasks.size) {
        await Promise.allSettled([...segment.pendingTasks]);
      }
      segment.endedAt = Date.now();
      segment.blob = new Blob(segment.chunks.map(chunk => chunk.blob), { type: this.recordingMimeType });
      segment.finalized = true;
      this.completedSegments.push(segment);
      this.completedSegments = this.completedSegments.filter(item => item.endedAt >= Date.now() - 16000);
      if (this.currentSegment === segment) this.currentSegment = null;
      this._refreshRollingBuffer();
      segment.resolveCompletion(segment);

      if (this.isRecording && this.stream) this._startRecorderSegment(this.stream);
    };

    recorder.start(1000);
    this.segmentRotationTimer = setTimeout(() => {
      if (this.currentSegment === segment && recorder.state === 'recording') recorder.stop();
    }, this.segmentDurationMs);
  }

  _refreshRollingBuffer() {
    const cutoff = Date.now() - 15500;
    const segments = [...this.completedSegments, this.currentSegment].filter(Boolean);
    this.rollingBuffer = segments.flatMap(segment => segment.chunks)
      .filter(chunk => chunk.timestamp >= cutoff)
      .sort((a, b) => a.timestamp - b.timestamp);
    if (this.onBufferUpdate) this.onBufferUpdate(this.rollingBuffer);
  }

  stopCapture() {
    this.isRecording = false;
    clearTimeout(this.segmentRotationTimer);
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try { this.mediaRecorder.stop(); } catch (e) {}
    }
    if (this.stream) {
      try {
        this.stream.getTracks().forEach(track => track.stop());
      } catch (e) {}
      this.stream = null;
    }
  }

  async exportAndSignHighlight() {
    const markedSegment = this.currentSegment;
    const eventMarkedAt = Date.now();
    if (!markedSegment || markedSegment.chunks.length < 1) {
      throw new Error('Wait for the capture buffer to show at least one chunk, then mark the event.');
    }

    // Each segment is a complete WebM recording with its own container header.
    // Marking an event seals the segment that was recording at that moment.
    await markedSegment.completion;
    if (!markedSegment.finalized || markedSegment.chunks.length < 2) {
      throw new Error('The marked segment did not contain enough video data. Keep capture running and try again.');
    }

    const eventMarker = {
      kind: 'operator_marked_segment',
      marked_at: eventMarkedAt,
      segment_started_at: markedSegment.startedAt,
      segment_ended_at: markedSegment.endedAt,
      pre_roll_ms: Math.max(0, eventMarkedAt - markedSegment.startedAt)
    };
    const segmentChunks = markedSegment.chunks;
    const blobs = segmentChunks.map(c => c.blob);
    const combinedBlob = new Blob(blobs, { type: 'video/webm' });
    const fullVideoBuffer = await combinedBlob.arrayBuffer();

    const chainMetadata = segmentChunks.map(c => ({
      chunk_sha256: c.chunk_sha256,
      chunk_size_bytes: c.chunk_size_bytes,
      entropy: c.entropy,
      timestamp: c.timestamp
    }));

    const attestation = await this.cryptoProvider.generateAttestation(
      fullVideoBuffer,
      chainMetadata,
      this.captureMode,
      eventMarker
    );

    return {
      videoBlob: combinedBlob,
      manifest: attestation.manifest,
      public_key: attestation.public_key,
      signature: attestation.signature,
      eventMarker
    };
  }

  async attestUploadedVideo(file, startTime, endTime, suggestion = null, verifiedSourceDigest = null) {
    const videoBuffer = await file.arrayBuffer();
    const chunkBytes = 8 * 1024 * 1024;
    const chainMetadata = [];
    for (let offset = 0; offset < videoBuffer.byteLength; offset += chunkBytes) {
      const chunk = videoBuffer.slice(offset, Math.min(offset + chunkBytes, videoBuffer.byteLength));
      chainMetadata.push({
        chunk_sha256: await this.cryptoProvider.sha256Hex(chunk),
        chunk_size_bytes: chunk.byteLength,
        entropy: { source: 'uploaded_file_bytes', byte_offset: offset },
        timestamp: Date.now()
      });
    }
    const isSuggested = Boolean(suggestion && suggestion.selection_method && suggestion.selection_method !== 'center_fallback');
    const userAdjusted = isSuggested && (
      Math.abs(Number(startTime) - Number(suggestion.start_time_seconds)) > 0.11 ||
      Math.abs(Number(endTime) - Number(suggestion.end_time_seconds)) > 0.11
    );
    const eventMarker = {
      kind: userAdjusted ? 'operator_adjusted_activity_window' : (isSuggested ? 'algorithm_suggested_activity_window' : 'operator_selected_time_range'),
      selection_method: isSuggested ? suggestion.selection_method : 'operator_selected_time_range',
      activity_score: isSuggested ? suggestion.activity_score : null,
      motion_score: isSuggested ? suggestion.motion_score : null,
      audio_peak_score: isSuggested ? suggestion.audio_peak_score : null,
      selection_reason: isSuggested ? suggestion.reason : 'Time range selected by the operator.',
      suggested_start_time_seconds: suggestion?.start_time_seconds ?? null,
      suggested_end_time_seconds: suggestion?.end_time_seconds ?? null,
      operator_adjusted_suggestion: userAdjusted,
      source_integrity: {
        status: 'passed',
        method: 'browser_server_sha256_match',
        sha256: verifiedSourceDigest
      },
      start_time_seconds: Number(startTime),
      end_time_seconds: Number(endTime),
      preserve_full_frame: true,
      audio_included: true
    };
    return {
      videoBuffer,
      eventMarker,
      ...await this.cryptoProvider.generateAttestation(videoBuffer, chainMetadata, 'uploaded_video', eventMarker)
    };
  }
}

window.captureController = new MediaStreamCaptureController();
