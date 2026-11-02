@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\v0.1.6\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.6\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\v0.1.5\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.5\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\v0.1.4\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.4\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\v0.1.3\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.3\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\v0.1.2\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.2\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\v0.1.1\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.1\YunKaoXing\YunKaoXing.exe" --simulator
    exit /b
)
if exist "dist\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\YunKaoXing\YunKaoXing.exe" --simulator
) else (
    start "" ".venv\Scripts\pythonw.exe" "main.py" --simulator
)
