@echo off
setlocal
cd /d "%~dp0"
set "PY="
set "PYPREFIX="

rem Find Python 3.10+ (the Microsoft Store stub fails this check and is skipped).
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY=py"
        set "PYPREFIX=-3"
    )
)
if not defined PY (
    echo.
    echo Sleeper Advisor needs Python 3.10 or newer, and it was not found on this computer.
    echo   Option 1: install it from https://www.python.org/downloads/
    echo             and tick "Add python.exe to PATH" on the first installer screen.
    echo   Option 2: in a terminal run:  winget install Python.Python.3.12
    set "EXIT_CODE=1"
    goto :done
)

set "SA_URL="
set "SA_USER="
for /f "usebackq delims=" %%A in (`%PY% %PYPREFIX% -m sleeper_advisor config get draft_url 2^>nul`) do set "SA_URL=%%A"
for /f "usebackq delims=" %%A in (`%PY% %PYPREFIX% -m sleeper_advisor config get username 2^>nul`) do set "SA_USER=%%A"

if not defined SA_URL goto :prompt
if not defined SA_USER goto :prompt
echo Saved draft:    %SA_URL%
echo Saved username: %SA_USER%
set "REUSE="
set /p "REUSE=Use these again? [Y/n]: "
if /i "%REUSE%"=="n" goto :prompt
goto :run

:prompt
set "SA_URL="
set "SA_USER="
echo.
echo On draft day open the REAL league draft in Sleeper and copy its address;
echo a mock draft has a different address.
set /p "SA_URL=Sleeper draft URL: "
set /p "SA_USER=Sleeper username: "

:run
if not defined SA_URL (
    echo A draft URL is required.
    set "EXIT_CODE=1"
    goto :done
)

%PY% %PYPREFIX% -m sleeper_advisor config set draft_url "%SA_URL%"
if errorlevel 1 (
    echo Paste the full draft address, for example https://sleeper.com/draft/nba/1234567890
    set "EXIT_CODE=1"
    goto :done
)
%PY% %PYPREFIX% -m sleeper_advisor config set username "%SA_USER%"

if defined SA_USER (
    %PY% %PYPREFIX% -m sleeper_advisor serve "%SA_URL%" --user "%SA_USER%" --open-browser
) else (
    %PY% %PYPREFIX% -m sleeper_advisor serve "%SA_URL%" --open-browser
)
set "EXIT_CODE=%ERRORLEVEL%"

:done
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Sleeper Advisor stopped or could not start ^(code %EXIT_CODE%^). Read the message above.
    pause
)
endlocal & exit /b %EXIT_CODE%
