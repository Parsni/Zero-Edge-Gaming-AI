$ErrorActionPreference = 'Stop'

$project = $PSScriptRoot
$python = Join-Path $project 'venv\Scripts\python.exe'
$serverUrl = $null

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment is missing. Run: python -m venv venv; .\venv\Scripts\python.exe -m pip install -r requirements.txt'
}
$health = $null
for ($port = 8007; $port -le 8017; $port++) {
    $candidate = "http://127.0.0.1:$port"
    try {
        $candidateHealth = Invoke-RestMethod -Uri "$candidate/health" -TimeoutSec 1
    } catch {
        $candidateHealth = $null
    }
    if ($candidateHealth -and $candidateHealth.runtime_build -eq '2.11.0') {
        $serverUrl = $candidate
        $health = $candidateHealth
        break
    }
    if (-not $candidateHealth) {
        Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', "$port") -WorkingDirectory $project -WindowStyle Hidden
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 500
            try {
                $candidateHealth = Invoke-RestMethod -Uri "$candidate/health" -TimeoutSec 1
                if ($candidateHealth.runtime_build -eq '2.11.0') { break }
            } catch {
                $candidateHealth = $null
            }
        }
        if ($candidateHealth -and $candidateHealth.runtime_build -eq '2.11.0') {
            $serverUrl = $candidate
            $health = $candidateHealth
            break
        }
    }
}

if (-not $serverUrl -or -not $health -or -not $health.aws_archive -or $null -eq $health.aws_archive.lambda_hook_configured) {
    throw 'The updated Traceframe runtime did not become ready on ports 8007–8017. Check whether they are in use.'
}

Write-Host "Traceframe API ready at $serverUrl"
Write-Host "S3 archive configured: $($health.aws_archive.configured)"
Write-Host 'Opening Traceframe in your default browser.'
Write-Host 'Sign in, load a video, pass the SHA-256 check, then review the recommended 10-second window.'
Start-Process "$serverUrl/capture/"
