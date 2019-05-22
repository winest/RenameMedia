@ECHO OFF
CD /D "%~dp0"

SET "OutDir=Output"

IF NOT EXIST "%OutDir%" ( MKDIR "%OutDir" )
pyinstaller -F --distpath="%OutDir%" --uac-admin RenameMedia.py
pyinstaller --clean RenameMedia.py

PAUSE