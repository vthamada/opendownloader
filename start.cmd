@echo off
setlocal
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\LumeDownloads\installation.json" if exist "%LOCALAPPDATA%\Programs\LumeDownloads\app\LumeDownloads.exe" (
  start "OpenDownloader" "%LOCALAPPDATA%\Programs\LumeDownloads\app\LumeDownloads.exe"
  exit /b 0
)
if exist "dist\LumeDownloads\LumeDownloads.exe" (
  start "OpenDownloader" "dist\LumeDownloads\LumeDownloads.exe"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "OpenDownloader" ".venv\Scripts\pythonw.exe" launch.py
  exit /b 0
)
echo Execute tools\build.ps1 para preparar o aplicativo.
pause
