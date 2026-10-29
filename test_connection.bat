@echo off
setlocal
cd /d "%~dp0"
py test_connection.py
pause
