"""Extract a release to a fresh path and exercise the bundled EXE without Python on PATH."""
import argparse
import hashlib
import json
import os
import subprocess
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--bank", type=Path, help="使用外部 PDF 验证解压后的题库导入及模拟考试，不将该 PDF 放入包内")
    parser.add_argument("--concurrency", type=int, choices=range(1, 31), default=1)
    args = parser.parse_args()
    archive_path = args.archive.resolve()
    destination = ROOT / "build" / ("免安装解压验证 " + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError("ZIP 包含不安全路径")
        archive.extractall(destination)
    app = destination / "YunKaoXing"
    manifest = json.loads((app / "文件校验清单.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = app / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise RuntimeError("解压文件缺失或大小不符：" + item["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("解压文件校验失败：" + item["path"])
    for filename in ("启动软件.cmd", "启动演示.cmd", "启动模拟小程序.cmd"):
        content = (app / filename).read_text(encoding="ascii")
        if ".venv" in content or "python" in content.casefold() or "dist\\" in content:
            raise RuntimeError("便携启动入口仍引用源码环境")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    env["PATH"] = str(Path(env.get("SystemRoot", "C:/Windows")) / "System32")
    output = destination / "自检结果"
    command = [str(app / "YunKaoXing.exe"), "--smoke-test", str(output), "--smoke-concurrency", str(args.concurrency)]
    if args.bank:
        command.extend(["--smoke-bank", str(args.bank.resolve())])
    result = subprocess.run(command,
                            cwd=app, env=env, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"解压后的程序自检退出码为 {result.returncode}")
    smoke = json.loads((output / "smoke-result.json").read_text(encoding="utf-8"))
    if not smoke.get("ok") or smoke.get("people") != 3 or smoke.get("questions_per_person") != 100:
        raise RuntimeError("解压后的模拟登录与考试流程未完整通过")
    summary = {"ok": True, "archive": str(archive_path), "extracted": str(app),
               "verified_files": len(manifest["files"]), "python_on_path": False,
               "people": smoke["people"], "questions_per_person": smoke["questions_per_person"],
               "scores": smoke["scores"], "bank_questions": smoke.get("bank_questions"),
               "bank_anomalies": smoke.get("bank_anomalies"), "concurrency": smoke.get("concurrency"),
               "background": smoke.get("background"), "exit_code": result.returncode}
    (archive_path.parent / "release-verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
