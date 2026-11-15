@echo off
rem ---------------------------------------------------------------
rem  SPOTI PLAYER launcher
rem
rem  Picks a Python interpreter that can actually load the modules the
rem  player needs (PySide6/Qt6 + Pillow + python-vlc + mutagen), installs
rem  the packages when they are missing, then starts the player.
rem
rem  Any argument is forwarded to main.py:
rem      SpotiPlayer.bat
rem      SpotiPlayer.bat "D:\Music"
rem      SpotiPlayer.bat --folder "D:\Music" --vlc-dir "C:\Program Files\VideoLAN\VLC"
rem      SpotiPlayer.bat --devices
rem      SpotiPlayer.bat --discord-id 123456789012345678
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "NEED=import PySide6.QtWidgets, PIL.Image, vlc, mutagen"
set "PY="

rem 1) prefer an interpreter that already has everything
for %%C in ("python" "python3" "py -3.13" "py -3" "py") do (
  if not defined PY %%~C -c "%NEED%" >nul 2>nul && set "PY=%%~C"
)

rem 2) otherwise take one that at least has Qt and install the rest
if not defined PY (
  for %%C in ("python" "python3" "py -3.13" "py -3" "py") do (
    if not defined PY %%~C -c "import PySide6.QtWidgets" >nul 2>nul && set "PY=%%~C"
  )
)

if not defined PY (
  echo.
  echo Python 3 with PySide6 ^(Qt 6^) was not found.
  echo Install Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PY% -c "%NEED%" >nul 2>nul
if errorlevel 1 (
  echo Installing the packages Spoti Player needs ^(PySide6, python-vlc, mutagen, pillow^)...
  %PY% -m pip install --disable-pip-version-check --upgrade -r requirements.txt
  %PY% -c "%NEED%" >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Could not prepare a working Python environment with: %PY%
    echo Try running:  python -m pip install -r requirements.txt
    echo Note: VLC media player ^(64-bit^) must also be installed.
    echo.
    pause
    exit /b 1
  )
)

rem optional: Discord Rich Presence support (the player also runs without it)
%PY% -c "import pypresence" >nul 2>nul
if errorlevel 1 (
  echo Installing optional Discord support ^(pypresence^)...
  %PY% -m pip install --disable-pip-version-check pypresence
)

%PY% main.py %*
if errorlevel 1 pause
endlocal
