"""Create the portable Windows ZIP from the verified PyInstaller application."""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from yunkaoxing import __version__


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="创建云考星助手解压即用包")
    parser.add_argument("--app-dir", type=Path, default=ROOT / "dist" / f"v{__version__}" / "YunKaoXing")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "releases")
    args = parser.parse_args()
    app_dir, output = args.app_dir.resolve(), args.output.resolve()
    required = [app_dir / "YunKaoXing.exe", app_dir / "_internal" / "python311.dll",
                app_dir / "_internal" / "PySide6" / "Qt6Core.dll"]
    for path in required:
        if not path.is_file():
            raise SystemExit(f"缺少打包文件：{path}")
    output.mkdir(parents=True, exist_ok=True)
    extras = {}
    for name, option in (("启动软件.cmd", ""), ("启动演示.cmd", " --demo"), ("启动模拟小程序.cmd", " --simulator")):
        extras[name] = ("@echo off\r\ncd /d \"%~dp0\"\r\nstart \"\" \"%~dp0YunKaoXing.exe\"" + option + "\r\n").encode("ascii")
    extras["解压后先看这里.txt"] = (f"云考星助手 v{__version__} · Windows 64 位免安装版\r\n\r\n"
        "1. 将整个 ZIP 完整解压到自己的可写目录。\r\n"
        "2. 双击“启动演示.cmd”，进入工作台后点击“启动批次”，即可演练模拟登录与 100 题考试。\r\n"
        "3. 双击“启动模拟小程序.cmd”，可以手动操作区县选择、模拟登录、考试和交卷。\r\n"
        "4. 双击“启动软件.cmd”或 YunKaoXing.exe，可导入自己的人员表和 PDF 题库。\r\n\r\n"
        "软件已包含 Python、Qt 和运行依赖，无需安装 Python 或额外下载依赖。\r\n"
        "请保留 YunKaoXing.exe 旁的 _internal 文件夹；不要只复制单个 EXE。\r\n"
        "邮件：在“连接与保存 → 邮件设置”配置 SMTP 发件邮箱和授权码。下次启动时后台通知 1564532736@qq.com；启动通知只含版本和时间。导入人员成功后，另行将原始 Excel 作为附件发送到同一邮箱，保留全部工作表和人员资料；失败不阻塞操作。包内没有发件凭据。\r\n"
        "授权码由 Windows 当前用户加密保存在 data/startup-mail.json；换电脑或 Windows 用户需重新配置。离线自检不会发送邮件。\r\n"
        "使用期限：北京时间 2027 年 6 月 30 日仍可使用，2027 年 7 月 1 日 00:00 起禁止启动。依据电脑系统时间检查；已打开的工作台到期后不能启动新批次。\r\n"
        "本地模拟无需微信登录或网络连接。所有演示身份与题目均为合成数据。\r\n"
        "真实登录：先登录电脑微信，并在微信使用一次目标小程序。关闭工作台“测试开关”，在“连接与保存”核对“小程序名称”，导入人员表后启动批次。\r\n"
        "“后台运行”默认不勾选，可以看到小程序打开和登录过程。未启动的小程序会通过微信的小程序列表按配置名称打开；首次打开需要短暂置前微信。\r\n"
        "程序按人员资料选择区县，必要时搜索或滚动；填写姓名、身份证号并回读核对后登录。进入个人资料核对身份后，确认退出并返回区县选择，再为下一人执行完整流程，最后一人也会退出。当前没有考试安排也可完成登录验证。\r\n"
        "“确保最低分”和“得分范围”在真实模式也可编辑、保存；当前真实模式仅验证登录，会保留考试目标设置，尚不执行真实考试目标分数控制。\r\n"
        "登录或连接异常会记录具体原因；能明确识别的登录错误提示会关闭并等待消失，再继续下一人，最后一人失败也会清理提示。\r\n"
        "全部人员正常处理完毕后关闭目标小程序窗口，保留微信主程序；模拟窗口同样在批次正常结束后关闭。手动停止、异常中断或仅检测环境时保留窗口。关闭未能确认时查看运行记录中的原因。\r\n"
        "2026-11-01 的 v0.1.3 已用同一授权账号、同一考区连续两轮完成前台登录核对与退出；其他真实账号、考区及后台运行尚未现场验证。2026-11-02 的 v0.1.4 新增真实窗口关闭和错误提示清理目前仅完成模拟控件回归，尚未使用新版 EXE 现场验证。后台操作要求小程序已打开且未最小化。\r\n"
        "真实云考星考试流程仍需考试任务开放后联调。\r\n"
        "miniprogram 文件夹是微信原生小程序源码，仅在需要开发者工具调试时使用。\r\n"
        "详细说明见 README.md，验证范围见 验证记录.md。\r\n").encode("utf-8-sig")
    excluded = {"data", "results", "build", ".venv", ".pytest_cache", "__pycache__"}
    files = []
    manifest = {"name": "YunKaoXing", "version": __version__, "platform": "Windows-x64",
                "portable": True, "files": []}
    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(app_dir)
        if relative.parts[0] in excluded or relative.as_posix() in extras:
            continue
        if not path.resolve().is_relative_to(app_dir):
            raise SystemExit("应用目录包含指向外部的文件，未打包")
        files.append((path, relative.as_posix()))
        manifest["files"].append({"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    for name, content in extras.items():
        manifest["files"].append({"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"YunKaoXing-Windows-x64-v{__version__}-Portable.zip"
    target = output / filename
    temporary = output / (filename + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, name in files:
            archive.write(path, "YunKaoXing/" + name)
        for name, content in extras.items():
            archive.writestr("YunKaoXing/" + name, content)
        archive.writestr("YunKaoXing/文件校验清单.json", manifest_data)
    with zipfile.ZipFile(temporary) as archive:
        broken = archive.testzip()
        if broken:
            raise SystemExit(f"ZIP 完整性检查未通过：{broken}")
    os.replace(temporary, target)
    (output / "release-manifest.json").write_bytes(manifest_data)
    (output / "SHA256SUMS.txt").write_text(f"{sha256(target)}  {filename}\n", encoding="ascii")
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size,
                      "files": len(manifest["files"]) + 1, "sha256": sha256(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
