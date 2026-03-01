@echo off
setlocal

echo.
echo  Garmin Health Coach — Windows Build
echo  =====================================
echo.

:: Install build dependencies
pip install pyinstaller pystray pillow --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed. Make sure Python is on your PATH.
    pause
    exit /b 1
)

:: Clean and build
pyinstaller garmin_coach.spec --clean --noconfirm
if errorlevel 1 (
    echo  ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

:: Zip the output folder for distribution
echo.
echo  Packaging dist\GarminHealthCoach\ into GarminHealthCoach-windows.zip ...
powershell -Command "Compress-Archive -Path 'dist\GarminHealthCoach' -DestinationPath 'GarminHealthCoach-windows.zip' -Force"

echo.
echo  Build complete.
echo  Executable : dist\GarminHealthCoach\GarminHealthCoach.exe
echo  Archive    : GarminHealthCoach-windows.zip
echo.
pause
