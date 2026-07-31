@echo off
setlocal
set ROOT=%~dp0
set SRC=%ROOT%goods-program\frontend\public\matching
set DST1=%ROOT%goods-matching
set DST2=%ROOT%firebase-deploy\public\goods-matching

if not exist "%SRC%\index.html" (
  echo Не найден %SRC%
  pause
  exit /b 1
)

robocopy "%SRC%" "%DST1%" /E /NFL /NDL /NJH /NJS /nc /ns /np
robocopy "%SRC%" "%DST2%" /E /NFL /NDL /NJH /NJS /nc /ns /np
echo Скопировано в goods-matching\ и firebase-deploy\public\goods-matching\
pause
