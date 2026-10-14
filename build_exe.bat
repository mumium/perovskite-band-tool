@echo off
setlocal
cd /d "%~dp0"
set "PROJECT_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PROJECT_PYTHON%" -m PyInstaller --noconfirm --clean PerovskiteDeviceStudio.spec
if errorlevel 1 (
  echo Build failed. Install requirements.txt and PyInstaller first.
  pause
  exit /b 1
)
echo EXE ready: %~dp0dist\PerovskiteDeviceStudio.exe
pause
