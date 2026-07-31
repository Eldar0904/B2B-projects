@echo off
cd /d "%~dp0"

echo Starting Product Matching app...
echo (First run can take a few minutes to download images and install dependencies)
echo.

if not exist backend\.env (
    copy backend\.env.example backend\.env >nul
    echo Created backend\.env from example
)
if not exist frontend\.env.local (
    copy frontend\.env.local.example frontend\.env.local >nul
    echo Created frontend\.env.local from example
)

docker compose up --build

echo.
echo App stopped. Press any key to close this window.
pause >nul
