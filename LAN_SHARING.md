# Share Traceframe on a trusted Wi-Fi network

This is for sharing the capture page with devices on the same trusted home, lab, or office Wi-Fi. It is not an internet deployment. Workspace routes require an account, but registration is open to people who can reach the host. Do not use guest/public Wi-Fi, do not forward the port on your router, and stop the server when finished.

## On the computer running Traceframe

1. Finish the one-time setup in README.md (Python dependencies and `.env`).
2. In PowerShell, from the project folder, run:

   ```powershell
   .\start-lan.ps1
   ```

3. If other devices cannot connect, run `.\allow-lan-firewall.ps1` from the project folder. It requests the Windows administrator approval needed to add a rule for the Traceframe Python runtime on port 8443, limited to **Private networks** and the **local subnet**. If Windows says access is denied again, your account or organization does not allow firewall changes; ask the PC administrator to run the helper. Do not allow Public networks.
4. Copy the HTTPS URL printed by the script and the `server.pem` certificate file to each trusted device. The certificate is stored at `outputs\lan-tls\server.pem` on the host computer. Share only the public certificate, never `server-key.pem`.

## On each other device

1. Import `server.pem` into that device's **current-user Trusted Root Certification Authorities** store. On Windows, open the certificate file and choose **Install Certificate → Current User → Place all certificates in the following store → Trusted Root Certification Authorities**. On phones/tablets, install the certificate profile and enable trust for it in the device's certificate settings. Menu names vary by OS.
2. Connect to the same Wi-Fi as the host computer.
3. Open the printed `https://<host-private-IP>:8443/capture/` link in a current Chrome, Edge, or Safari browser.
4. Register with a username and a password of at least 12 characters, or sign in if you already have an account. The first account on the host becomes its administrator; subsequent accounts are standard users.
5. Choose **Capture Display / Window**, select the game/window/display, and allow screen capture. Press **Prove & Crop Highlight** after the buffer has collected footage.

The host must keep `start-lan.ps1` running. Its IP can change when the router reconnects; rerun the script and use the new URL if that happens. The launcher regenerates the HTTPS certificate when the host IP changes, so install the updated `outputs\lan-tls\server.pem` on each device when prompted. The certificate is valid for one year.

## Limitations

Authentication uses a local SQLite account database. The HTTPS certificate is self-signed and must be transferred to peers through a trusted channel. AWS archiving is disabled by default. Public access needs a separately deployed, hardened service; do not expose this development server to the internet.
