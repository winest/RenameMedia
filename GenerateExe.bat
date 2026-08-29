@ECHO OFF
CD /D "%~dp0"

SET "OutDir=Output"
SET "AppName=RenameMedia"
SET "AppDir=%OutDir%\%AppName%"
SET "StageDir=build\stage"

REM Prefer the virtual environment in this folder, so that the exe is built with the same
REM exifread / pymediainfo versions that are used when running the .py directly.
SET "PyExe=python"
IF EXIST "venv\Scripts\python.exe" ( SET "PyExe=venv\Scripts\python.exe" )
IF EXIST ".venv\Scripts\python.exe" ( SET "PyExe=.venv\Scripts\python.exe" )
ECHO Building with "%PyExe%"

REM Build into a staging folder and copy the result over the existing output. Deleting
REM Output first fails whenever anything holds a handle on it, and a command prompt
REM sitting in that folder is enough to break the build.
IF EXIST "%StageDir%" ( RMDIR /S /Q "%StageDir%" )

REM --onedir is required: --onefile unpacks python3xx.dll / ucrtbase.dll into
REM %TEMP%\_MEIxxxxx, and Smart App Control / WDAC on a clean Windows 11 install
REM blocks loading unsigned DLLs from there (error 0xc0e90002).
REM Invoked as a module: the pyinstaller launcher is not on PATH when the package is
REM installed with "python -m pip install --user", but the module always is.
REM _Tools holds MediaInfo.dll, which RenameMedia.py puts on %PATH% at startup.
"%PyExe%" -m PyInstaller ^
  --distpath "%StageDir%" ^
  --workpath "build" ^
  --specpath "build" ^
  --name "%AppName%" ^
  --onedir ^
  --uac-admin ^
  --noconfirm ^
  --clean ^
  --add-data "%~dp0_Tools;_Tools" ^
  "%AppName%.py"

IF ERRORLEVEL 1 (
  ECHO Build failed.
  ECHO If PyInstaller is missing, run "%PyExe% -m pip install pyinstaller".
  PAUSE
  EXIT /B 1
)

IF NOT EXIST "%AppDir%" ( MKDIR "%AppDir%" )
XCOPY "%StageDir%\%AppName%" "%AppDir%\" /E /I /Y /Q >NUL
IF ERRORLEVEL 1 (
  ECHO Failed to copy the build into %AppDir%.
  ECHO Close any program running from that folder ^(including a command prompt whose
  ECHO current directory is inside it^) and run this script again.
  PAUSE
  EXIT /B 1
)

FOR %%f IN ("__pycache__" "build" "dist") DO (
  IF EXIST "%%~f\" ( RMDIR /S /Q "%%~f" )
)
IF EXIST "%AppName%.spec" ( DEL /F /Q "%AppName%.spec" )

REM Older versions of this script produced a single Output\RenameMedia.exe. Remove it so
REM that _AddExeToMenu.bat cannot register a stale build by mistake.
IF EXIST "%OutDir%\%AppName%.exe" ( DEL /F /Q "%OutDir%\%AppName%.exe" )

ECHO.
ECHO Output: %AppDir%\%AppName%.exe
ECHO Usage:  %AppName%.exe ^<file or directory path^>
ECHO NOTE:   Copy the whole "%AppName%" folder. The exe needs the _internal folder next
ECHO         to it. Run _AddExeToMenu.bat to register the right click menu entry.
ECHO.

PAUSE
