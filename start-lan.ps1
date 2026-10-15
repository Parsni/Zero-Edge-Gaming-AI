$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$python = Join-Path $project 'venv\Scripts\python.exe'
$port = 8443
$certDir = Join-Path $project 'outputs\lan-tls'
$certPath = Join-Path $certDir 'server.pem'
$keyPath = Join-Path $certDir 'server-key.pem'
$ip = (& $python -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print(s.getsockname()[0]); s.close()" 2>$null | Select-Object -Last 1)

if (-not $ip) { throw 'Could not find a private IPv4 address. Connect this PC to Wi-Fi or Ethernet and retry.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment is missing. Follow the setup steps in README.md first.' }
$certIp = ''
if (Test-Path -LiteralPath $certPath) {
    $certIp = (& $python -c "from cryptography import x509; from cryptography.x509.oid import ExtensionOID; from pathlib import Path; c=x509.load_pem_x509_certificate(Path(r'$certPath').read_bytes()); print(c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.IPAddress)[0])" 2>$null | Select-Object -Last 1)
}
if (-not (Test-Path -LiteralPath $certPath) -or -not (Test-Path -LiteralPath $keyPath) -or $certIp -ne $ip) {
    New-Item -ItemType Directory -Force -Path $certDir | Out-Null
    @'
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import os, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

directory = Path(sys.argv[1])
address = ip_address(sys.argv[2])
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(address))])
now = datetime.now(timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
    .public_key(key.public_key()).serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
        data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
        encipher_only=False, decipher_only=False), critical=True)
    .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .sign(key, hashes.SHA256()))
(directory / 'server-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
(directory / 'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
'@ | & $python - $certDir $ip
}

Write-Host "Traceframe LAN capture will be available at https://${ip}:$port/capture/"
Write-Host "If the host IP changed and a new certificate was generated, import the updated server.pem on each device before opening the URL."
Write-Host "Certificate: $certPath"
Write-Host "Keep this on a trusted private Wi-Fi network. Do not configure router port forwarding."
& $python -m uvicorn server:app --host $ip --port $port --ssl-certfile $certPath --ssl-keyfile $keyPath
