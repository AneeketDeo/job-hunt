@echo off
title Naukri SSO Login Launcher
cd /d "%~dp0"
echo ===================================================
echo Launching Naukri SSO Login in Microsoft Edge...
echo ===================================================
".\.venv\Scripts\python.exe" -m src.automation.sso_manager --platform naukri
