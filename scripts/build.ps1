param([string]$NodePath = 'node')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& '.\.venv\Scripts\python.exe' -m pytest
if ($LASTEXITCODE -ne 0) { throw '自动化测试未通过' }
& $NodePath --test tests\miniprogram.test.cjs
if ($LASTEXITCODE -ne 0) { throw '小程序逻辑测试未通过；测试需要 Node.js 18 或更新版本' }
$appVersion = (& '.\.venv\Scripts\python.exe' -c 'from yunkaoxing import __version__; print(__version__)').Trim()
$distPath = "dist\v$appVersion"
$appPath = "$distPath\YunKaoXing"
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --distpath $distPath YunKaoXing.spec
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
Copy-Item -LiteralPath README.md,验证记录.md,THIRD_PARTY_NOTICES.md -Destination $appPath -Force
Copy-Item -LiteralPath miniprogram -Destination $appPath -Recurse -Force
& '.\.venv\Scripts\python.exe' scripts\package_release.py
if ($LASTEXITCODE -ne 0) { throw '免安装 ZIP 生成失败' }
$archivePath = "dist\releases\YunKaoXing-Windows-x64-v$appVersion-Portable.zip"
& '.\.venv\Scripts\python.exe' scripts\verify_portable.py $archivePath
if ($LASTEXITCODE -ne 0) { throw '解压后运行验证失败' }
Write-Output "完整软件目录：$appPath，请完整保留 _internal。"
Write-Output "解压即用包：$archivePath"
