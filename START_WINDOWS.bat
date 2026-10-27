@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (echo Python tidak ditemukan & pause & exit /b 1)
)
.venv\Scripts\python.exe -m pip install -r requirements.txt || (pause & exit /b 1)
.venv\Scripts\python.exe -m playwright install chromium || (pause & exit /b 1)
.venv\Scripts\python.exe -m gateway doctor || (pause & exit /b 1)
echo Token gateway dibuat di data\gateway-token.txt; jangan dibagikan.
.venv\Scripts\python.exe -m gateway serve
pause
