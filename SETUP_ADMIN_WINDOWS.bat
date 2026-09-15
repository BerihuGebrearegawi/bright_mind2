@echo off
setlocal
cd /d "%~dp0"
echo.
echo ================================================
echo Bright Mind Tutor - ADMIN ACCESS SETUP
echo ================================================
echo.
echo This will add admin=true and role=admin to your
 echo Firebase administrator account. No password is changed.
echo.
py -m pip show firebase-admin >nul 2>&1
if errorlevel 1 (
  echo Installing the required Firebase Admin package...
  py -m pip install firebase-admin==6.9.0
  if errorlevel 1 goto :error
)
py setup_admin.py
if errorlevel 1 goto :error
echo.
echo Setup completed successfully.
pause
exit /b 0
:error
echo.
echo SETUP FAILED. Read the message above.
pause
exit /b 1
