@echo off
REM ==================================================================
REM TankTrouble - one-click build script
REM ==================================================================
REM Usage: double-click or run "build.bat" from cmd
REM Flow:
REM   1. Check dependencies (PyInstaller, Inno Setup)
REM   2. Clean old build/ dist/ installers/Output/
REM   3. Optional: convert app_icon.png -> app_icon.ico
REM   4. PyInstaller (onedir, console=False) -> dist\TankTrouble\
REM   5. Inno Setup (ISCC.exe) -> installers\Output\*-Setup.exe
REM ==================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   TankTrouble Build
echo   Working dir: %CD%
echo ============================================
echo.

REM ==================================================================
REM 1. dependency check
REM ==================================================================
echo [1/4] Checking dependencies...

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller not found. Run: pip install pyinstaller
    pause
    exit /b 1
)
echo        PyInstaller  OK

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"         set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    echo [WARN] Inno Setup not found. Only green build, skipping installer.
    set "SKIP_INSTALLER=1"
) else (
    echo        Inno Setup  OK
)

if not exist "app\assets\icon\app_icon.png" (
    echo [WARN] app\assets\icon\app_icon.png not found. Using default icon.
) else (
    if not exist "app\assets\icon\app_icon.ico" (
        echo        Converting png icon to ico...
        python tools\_gen_icon.py 2>nul
        if errorlevel 1 (
            echo [WARN] png-to-ico conversion failed. Skipping icon.
        ) else (
            echo        Icon OK
        )
    )
)
echo.

REM ==================================================================
REM 2. clean old artifacts
REM ==================================================================
echo [2/4] Cleaning old artifacts...
if exist "build"            rmdir /s /q build
if exist "dist"             rmdir /s /q dist
if exist "installers\Output" rmdir /s /q installers\Output
echo        Cleaned
echo.

REM ==================================================================
REM 3. PyInstaller build
REM ==================================================================
echo [3/4] PyInstaller building (onedir, no console)...
echo.

pyinstaller TankTrouble.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed. See above.
    pause
    exit /b 1
)

echo.
echo        PyInstaller done ^-> dist\TankTrouble\
echo.

REM ==================================================================
REM 4. Inno Setup compile
REM ==================================================================
if "%SKIP_INSTALLER%"=="1" (
    echo [4/4] Skipped (no Inno Setup)
    goto :done
)

echo [4/4] Inno Setup compiling installer...
echo.

"%ISCC%" "installers\TankTrouble.iss"
if errorlevel 1 (
    echo.
    echo [ERROR] Inno Setup failed. See above.
    pause
    exit /b 1
)

echo.
echo        Installer done ^-> installers\Output\
echo.

REM ==================================================================
REM done
REM ==================================================================
:done
echo ============================================
echo   Build complete!
echo ============================================
echo.
if exist "dist\TankTrouble\TankTrouble.exe" (
    echo   Green build:  dist\TankTrouble\TankTrouble.exe
)
if exist "installers\Output\" (
    for %%f in ("installers\Output\*.exe") do (
        echo   Installer:    installers\Output\%%~nxf  [%%~zf bytes]
    )
)
echo.
echo Opening output directories...
start "" "dist" 2>nul
if exist "installers\Output" start "" "installers\Output"

pause
endlocal
