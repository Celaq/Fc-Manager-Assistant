@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if not exist "active-version.txt" goto missing
set "FC_ACTIVE="
set /p FC_ACTIVE=<"active-version.txt"
setlocal EnableDelayedExpansion
for /d %%D in (payloads\*) do if "%%~nxD"=="!FC_ACTIVE!" (
    if not exist "%%D\runtime\pythonw.exe" goto missing
    start "" "%%D\runtime\pythonw.exe" -I -B "%%D\launch.py"
    exit /b
)
:missing
echo FC Manager package is incomplete. Extract the whole ZIP and try again.
pause
exit /b 1
