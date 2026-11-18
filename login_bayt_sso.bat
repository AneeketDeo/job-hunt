@echo off
title Bayt.com SSO Login Launcher
cd /d "%~dp0"
echo ===================================================
echo Launching Bayt.com SSO Login in Microsoft Edge...
echo ===================================================
".\.venv\Scripts\python.exe" -m src.automation.sso_manager --platform bayt
