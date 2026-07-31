@echo off
cd /d "%~dp0catalog-matcher"
if not exist "docker-compose.yml" (
  echo catalog-matcher/ not found. Copy GoodsProgram into catalog-matcher first.
  pause
  exit /b 1
)
echo.
echo  Catalog Matcher (GoodsProgram) — Docker
echo  UI:  http://localhost:3000
echo  API: http://localhost:8000
echo  Docs: http://localhost:8000/docs
echo.
echo  Then open the B2B dashboard → Подбор по каталогу
echo  (start-dashboard.bat → http://localhost:5500/...)
echo.
call start-app.bat
