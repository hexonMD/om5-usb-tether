@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" jewelry_studio.py %*
    goto finished
)
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    "%VIRTUAL_ENV%\Scripts\python.exe" jewelry_studio.py %*
    goto finished
)
where py.exe >nul 2>nul
if errorlevel 1 (
    python jewelry_studio.py %*
) else (
    py -3 jewelry_studio.py %*
)
:finished
set "studio_result=%ERRORLEVEL%"
if not "%studio_result%"=="0" (
    echo For setup instructions, open README.md.
    if "%~1"=="" pause
)
popd
exit /b %studio_result%
