const { app, BrowserWindow, ipcMain, desktopCapturer, globalShortcut, dialog, session } = require('electron');
const path = require('path');
const { URL } = require('url');
const provenance = require('./provenance');

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 900,
    minWidth: 1024,
    minHeight: 768,
    backgroundColor: '#0b0f19',
    title: 'Traceframe — Media Evidence Capture & Audit',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  // Use the local API origin so the desktop shell shares the same authenticated
  // browser session as the capture page and protected media endpoints.
  const serverUrl = process.env.TRACEFRAME_SERVER_URL || 'http://127.0.0.1:8007';
  const captureUrl = new URL('/capture/', serverUrl).toString();
  mainWindow.loadURL(captureUrl);

  // Register F9 Hotkey
  globalShortcut.register('F9', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('trigger-capture');
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  // The renderer invokes getDisplayMedia directly from the user's button click.
  // Show the native source picker here, then grant only the selected source.
  session.defaultSession.setDisplayMediaRequestHandler(async (request, callback) => {
    if (
      !request.userGesture ||
      !mainWindow ||
      mainWindow.isDestroyed() ||
      request.frame !== mainWindow.webContents.mainFrame
    ) {
      callback(null);
      return;
    }

    try {
      const sources = await desktopCapturer.getSources({
        types: ['screen', 'window'],
        thumbnailSize: { width: 0, height: 0 }
      });
      if (!sources.length) {
        callback(null);
        return;
      }

      const options = sources.slice(0, 8);
      const result = await dialog.showMessageBox(mainWindow, {
        type: 'question',
        title: 'Choose capture source',
        message: 'Select the screen or window to capture.',
        buttons: [...options.map((source) => source.name.slice(0, 48)), 'Cancel'],
        defaultId: 0,
        cancelId: options.length,
        noLink: true
      });
      const source = result.response === options.length ? null : options[result.response];
      callback(source ? { video: source } : null);
    } catch (_error) {
      callback(null);
    }
  });

  // Chunk SHA-256 Hashing
  ipcMain.handle('provenance:hash-chunk', (event, buffer) => {
    return provenance.sha256Hex(Buffer.from(buffer));
  });

  // Sign Payload
  ipcMain.handle('provenance:sign', async (event, { videoBuffer, chainMetadata, captureMode }) => {
    return await provenance.generateAttestation(videoBuffer, chainMetadata, captureMode);
  });

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
