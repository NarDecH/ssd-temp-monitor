@echo off
rem One-click GitHub push for this project.
rem 1) opens the "new repository" page (name prefilled) if not created yet
rem 2) asks for your GitHub username, sets the remote
rem 3) pushes - Git Credential Manager opens a browser window for login
setlocal
set /p GH_USER=Your GitHub username (e.g. NarDech):
if "%GH_USER%"=="" exit /b 1
git remote set-url origin https://github.com/%GH_USER%/ssd-temp-monitor.git
echo.
echo Pushing to https://github.com/%GH_USER%/ssd-temp-monitor.git
echo (if it does not exist yet, it will open the creation page first)
echo.
git ls-remote --exit-code origin >/nul 2>&1
if errorlevel 1 (
  start "" "https://github.com/new?name=ssd-temp-monitor"
  echo Create the repository in the browser (Public, no README), then
  pause
)
git push -u origin main
if errorlevel 1 (
  echo.
  echo Push failed - read the message above.
  pause
  exit /b 1
)
echo.
echo SUCCESS - code is on GitHub. CI will run automatically.
echo To publish a release:  git tag v1.6.0 ^&^& git push origin v1.6.0
pause
