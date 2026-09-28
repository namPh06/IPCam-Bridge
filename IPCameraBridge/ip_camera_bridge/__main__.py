"""Application entry point plus repeatable offscreen deployment smoke test."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="IP Camera Bridge")
    parser.add_argument("--smoke-test", metavar="DIRECTORY", help="Offscreen pattern/GUI smoke; save JSON and PNG")
    parser.add_argument("--source-file", help="Use a video file in the smoke test")
    parser.add_argument("--probe-webcam", action="store_true", help="Try the installed OBS backend during smoke")
    parser.add_argument("--smoke-cameras", type=int, choices=range(1, 17), default=1,
                        help="Number of concurrent sources in the smoke test")
    args = parser.parse_args()
    if args.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .config import setup_logging
    from .ui import MainWindow, data_directory

    app = QApplication(sys.argv[:1])
    if args.smoke_test and os.name == "nt":
        # Qt offscreen does not enumerate Windows system fonts.
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"))
        app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("IP Camera Bridge")
    app.setOrganizationName("IPCameraBridge")
    destination = Path(args.smoke_test).resolve() if args.smoke_test else data_directory()
    destination.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(destination / "bridge.log")

    def report_exception(kind, value, traceback):
        # No exception text/locals: third-party errors can contain RTSP credentials.
        logger.error("Unhandled application error type=%s", kind.__name__)
        if not args.smoke_test:
            QMessageBox.critical(None, "IP Camera Bridge", "Ứng dụng gặp lỗi. Hãy khởi động lại và kiểm tra cấu hình.")
        app.exit(1)

    sys.excepthook = report_exception
    window = MainWindow(config_path=destination / "config.json", automation=not bool(args.smoke_test))
    window.show()
    if args.smoke_test:
        for index in range(args.smoke_cameras):
            if index:
                window.add_camera()
            if args.source_file:
                window.source_kind.setCurrentIndex(window.source_kind.findData("file"))
                window.file_path.setText(str(Path(args.source_file).resolve()))
            else:
                window.source_kind.setCurrentIndex(0)
        window.connect_all()
        started = time.monotonic()
        ready_at = None
        output_started = False
        finished = False

        def check():
            nonlocal ready_at, output_started, finished
            if finished:
                return
            now = time.monotonic()
            if all(camera.source_state == "connected" for camera in window.group.cameras) and ready_at is None:
                ready_at = now
                window.camera_selector.setCurrentIndex(0)
            if ready_at is not None and args.probe_webcam and not output_started:
                output_started = True
                window.group.start_output()
            backend_done = not args.probe_webcam or window.group.output_state in ("running", "error")
            if (ready_at is not None and now - ready_at > 2 and backend_done) or now - started > 20:
                finished = True
                result = {
                    "source": window.source_kind.currentData(),
                    "source_state": window.bridge.source_state,
                    "camera_states": [camera.source_state for camera in window.group.cameras],
                    "output_state": window.group.output_state,
                    "output_message": window.group.output_message,
                    "processing_fps": round(window.bridge.processing_fps, 2),
                    "preview_size": list(window.bridge.preview().shape),
                    "qt_responsive": True,
                }
                window.grab().save(str(destination / "preview.png"))
                (destination / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Smoke finished source=%s output=%s", result["source_state"], result["output_state"])
                window.close()
        timer = QTimer(window)
        timer.timeout.connect(check)
        timer.start(50)
    result = app.exec()
    if args.smoke_test:
        result_path = destination / "result.json"
        if not result_path.exists():
            return 1
        data = json.loads(result_path.read_text(encoding="utf-8"))
        return 0 if all(state == "connected" for state in data["camera_states"]) and window.group.closed else 1
    return result


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

