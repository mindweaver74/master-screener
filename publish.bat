@echo off
setlocal
cd /d "%~dp0"

if not exist "docs\index.html" (
  echo docs\index.html not found. Run run_screen.bat first.
  goto :end
)
if not exist ".git" (
  echo This folder is not a git repository. See README.md - GitHub section.
  goto :end
)

git add docs
git commit -m "update %DATE% %TIME%"
if errorlevel 1 (
  echo Nothing new to commit, or the commit failed. See the message above.
  goto :end
)
git push
if errorlevel 1 (
  echo Push failed. Check your network and GitHub login.
  goto :end
)
echo.
echo Published. The page updates in about a minute.

:end
pause
