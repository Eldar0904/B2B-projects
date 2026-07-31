@echo off
title Catalog Matcher - Frontend
cd /d "%~dp0..\frontend"

if not exist "node_modules" (
    echo Installing npm packages...
    call npm install
)

echo.
echo Frontend will open at http://localhost:5173
echo If port 5173 is busy, check the URL shown below.
echo Close this window to stop the server.
echo.

call npm run dev
pause
