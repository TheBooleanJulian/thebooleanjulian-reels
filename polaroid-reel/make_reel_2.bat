@echo off
REM Template 2 (pile + instant camera). Drag a photo folder onto this file to make <folder>_pile.mp4 next to it.
REM First time on a laptop: double-click setup.bat.
REM Captions: put captions.txt in the photo folder ("filename = caption" per line).
REM Edit the options line below, e.g. -d 60 -m "C:\Music\track.mp3" -e "D:\Event\end_card.png"
set OPTIONS=
if "%~1"=="" (
  echo Drag a folder of photos onto this file.
  pause
  exit /b 1
)
set "PY=python"
if exist "%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe" set "PY=%USERPROFILE%\.polaroid-reel\venv\Scripts\python.exe"
"%PY%" "%~dp0polaroid_pile.py" "%~1" -o "%~1_pile.mp4" %OPTIONS%
if errorlevel 1 echo If Python or a module is missing, double-click setup.bat first.
pause
