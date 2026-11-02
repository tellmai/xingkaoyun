import argparse
import json
import os
import sys
from pathlib import Path

from yunkaoxing import APP_NAME, __version__
from yunkaoxing.lifetime import china_now, ensure_available


def demo_data():
    from yunkaoxing.demo import demo_data as create
    return create()


def main():
    parser = argparse.ArgumentParser(description="云考星微信小程序桌面助手")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--demo", action="store_true", help="显示合成数据，默认使用本地模拟模式")
    parser.add_argument("--simulator", action="store_true", help="单独打开可手动登录、考试的模拟小程序")
    parser.add_argument("--smoke-test", metavar="OUTPUT", help="离线自检并保存 GUI 截图，不连接微信")
    parser.add_argument("--smoke-bank", metavar="PDF", help="离线自检时使用指定 PDF 题库，不将文件写入程序包")
    parser.add_argument("--smoke-concurrency", type=int, choices=range(1, 31), default=1, metavar="N", help="离线自检的独立模拟会话数量（1–30）")
    args = parser.parse_args()
    if args.smoke_bank and not args.smoke_test:
        parser.error("--smoke-bank 须与 --smoke-test 一起使用")
    if args.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    started_at = china_now()
    expired = None
    try:
        ensure_available(started_at)
    except ValueError as exc:
        expired = str(exc)
    if expired and args.smoke_test:
        output = Path(args.smoke_test).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "smoke-result.json").write_text(json.dumps({"ok": False, "error": expired}, ensure_ascii=False), encoding="utf-8")
        return 3
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    family = "Microsoft YaHei UI"
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    if font.is_file():
        fid = QFontDatabase.addApplicationFont(str(font))
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            family = families[0]
    app.setFont(QFont(family, 10))
    if expired:
        QMessageBox.critical(None, "软件已到期", expired)
        return 3
    frozen = getattr(sys, "frozen", False)
    home = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
    resources = Path(getattr(sys, "_MEIPASS", home))
    if args.simulator:
        from yunkaoxing.simulator import SimulatorWindow
        _, bank = demo_data()
        simulator = SimulatorWindow(bank.questions)
        simulator.show()
        from yunkaoxing.mail_dialog import watch_startup_notification
        watch_startup_notification(simulator, home / "data" / "startup-mail.json", started_at,
                                   simulator.statusBar().showMessage)
        return app.exec()
    from yunkaoxing.gui import MainWindow
    window = MainWindow(home, resources)
    if args.smoke_test:
        output = Path(args.smoke_test).resolve()
        output.mkdir(parents=True, exist_ok=True)
        window.settings_path = output / "test-settings.json"
    if args.demo or args.smoke_test:
        window.load_demo()
        window.notice.setText("当前使用合成演示数据 · 未连接微信 · 本地模拟结果不代表真实考试成绩")
    if args.smoke_test:
        window.background.setChecked(True)
        window.concurrency.setValue(args.smoke_concurrency)
    imported_bank = None
    if args.smoke_bank:
        from yunkaoxing.importers import load_bank
        imported_bank = load_bank(args.smoke_bank)
        window.imported("bank", imported_bank)
    window.show()
    if not args.smoke_test:
        window.startup_notification(started_at)
    state = {"exit": 0}
    if args.smoke_test:
        def finished_smoke():
            try:
                store = window.store
                if not store or any(r.status != "completed" for r in store.records):
                    raise RuntimeError("模拟登录与考试批次没有全部完成：" + window.notice.text())
                app.processEvents()
                window.grab().save(str(output / "workspace.png"))
                # grab() also renders a hidden simulator for offline visual QA.
                window.simulator.grab().save(str(output / "simulator-result.png"))
                (output / "smoke-result.json").write_text(json.dumps({"ok": True, "mode": "full-mini-program-simulation", "people": len(store.records), "questions_per_person": 100, "scores": [r.score for r in store.records], "bank_questions": len(window.questions), "bank_anomalies": len(imported_bank.anomalies) if imported_bank else 0, "concurrency": window.worker.options.concurrency, "background": window.worker.options.background, "export": str(store.folder)}, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                state["exit"] = 1
                (output / "smoke-result.json").write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), encoding="utf-8")
            finally:
                window.close()
                app.quit()
        def smoke():
            from yunkaoxing.models import IdentityImport
            window.imported("identity", IdentityImport(window.candidates[:3], [], "合成演示"))
            window.output.setText(str(output))
            window.start_batch()
            if window.worker is None:
                state["exit"] = 1
                app.quit()
                return
            window.worker.finished.connect(finished_smoke)
            QTimer.singleShot(60000, lambda: window.worker.control.stop.set() if window.worker.isRunning() else None)
        QTimer.singleShot(250, smoke)
    result = app.exec()
    return state["exit"] or result


if __name__ == "__main__":
    raise SystemExit(main())
