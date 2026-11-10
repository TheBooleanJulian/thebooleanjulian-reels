@echo off
REM TBJ Reels Studio: double-click to open the UI in your browser. Close this window to stop it.
REM First time on a laptop: double-click polaroid-reel\setup.bat (installs Python + packages).
title TBJ Reels Studio
set "PY=python"
if exist "%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe" set "PY=%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe"
"%PY%" "%~dp0studio.py" %*
if errorlevel 1 (
  echo.
  echo The studio stopped with an error. If Python or a package is missing, run polaroid-reel\setup.bat first.
  pause
)
