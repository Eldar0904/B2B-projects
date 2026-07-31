@echo off
cd /d "%~dp0"
docker compose down
echo.
echo App stopped. Press any key to close this window.
pause >nul
