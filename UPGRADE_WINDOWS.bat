@echo off
setlocal
cd /d "%~dp0"
echo BAG v0.6.1 - Upgrade v0.6.0 (source only; data/config preserved)
if "%~1"=="" (
  set /p TARGET=Masukkan path FULL folder proyek v0.6.0: 
) else (
  set "TARGET=%~1"
)
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% tools\upgrade.py --target "%TARGET%" || (echo Dry-run gagal & pause & exit /b 1)
echo Pastikan gateway lama sudah dihentikan dan path benar.
set /p CONFIRM=Ketik LANJUT untuk upgrade: 
if /i not "%CONFIRM%"=="LANJUT" (echo Dibatalkan & exit /b 0)
%PY% tools\upgrade.py --target "%TARGET%" --apply
if errorlevel 1 (echo Upgrade gagal; coba baca error di atas & pause & exit /b 1)
echo Selesai. Periksa README dan jalankan tes di folder proyek lama.
pause
