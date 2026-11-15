@echo off
rem ---------------------------------------------------------------
rem  Build "Spoti Player.exe" (single file) with PyInstaller.
rem
rem  Result:  dist\Spoti Player.exe
rem
rem  libVLC is NOT bundled - the exe uses the VLC you already have
rem  installed. For a portable build put a "vlc" folder (with
rem  libvlc.dll) next to the exe.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY="
for %%C in ("python" "python3" "py -3.13" "py -3" "py") do (
  if not defined PY %%~C -c "import PySide6.QtWidgets, PIL, vlc, mutagen" >nul 2>nul && set "PY=%%~C"
)
if not defined PY (
  for %%C in ("python" "python3" "py -3.13" "py -3" "py") do (
    if not defined PY %%~C -c "import PySide6.QtWidgets" >nul 2>nul && set "PY=%%~C"
  )
)
if not defined PY (
  echo Python 3 with PySide6 was not found. Install Python 3.10+ and run:
  echo     python -m pip install -r requirements.txt
  pause
  exit /b 1
)

%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo Installing PyInstaller...
  %PY% -m pip install --disable-pip-version-check --upgrade pyinstaller
)

if not exist "assets\spoti.ico" (
  echo Creating the application icon...
  %PY% tools\make_icon.py
)

echo.
echo Building... (this takes a minute or two)
%PY% -m PyInstaller --clean --noconfirm SpotiPlayer.spec
if errorlevel 1 (
  echo.
  echo Build failed - see the messages above.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  Done:  "%CD%\dist\Spoti Player.exe"
echo ============================================================
echo.
echo  Copy that single file anywhere and run it.
echo  VLC media player (64-bit) must be installed on the PC.
echo.
pause
endlocal
