const crypto = require('crypto');
const os = require('os');

class KineticMerkleProvenance {
  constructor() {
    this.keyPair = null;
  }

  async initializeKeys() {
    if (!this.keyPair) {
      this.keyPair = crypto.generateKeyPairSync('ec', {
        namedCurve: 'prime256v1',
        publicKeyEncoding: { type: 'spki', format: 'pem' },
        privateKeyEncoding: { type: 'pkcs8', format: 'pem' }
      });
    }
    return this.keyPair.publicKey;
  }

  getClientEnvironment() {
    return {
      source: 'desktop_runtime_reported',
      attested: false,
      architecture: os.arch(),
      platform: os.platform()
    };
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

  sha256Hex(data) {
    return crypto.createHash('sha256').update(data).digest('hex');
  }

  async generateAttestation(videoArrayBuffer, chunkChainMetadata, captureMode = 'unknown') {
    await this.initializeKeys();

    const fullVideoHash = this.sha256Hex(Buffer.from(videoArrayBuffer));
    const clientEnvironment = this.getClientEnvironment();

    let prevChainHash = '0'.repeat(64);
    const verifiedChain = [];

    for (let i = 0; i < chunkChainMetadata.length; i++) {
      const item = chunkChainMetadata[i];
      const entropyObj = item.entropy || {};
      const entropyCanonical = this.canonicalize(entropyObj);
      const entropyHash = this.sha256Hex(entropyCanonical);
      const linkHash = this.sha256Hex(
        `${prevChainHash}:${item.chunk_sha256}:${entropyHash}:${item.timestamp}`
      );

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
      capture_mode: captureMode,
      client_environment: clientEnvironment,
      temporal_chain: verifiedChain,
      temporal_chain_length: verifiedChain.length,
      temporal_merkle_root: prevChainHash,
      timestamp: Date.now(),
      version: '2.0.0-kinetic-merkle',
      video_sha256: fullVideoHash
    };

    const canonicalPayload = this.canonicalize(manifest);
    const signer = crypto.createSign('SHA256');
    signer.update(canonicalPayload);
    signer.end();

    const signature = signer.sign(this.keyPair.privateKey, 'base64');

    return {
      manifest,
      public_key: this.keyPair.publicKey,
      signature
    };
  }
}

module.exports = new KineticMerkleProvenance();
