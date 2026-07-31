@echo off
title Catalog Matcher - Backend
cd /d "%~dp0..\backend"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo Backend: http://localhost:8000
echo API docs: http://localhost:8000/docs
echo Close this window to stop the server.
echo.

python -m uvicorn app.main:app --reload
pause
