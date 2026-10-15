@echo off
setlocal
cd /d "%~dp0"
set "PROJECT_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
set "PDS_BUILD_LAYOUT="
if /i "%~1"=="--folder" set "PDS_BUILD_LAYOUT=folder"
"%PROJECT_PYTHON%" -m PyInstaller --noconfirm --clean PerovskiteDeviceStudio.spec
if errorlevel 1 (
  echo Build failed. Install requirements.txt and PyInstaller first.
  pause
  exit /b 1
)
if /i "%PDS_BUILD_LAYOUT%"=="folder" (
  echo Portable folder ready: %~dp0dist\PerovskiteDeviceStudio-portable
) else (
  echo EXE ready: %~dp0dist\PerovskiteDeviceStudio.exe
)
pause
