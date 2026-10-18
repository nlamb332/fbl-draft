@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\local-panel.ps1" -PageRunner -OpenPanel
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo FBL Draft Advisor stopped or could not start ^(code %EXIT_CODE%^). Read the message above.
    pause
)
endlocal & exit /b %EXIT_CODE%
