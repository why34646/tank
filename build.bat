@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   TankTrouble Build
echo   Working dir: %CD%
echo ============================================
echo.

REM ---- 1. dependency check ----
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
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [WARN] Inno Setup not found. Only green build, skipping installer.
    set "SKIP=1"
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
            echo [WARN] png-to-ico failed. Skipping icon.
        ) else (
            echo        Icon OK
        )
    )
)
echo.

REM ---- 2. clean old artifacts ----
echo [2/4] Cleaning old artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installers\Output rmdir /s /q installers\Output
echo        Cleaned
echo.

REM ---- 3. PyInstaller ----
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
echo        PyInstaller done -^> dist\TankTrouble\
echo.

REM ---- 4. Inno Setup ----
if "%SKIP%"=="1" goto step4_skip
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
echo        Installer done -^> installers\Output\
echo.
goto step4_done

:step4_skip
echo [4/4] Skipped (no Inno Setup)

:step4_done

REM ---- done ----
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
