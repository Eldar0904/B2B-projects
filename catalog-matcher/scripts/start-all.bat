@echo off
title Catalog Matcher - Launcher
cd /d "%~dp0.."

echo Starting Catalog Matcher...
echo.

start "Catalog Matcher - Backend" "%~dp0start-backend.bat"
timeout /t 4 /nobreak >nul
start "Catalog Matcher - Frontend" "%~dp0start-frontend.bat"
timeout /t 6 /nobreak >nul

start http://localhost:5173/
echo Opened http://localhost:5173/ in your browser.
echo If the page does not load, wait a few seconds and refresh.
echo.
echo Two black windows must stay open: Backend and Frontend.
pause
