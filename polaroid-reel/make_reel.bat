@echo off
REM Drag a photo folder onto this file to make <folder>_reel.mp4 next to it.
REM First time on a laptop: double-click setup.bat.
REM Edit the options line below to set length / music, e.g. -d 90 -m "C:\Music\track.mp3"
set OPTIONS=-d 90
if "%~1"=="" (
  echo Drag a folder of photos onto this file.
  pause
  exit /b 1
)
set "PY=python"
if exist "%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe" set "PY=%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe"
"%PY%" "%~dp0polaroid_reel.py" "%~1" -o "%~1_reel.mp4" %OPTIONS%
if errorlevel 1 echo If Python or a module is missing, double-click setup.bat first.
pause
