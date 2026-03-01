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
:: (Brief wait lets Windows Defender finish scanning the new .exe before we zip)
echo.
echo  Waiting for file locks to clear ...
timeout /t 5 /nobreak > nul

echo  Packaging dist\GarminHealthCoach\ into GarminHealthCoach-windows.zip ...
python -c "import zipfile, os; zf=zipfile.ZipFile('GarminHealthCoach-windows.zip','w',zipfile.ZIP_DEFLATED); [zf.write(os.path.join(r,f), os.path.relpath(os.path.join(r,f),'dist')) for r,d,files in os.walk('dist\\GarminHealthCoach') for f in files]; zf.close(); print(' Done.')"
if errorlevel 1 (
    echo  WARNING: Python zip failed, falling back to PowerShell ...
    powershell -Command "Compress-Archive -Path 'dist\GarminHealthCoach' -DestinationPath 'GarminHealthCoach-windows.zip' -Force"
)

echo.
echo  Build complete.
echo  Executable : dist\GarminHealthCoach\GarminHealthCoach.exe
echo  Archive    : GarminHealthCoach-windows.zip
echo.
pause
