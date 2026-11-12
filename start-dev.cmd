@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto directory_error

where node >nul 2>nul
if errorlevel 1 goto node_missing
where pnpm >nul 2>nul
if errorlevel 1 goto pnpm_missing

echo Starting NewDiag. The browser will open automatically.
echo Keep this window open. Press Ctrl+C to stop the services.
call pnpm dev --open
set "DEV_EXIT_CODE=%ERRORLEVEL%"
if not "%DEV_EXIT_CODE%"=="0" pause
endlocal & exit /b %DEV_EXIT_CODE%

:directory_error
echo Cannot open the project directory.
pause
exit /b 1

:node_missing
echo Node.js was not found. Install Node.js and reopen this script.
pause
exit /b 1

:pnpm_missing
echo pnpm was not found. Install pnpm and reopen this script.
pause
exit /b 1
