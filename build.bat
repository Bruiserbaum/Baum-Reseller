@echo off
echo ========================================
echo  Baum Reseller - Local Build
echo ========================================

echo.
echo [1/3] Running PyInstaller...
py -m PyInstaller baum_reseller.spec --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Building installer with Inno Setup...
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    "C:\Program Files\Inno Setup 6\ISCC.exe" installer\setup.iss
) else (
    echo WARNING: Inno Setup not found. Skipping installer creation.
    echo          Install from https://jrsoftware.org/isdownload.php
)

echo.
echo [3/3] Done!
if exist "dist\BaumResellerSetup.exe" (
    echo Installer: dist\BaumResellerSetup.exe
) else (
    echo Binary folder: dist\BaumReseller\BaumReseller.exe
)
echo.
pause
