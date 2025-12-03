@ECHO OFF
CD /D "%~dp0"

SET "OutDir=Output"

IF NOT EXIST "%OutDir%" ( MKDIR "%OutDir%" )
pyinstaller --distpath="%OutDir%" --uac-admin --clean --onefile RenameMedia.py

FOR %%f IN ("__pycache__" "build" "dist" "RenameMedia.spec") DO (
  IF EXIST "%%~f\\*" (
    DEL /F /S /Q "%%~f"
    RMDIR /S /Q "%%~f"
  ) ELSE IF EXIST "%%~f" (
    DEL /F /Q "%%~f"
  )
)

PAUSE