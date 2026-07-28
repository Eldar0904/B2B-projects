@echo off
cd /d "%~dp0"
echo.
echo  B2B Fitout Dashboard — local server
echo  Open: http://localhost:5500/B2B_Fitout_Dashboard_Prototype.html
echo  Press Ctrl+C to stop.
echo.
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" "http://localhost:5500/B2B_Fitout_Dashboard_Prototype.html"
  python -m http.server 5500
  goto :eof
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" "http://localhost:5500/B2B_Fitout_Dashboard_Prototype.html"
  py -m http.server 5500
  goto :eof
)
echo Python not found. Opening the HTML file directly instead...
start "" "%~dp0B2B_Fitout_Dashboard_Prototype.html"
pause
