@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  Neural Sprite - Windows installer builder
echo ============================================================
echo.

rem Prefer a real Python installation in standard locations before PATH.
rem Windows App Execution Aliases can make "where python" succeed even when
rem the command only opens the Microsoft Store and cannot run Python.
set "PYTHON_EXE="
set "PYTHON_ARGS="

rem Check common per-user Python installations, including the user's Python 3.14.
if defined LocalAppData (
  for %%V in (Python314 Python313 Python312 Python311 Python310) do (
    if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\%%V\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\%%V\python.exe"
  )
)

rem Check common machine-wide installations.
if not defined PYTHON_EXE (
  for %%V in (314 313 312 311 310) do (
    if not defined PYTHON_EXE if exist "%ProgramFiles%\Python%%V\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python%%V\python.exe"
    if not defined PYTHON_EXE if exist "%ProgramFiles%\Python\Python%%V\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python\Python%%V\python.exe"
  )
)

rem Only use PATH commands if no real installation was found above.
if not defined PYTHON_EXE (
  where python >nul 2>nul && set "PYTHON_EXE=python"
  if not defined PYTHON_EXE where py >nul 2>nul && set "PYTHON_EXE=py" && set "PYTHON_ARGS=-3"
)

if not defined PYTHON_EXE (
  echo ERROR: Could not locate Python automatically.
  echo If Python is installed, edit this file's PYTHON_EXE line to its full path.
  echo Python 3.11 or newer is recommended.
  echo.
  pause
  exit /b 1
)

echo Using Python: %PYTHON_EXE% %PYTHON_ARGS%
%PYTHON_EXE% %PYTHON_ARGS% --version
if errorlevel 1 goto fail
%PYTHON_EXE% %PYTHON_ARGS% -m pip --version
if errorlevel 1 (
  echo pip is missing. Attempting to enable it...
  %PYTHON_EXE% %PYTHON_ARGS% -m ensurepip --upgrade
  if errorlevel 1 goto fail
)

echo.
echo Installing/updating build dependencies...
%PYTHON_EXE% %PYTHON_ARGS% -m pip install --upgrade pip
if errorlevel 1 goto fail
%PYTHON_EXE% %PYTHON_ARGS% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo.
echo Building the standalone Neural Sprite application...
%PYTHON_EXE% %PYTHON_ARGS% -m PyInstaller --noconfirm --clean --windowed --name "Neural Sprite" --collect-all PySide6 --collect-all PIL --collect-all numpy main.py
if errorlevel 1 goto fail

if not exist "dist\Neural Sprite\Neural Sprite.exe" (
  echo ERROR: PyInstaller finished but the application EXE was not found.
  goto fail
)

echo.
echo Standalone application built successfully:
echo   %CD%\dist\Neural Sprite\Neural Sprite.exe

echo.
rem Locate Inno Setup Compiler in PATH or standard installation folders.
set "ISCC_EXE="
where ISCC.exe >nul 2>nul && set "ISCC_EXE=ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "F:\Inno Setup 6\ISCC.exe" set "ISCC_EXE=F:\Inno Setup 6\ISCC.exe"

if defined ISCC_EXE (
  echo Creating the installable setup EXE...
  "%ISCC_EXE%" "installer\NeuralSprite.iss"
  if errorlevel 1 goto fail
  echo.
  echo INSTALLER CREATED:
  echo   %CD%\installer_output\NeuralSprite_Setup_v0.28.exe
) else (
  echo Inno Setup 6 was not found, so a setup EXE cannot be compiled yet.
  echo The standalone app is ready in: dist\Neural Sprite\
  echo You can launch it with "dist\Neural Sprite\Neural Sprite.exe".
  echo To create a conventional setup EXE, install Inno Setup 6 and rerun this file.
  echo Download: https://jrsoftware.org/isdl.php
)

echo.
echo Build process finished. Review the messages above for the output created.
pause
exit /b 0

:fail
echo.
echo BUILD FAILED. Copy the error shown above so the specific issue can be fixed.
pause
exit /b 1
