@echo off
setlocal
cd /d "%~dp0"

echo.
echo  Goods Program — запуск БЕЗ Docker (SQLite + локальный Qdrant)
echo  API: http://localhost:8000
echo  Дашборд B2B: раздел «Подбор по каталогу» (нужен этот API)
echo.

cd backend

if not exist .env (
  if exist env.native.example (
    copy /Y env.native.example .env >nul
    echo Создан backend\.env из env.native.example ^(SQLite^)
  ) else if exist .env.example (
    copy /Y .env.example .env >nul
    echo Создан backend\.env — при отсутствии Postgres укажите DATABASE_URL=sqlite:///./product_matching.db
  )
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (set PY=py) else (set PY=python)

if not exist .venv (
  echo Создание виртуального окружения...
  %PY% -m venv .venv
)

call .venv\Scripts\activate.bat
echo Установка зависимостей Python ^(первый раз может занять несколько минут^)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
  echo Ошибка pip install. Проверьте Python 3.11+ и интернет.
  pause
  exit /b 1
)

echo.
echo Запуск API на http://localhost:8000 ...
echo Окно можно свернуть. Для остановки: Ctrl+C
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
