@echo off
REM One-click setup for Polaroid Reel on a new Windows laptop. Double-click it; safe to run again.
REM Installs Python 3.12 (via winget) if needed, then the packages into a private environment in
REM %USERPROFILE%\.polaroid-reel\venv - on this laptop, not in the synced Drive folder.
REM Not under AppData: Microsoft Store Python silently redirects AppData writes into its own
REM sandbox folder, where these .bat files can't see them.
REM ffmpeg comes bundled with the imageio-ffmpeg package, so nothing else to install.
setlocal
title Polaroid Reel - setup
set "VENV=%USERPROFILE%\.polaroid-reel\venv"
set "VPY=%VENV%\Scripts\python.exe"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"

echo === Polaroid Reel setup ===
echo.

call :find_python
if defined PY goto have_python

echo Python 3.10 or newer was not found. Installing Python 3.12 - this takes a minute or two...
where winget >nul 2>nul
if errorlevel 1 goto no_winget
winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
if defined PY goto have_python
echo.
echo Python was installed, but this window can't see it yet.
echo Close this window and double-click setup.bat again.
pause
exit /b 1

:no_winget
echo.
echo This laptop has no winget, so Python can't be installed automatically.
echo Opening the Python download page: install Python 3.12, tick "Add python.exe to PATH",
echo then double-click setup.bat again.
start "" https://www.python.org/downloads/
pause
exit /b 1

:have_python
echo Using Python: %PY%
REM reuse the environment if it still works, otherwise rebuild it
if exist "%VPY%" "%VPY%" -c "%CHECK%" >nul 2>nul && goto install
if exist "%VENV%" rmdir /s /q "%VENV%"
echo Creating the environment in %VENV% ...
%PY% -m venv "%VENV%"
if errorlevel 1 goto failed
if not exist "%VPY%" (
  echo The environment was not created where expected: %VENV%
  goto failed
)

:install
echo Installing packages (numpy, opencv, pillow, ffmpeg, iPhone HEIC support)...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" pillow-heif
if errorlevel 1 goto failed
"%VPY%" -c "import cv2, numpy, PIL, pillow_heif, imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()"
if errorlevel 1 goto failed
REM The rest of TBJ-reels (studio.bat, reel presets, interviews, presentations) shares this environment.
echo Installing the reel, interview and transcription packages (scipy, faster-whisper)...
"%VPY%" -m pip install --disable-pip-version-check -r "%~dp0..\requirements.txt"
if errorlevel 1 goto failed
echo.
echo All set. Double-click studio.bat (one folder up) to open TBJ Reels Studio,
echo or drag a photo folder onto make_reel.bat (template 1) or make_reel_2.bat (template 2).
pause
exit /b 0

:failed
echo.
echo Setup failed - see the messages above. Check the internet connection and run setup.bat again.
pause
exit /b 1

REM Sets PY to a command that runs Python 3.10+, or leaves it empty.
:find_python
set "PY="
for %%C in ("py -3.12" "py -3" "python") do (
  if not defined PY (
    %%~C -c "%CHECK%" >nul 2>nul && set "PY=%%~C"
  )
)
REM fresh winget install: not on this window's PATH yet, so look where it lands
set "NEWPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%NEWPY%" set PY="%NEWPY%"
exit /b 0
