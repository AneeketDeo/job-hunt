@echo off
title Job Hunt Orchestrator - Mobile Access Launcher
cd /d "%~dp0"
echo ===================================================
echo Starting Job Hunt Orchestrator for Mobile Access...
echo ===================================================

echo Starting FastAPI Backend with Auto-Reload on 0.0.0.0:8000...
start /B "" ".\.venv\Scripts\python.exe" -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload --loop asyncio

timeout /t 3 /nobreak >nul

echo Starting Secure Cloudflare Public Tunnel...
start "" ".\tools\cloudflared.exe" tunnel --url http://127.0.0.1:8000

echo.
echo ===================================================
echo Dashboard is now accessible from:
echo 1. Any Mobile (HTTPS Tunnel URL shown in Cloudflare window)
echo 2. Same Wi-Fi: http://192.168.0.107:8000
echo ===================================================
pause
