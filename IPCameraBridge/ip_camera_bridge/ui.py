"""Vietnamese desktop interface; all video work remains in child processes."""
from pathlib import Path
import logging
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .config import load_config, save_config
from .cameras import CameraGroup
from .sources import SourceSpec
from .windows_settings import load_profiles, save_profiles, set_startup, startup_enabled, MAX_CAMERAS


def data_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IPCameraBridge"


class MainWindow(QMainWindow):
    def __init__(self, config_path=None, automation=True):
        super().__init__()
        self.setWindowTitle("IP Camera Bridge")
        self.resize(1100, 930)
        self.setMinimumSize(850, 780)
        self.config_path = Path(config_path) if config_path else data_directory() / "config.json"
        config = load_config(self.config_path)
        self.group = CameraGroup(
            *( (1920, 1080) if config["resolution"] == "1080p" else (1280, 720) )
        )
        self.automation = automation
        self.profiles_path = self.config_path.with_name('cameras.dat')
        profile_error = ''
        saved = None
        if automation:
            try:
                saved = load_profiles(self.profiles_path)
            except (ValueError, OSError):
                profile_error = 'Không đọc được danh sách camera đã mã hóa; hãy nhập và lưu lại trên tài khoản Windows này.'
        self._had_profiles = saved is not None
        self.profiles = saved['cameras'] if saved else [{
            'name': 'Camera 1', 'kind': config['source_kind'],
            'address': config['file_path'] if config['source_kind'] == 'file' else '',
            'username': '', 'password': '',
        }]
        for _ in self.profiles[1:]:
            self.group.add()
        self._editing_index = 0
        self._closing = False
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)
        heading = QLabel("IP Camera Bridge")
        heading.setStyleSheet("font-size: 26px; font-weight: 700;")
        layout.addWidget(heading)
        subtitle = QLabel("Nguồn video → Xem trước → OBS Virtual Camera → Google Meet")
        subtitle.setStyleSheet("color: #91a6c2;")
        layout.addWidget(subtitle)

        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel('Camera xuất'))
        self.camera_selector = QComboBox()
        self.camera_selector.setAccessibleName('Chọn camera xuất ra webcam ảo')
        for profile in self.profiles:
            self.camera_selector.addItem(profile['name'])
        self.add_button = QPushButton('Thêm camera')
        self.remove_button = QPushButton('Xóa camera')
        self.camera_name = QLineEdit()
        self.camera_name.setPlaceholderText('Tên camera')
        self.camera_name.setAccessibleName('Tên camera đang chọn')
        camera_row.addWidget(self.camera_selector, 2)
        camera_row.addWidget(self.camera_name, 1)
        camera_row.addWidget(self.add_button)
        camera_row.addWidget(self.remove_button)
        layout.addLayout(camera_row)

        row = QHBoxLayout()
        self.source_kind = QComboBox()
        for label, value in (("Test Pattern", "test"), ("Video File", "file"), ("RTSP", "rtsp")):
            self.source_kind.addItem(label, value)
        self.source_kind.setAccessibleName("Chọn nguồn video")
        self.source_kind.setCurrentIndex(max(0, self.source_kind.findData(config["source_kind"])))
        self.resolution = QComboBox()
        self.resolution.addItem("720p · 1280 × 720", "720p")
        self.resolution.addItem("1080p · 1920 × 1080", "1080p")
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(config["resolution"])))
        self.resolution.setAccessibleName("Độ phân giải đầu ra")
        row.addWidget(QLabel("Nguồn"))
        row.addWidget(self.source_kind, 1)
        row.addWidget(QLabel("Đầu ra"))
        row.addWidget(self.resolution, 1)
        row.addWidget(QLabel("25 fps"))
        layout.addLayout(row)

        self.source_options = QStackedWidget()
        self.source_options.setMinimumHeight(128)
        pattern = QLabel("Hình kiểm tra có màu, chuyển động, thời gian và số khung hình.\nKhông cần camera hoặc file video.")
        pattern.setWordWrap(True)
        self.source_options.addWidget(pattern)

        file_page = QWidget()
        file_layout = QHBoxLayout(file_page)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.file_path = QLineEdit(config["file_path"])
        self.file_path.setPlaceholderText("Chọn file video — phát lặp theo tốc độ gốc")
        self.file_path.setAccessibleName("Đường dẫn file video")
        self.browse = QPushButton("Chọn file…")
        self.browse.clicked.connect(self.choose_file)
        file_layout.addWidget(self.file_path, 1)
        file_layout.addWidget(self.browse)
        self.source_options.addWidget(file_page)

        rtsp_page = QWidget()
        rtsp_layout = QFormLayout(rtsp_page)
        rtsp_layout.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit()
        self.url.setEchoMode(QLineEdit.EchoMode.Password)
        self.url.setPlaceholderText("rtsp://địa-chỉ-camera:554/đường-dẫn")
        self.url.setToolTip("Nhập tài khoản riêng bên dưới. Chỉ lưu khi bật Ghi nhớ camera; dữ liệu được Windows mã hóa.")
        self.username = QLineEdit()
        self.username.setEchoMode(QLineEdit.EchoMode.Password)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Mật khẩu nguyên bản; giữ nguyên ký tự @")
        rtsp_layout.addRow("URL RTSP (TCP)", self.url)
        rtsp_layout.addRow("Tên đăng nhập", self.username)
        rtsp_layout.addRow("Mật khẩu", self.password)
        self.source_options.addWidget(rtsp_page)
        self.source_options.setCurrentIndex(self.source_kind.currentIndex())
        self.source_kind.currentIndexChanged.connect(self.source_options.setCurrentIndex)
        layout.addWidget(self.source_options)

        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Kết nối nguồn")
        self.connect_button.setMinimumHeight(40)
        self.connect_button.clicked.connect(self.toggle_source)
        self.output_button = QPushButton("Bắt đầu webcam ảo")
        self.output_button.setMinimumHeight(40)
        self.output_button.clicked.connect(self.toggle_output)
        self.output_button.setEnabled(False)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.output_button)
        layout.addLayout(buttons)
        all_buttons = QHBoxLayout()
        self.connect_all_button = QPushButton('Kết nối tất cả')
        self.disconnect_all_button = QPushButton('Ngắt tất cả')
        self.connect_all_button.clicked.connect(self.connect_all)
        self.disconnect_all_button.clicked.connect(self.disconnect_all)
        all_buttons.addWidget(self.connect_all_button)
        all_buttons.addWidget(self.disconnect_all_button)
        layout.addLayout(all_buttons)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.preview.setStyleSheet("background: #000; border: 1px solid #34445a; border-radius: 6px;")
        self.preview.setAccessibleName("Xem trước video")
        layout.addWidget(self.preview, 1)
        self.source_status = QLabel("Nguồn: Chưa kết nối.")
        self.camera_status = QLabel("Webcam: Chưa bật.")
        self.fps_label = QLabel("Xử lý: 0.0 fps")
        self.source_status.setWordWrap(True)
        self.camera_status.setWordWrap(True)
        layout.addWidget(self.source_status)
        layout.addWidget(self.camera_status)
        layout.addWidget(self.fps_label)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #ffbd86;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        preferences = QHBoxLayout()
        self.remember = QCheckBox('Ghi nhớ camera (mã hóa Windows)')
        self.auto_connect = QCheckBox('Tự kết nối và bật webcam')
        self.start_windows = QCheckBox('Mở cùng Windows')
        self.save_button = QPushButton('Lưu cài đặt')
        self.remember.setChecked(saved is not None)
        self.auto_connect.setChecked(bool(saved and saved['auto_connect']))
        self.auto_connect.setEnabled(saved is not None and automation)
        try:
            self.start_windows.setChecked(automation and startup_enabled())
        except OSError:
            profile_error = 'Không đọc được tùy chọn mở cùng Windows.'
        self.remember.setEnabled(automation)
        self.start_windows.setEnabled(automation)
        self.save_button.setEnabled(automation)
        self.start_windows.setToolTip('Mở sau khi đăng nhập Windows. Giữ file .exe tại vị trí cố định; bấm Lưu cài đặt để áp dụng.')
        self.auto_connect.setToolTip('Kết nối tất cả camera và xuất camera đang chọn khi mở ứng dụng.')
        self.remember.toggled.connect(self.remember_changed)
        self.save_button.clicked.connect(self.save_settings)
        for widget in (self.remember, self.auto_connect, self.start_windows, self.save_button):
            preferences.addWidget(widget)
        layout.addLayout(preferences)
        help_text = QLabel(
            'Cần cài OBS riêng. Dừng “Virtual Camera” trong OBS trước khi bật ở đây. '
            'Trong Meet chọn “OBS Virtual Camera”; dùng micro riêng. '
            '<a href="https://obsproject.com/kb/virtual-camera-troubleshooting">Hướng dẫn OBS</a>'
        )
        help_text.setOpenExternalLinks(True)
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #111b2a; color: #ecf1f9; font-size: 13px; }
            QLineEdit, QComboBox { background: #1a2a40; padding: 7px; border: 1px solid #40516a; border-radius: 4px; }
            QPushButton { background: #245798; border: 1px solid #487ab8; padding: 8px 14px; border-radius: 5px; }
            QPushButton:hover { background: #306cb9; }
            QPushButton:disabled { background: #243145; color: #8b9aac; border-color: #34445a; }
            QLineEdit:focus, QComboBox:focus, QPushButton:focus { border: 2px solid #88beff; }
        """)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.refresh)
        self.load_camera_fields(0)
        self.camera_selector.currentIndexChanged.connect(self.select_camera)
        self.add_button.clicked.connect(self.add_camera)
        self.remove_button.clicked.connect(self.remove_camera)
        if saved:
            self.camera_selector.setCurrentIndex(saved['selected'])
        self.error_label.setText(profile_error)
        self.timer.start()
        self.refresh()
        if saved and saved['auto_connect'] and automation:
            QTimer.singleShot(0, self.start_saved_cameras)

    @property
    def bridge(self):
        return self.group.current

    def capture_camera_fields(self):
        profile = self.profiles[self._editing_index]
        kind = self.source_kind.currentData()
        profile.update(name=self.camera_name.text().strip() or f'Camera {self._editing_index + 1}',
                       kind=kind, address=self.file_path.text().strip() if kind == 'file' else self.url.text().strip(),
                       username=self.username.text(), password=self.password.text())

    def load_camera_fields(self, index):
        profile = self.profiles[index]
        self.camera_name.setText(profile['name'])
        self.source_kind.setCurrentIndex(self.source_kind.findData(profile['kind']))
        self.file_path.setText(profile['address'] if profile['kind'] == 'file' else '')
        self.url.setText(profile['address'] if profile['kind'] == 'rtsp' else '')
        self.username.setText(profile['username'])
        self.password.setText(profile['password'])

    def select_camera(self, index):
        if index < 0 or self._closing:
            return
        self.capture_camera_fields()
        self._editing_index = index
        self.group.select(index)
        self.load_camera_fields(index)
        self.refresh()

    def add_camera(self):
        self.capture_camera_fields()
        try:
            self.group.add()
        except RuntimeError as error:
            self.error_label.setText(str(error))
            return
        self.profiles.append({'name': f'Camera {len(self.profiles) + 1}', 'kind': 'rtsp',
                              'address': '', 'username': '', 'password': ''})
        self.camera_selector.addItem(self.profiles[-1]['name'])
        self.camera_selector.setCurrentIndex(len(self.profiles) - 1)

    def remove_camera(self):
        index = self.group.selected
        try:
            self.group.remove(index)
        except RuntimeError as error:
            self.error_label.setText(str(error))
            return
        del self.profiles[index]
        self.camera_selector.blockSignals(True)
        self.camera_selector.removeItem(index)
        self.camera_selector.setCurrentIndex(self.group.selected)
        self.camera_selector.blockSignals(False)
        self._editing_index = self.group.selected
        self.load_camera_fields(self.group.selected)
        self.refresh()

    def sync_resolution(self):
        width, height = (1920, 1080) if self.resolution.currentData() == '1080p' else (1280, 720)
        if (width, height) != (self.group.shared.width, self.group.shared.height):
            self.group.set_resolution(width, height)

    def connect_all(self):
        self.capture_camera_fields()
        self.error_label.clear()
        try:
            self.sync_resolution()
            for index, (camera, profile) in enumerate(zip(self.group.cameras, self.profiles)):
                if not camera.source.active:
                    try:
                        camera.connect(SourceSpec(**{key: profile[key] for key in ('kind', 'address', 'username', 'password')}))
                    except (ValueError, RuntimeError) as error:
                        self.error_label.setText(f'Camera {index + 1}: {error}')
        except (ValueError, RuntimeError) as error:
            self.error_label.setText(str(error))
        self.refresh()

    def disconnect_all(self):
        self.group.disconnect_all()
        self.refresh()

    def start_saved_cameras(self):
        if not self._closing:
            self.group.auto_output = True
            self.connect_all()

    def remember_changed(self, enabled):
        self.auto_connect.setEnabled(enabled and self.automation)
        if not enabled:
            self.auto_connect.setChecked(False)

    def save_settings(self):
        if not self.automation:
            return True
        self.capture_camera_fields()
        try:
            if self.remember.isChecked():
                save_profiles(self.profiles_path, {'version': 1, 'cameras': self.profiles,
                              'selected': self.group.selected, 'auto_connect': self.auto_connect.isChecked()})
                self._had_profiles = True
            elif self._had_profiles:
                self.profiles_path.unlink(missing_ok=True)
                self._had_profiles = False
            if self.start_windows.isChecked() or startup_enabled():
                set_startup(self.start_windows.isChecked())
            save_config(self.config_path, {'source_kind': self.source_kind.currentData(),
                        'file_path': self.file_path.text(), 'resolution': self.resolution.currentData()})
        except (ValueError, OSError):
            self.error_label.setText('Không lưu được cài đặt hoặc đăng ký tự khởi động. Kiểm tra quyền Windows và vị trí file ứng dụng.')
            return False
        self.error_label.setText('Đã lưu cài đặt. Camera được mã hóa cho tài khoản Windows này.' if self.remember.isChecked() else 'Đã lưu cài đặt; thông tin đăng nhập không được lưu.')
        return True

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn video", self.file_path.text(),
            "Video (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;Tất cả file (*)",
        )
        if path:
            self.file_path.setText(path)

    def toggle_source(self):
        self.error_label.clear()
        if self.bridge.source.active:
            self.group.stop_output()
            self.bridge.disconnect()
        else:
            try:
                self.sync_resolution()
                self.capture_camera_fields()
                kind = self.source_kind.currentData()
                address = self.file_path.text().strip() if kind == "file" else self.url.text().strip()
                self.bridge.connect(SourceSpec(
                    kind=kind, address=address,
                    username=self.username.text(), password=self.password.text(),
                ))
            except (ValueError, RuntimeError) as error:
                self.error_label.setText(str(error))
            except Exception:
                self.error_label.setText("Không mở được nguồn. Hãy kiểm tra cấu hình.")
        self.refresh()

    def toggle_output(self):
        self.error_label.clear()
        if self.group.output.active:
            self.group.stop_output()
        else:
            try:
                self.group.start_output()
            except RuntimeError as error:
                self.error_label.setText(str(error))
        self.refresh()

    def refresh(self):
        self.group.tick()
        active = self.bridge.source.active
        output_active = self.group.output.active
        stopping = self.bridge.source_state == "stopping"
        self.connect_button.setText("Đang ngắt…" if stopping else ("Ngắt kết nối" if active else "Kết nối nguồn"))
        self.connect_button.setEnabled(not stopping and not self._closing)
        self.output_button.setText("Dừng webcam ảo" if output_active else "Bắt đầu webcam ảo")
        self.output_button.setEnabled(
            not self._closing and self.group.output_state != "stopping"
            and (output_active or self.bridge.source_state == "connected")
        )
        self.source_kind.setEnabled(not active and not self._closing)
        self.source_options.setEnabled(not active and not self._closing)
        self.resolution.setEnabled(not self.group.active and not self._closing)
        self.add_button.setEnabled(not self._closing and len(self.profiles) < MAX_CAMERAS)
        self.remove_button.setEnabled(not self._closing and not active and len(self.profiles) > 1)
        self.camera_selector.setEnabled(not self._closing)
        self.camera_name.setEnabled(not self._closing)
        self.connect_all_button.setEnabled(not self._closing)
        self.disconnect_all_button.setEnabled(not self._closing and self.group.active)
        for index, camera in enumerate(self.group.cameras):
            state = {'connected': 'Có hình', 'connecting': 'Đang kết nối', 'stopping': 'Đang ngắt',
                     'stopped': 'Đã ngắt', 'retrying': 'Thử lại', 'lost': 'Mất kết nối', 'error': 'Lỗi'}.get(camera.source_state, '')
            self.camera_selector.setItemText(index, f"{self.profiles[index]['name']} — {state}")
        self.source_status.setText("Nguồn: " + self.bridge.source_message)
        self.camera_status.setText("Webcam: " + self.group.output_message)
        self.fps_label.setText(f"Xử lý: {self.bridge.processing_fps:.1f} fps · Đầu ra: 25 fps")
        rgb = self.bridge.preview()
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        ))
        if self._closing and self.group.closed:
            self.timer.stop()
            self.close()

    def closeEvent(self, event):
        if self.group.closed:
            event.accept()
            return
        event.ignore()
        if not self._closing:
            if self.automation and not self.save_settings():
                answer = QMessageBox.warning(self, 'Không lưu được cài đặt',
                    'Cài đặt mới chưa được lưu đầy đủ. Đóng ứng dụng và giữ dữ liệu đã lưu trước đó?',
                    QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Discard:
                    return
            self._closing = True
            try:
                save_config(self.config_path, {
                    "source_kind": self.source_kind.currentData(),
                    "file_path": self.file_path.text(),
                    "resolution": self.resolution.currentData(),
                })
            except OSError:
                logging.getLogger("ip_camera_bridge").warning("Could not save non-sensitive preferences")
            self.password.clear()
            self.username.clear()
            self.url.clear()
            for profile in self.profiles:
                profile.update(address='', username='', password='')
            self.group.close()

