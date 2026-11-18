# Ensure logs directory exists
$logDir = "V:\Development\Job Hunt\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force }

# Kill existing uvicorn/cloudflared if any
Get-Process python, cloudflared -ErrorAction SilentlyContinue | Where-Object { 
    $_.Path -match "Job Hunt" -or $_.CommandLine -match "src.api.main:app"
} | Stop-Process -Force -ErrorAction SilentlyContinue

# Start Uvicorn bound to 0.0.0.0:8000 in background
$uvicornLog = "$logDir\uvicorn_mobile.log"
Start-Process -FilePath "V:\Development\Job Hunt\.venv\Scripts\python.exe" `
    -ArgumentList "-m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --loop asyncio" `
    -RedirectStandardOutput $uvicornLog `
    -RedirectStandardError "$logDir\uvicorn_err.log" `
    -WindowStyle Hidden

Start-Sleep -Seconds 3

# Start Cloudflare Quick Tunnel in background
$cfLog = "$logDir\cloudflared_mobile.log"
if (Test-Path $cfLog) { Remove-Item $cfLog -Force }

Start-Process -FilePath "V:\Development\Job Hunt\tools\cloudflared.exe" `
    -ArgumentList "tunnel --url http://127.0.0.1:8000 --logfile `"$cfLog`"" `
    -WindowStyle Hidden

# Wait for URL to appear in cloudflared log
$tunnelUrl = ""
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $cfLog) {
        $content = Get-Content $cfLog -Raw -ErrorAction SilentlyContinue
        if ($content -match "https://[a-zA-Z0-9-]+\.trycloudflare\.com") {
            $tunnelUrl = $matches[0]
            break
        }
    }
}

$mobileInfo = @"
============================================================
JOB HUNT ORCHESTRATOR - MOBILE ACCESS READY
============================================================
Public HTTPS URL (Access from Mobile anywhere):
$tunnelUrl

Local Wi-Fi URL (Access from Mobile on same Wi-Fi):
http://192.168.0.107:8000
============================================================
"@

Set-Content -Path "V:\Development\Job Hunt\data\mobile_access.txt" -Value $mobileInfo
Write-Output $mobileInfo
