@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\v0.1.6\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.6\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\v0.1.5\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.5\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\v0.1.4\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.4\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\v0.1.3\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.3\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\v0.1.2\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.2\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\v0.1.1\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\v0.1.1\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist "dist\YunKaoXing\YunKaoXing.exe" (
    start "" "dist\YunKaoXing\YunKaoXing.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b
)
echo 请先按 README.md 安装项目依赖，再运行本文件。
pause
