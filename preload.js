const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  hashChunk: (buffer) => ipcRenderer.invoke('provenance:hash-chunk', buffer),
  signPayload: (payload) => ipcRenderer.invoke('provenance:sign', payload),
  onTriggerCapture: (callback) => ipcRenderer.on('trigger-capture', () => callback()),
  removeTriggerCapture: () => ipcRenderer.removeAllListeners('trigger-capture')
});
