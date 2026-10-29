@echo off
setlocal
cd /d "%~dp0"
echo Running the screener (takes a few minutes). Run it again after the close (15:40 or later) for confirmed values.
py screener.py
if errorlevel 1 goto :error
start "" "%~dp0docs\index.html"
echo.
echo Done. docs\index.html (browser) and result\screen_result.csv (all rows).
pause
exit /b 0

:error
echo.
echo ERROR: see the messages above.
pause
exit /b 1
