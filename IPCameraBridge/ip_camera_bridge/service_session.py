"""Owner-session publisher and tray UI; the service owns camera capture."""
import copy
import logging
import multiprocessing as mp
import queue
import re
import threading
import time
from uuid import uuid4
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, QByteArray
from pathlib import Path
from PySide6.QtGui import QFont, QImage, QPixmap, QIcon, QPainter, QColor, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QPushButton, QScrollArea, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget, QMessageBox, QStyledItemDelegate)

from .controller import ManagedProcess
from .config import validate_rtsp_url
from .frames import SharedFrame, status_frame
from .output import run_output
from .onvif_discovery import scan_range, scan_rtsp
from .rtsp_address import DEFAULT_PATH, expand_rtsp_address
from .service_config import migrate_profiles
from .service_ipc import ServiceClient
from .windows_settings import MAX_CAMERAS, load_profiles, set_startup

ERRORS = {'source_busy': 'Ngắt nguồn trước khi sửa thông tin kết nối hoặc độ phân giải.',
          'stale_revision': 'Cấu hình đã thay đổi. Bấm Tải lại rồi chỉnh sửa.',
          'save_failed': 'Không lưu được cấu hình; dữ liệu trước đó được giữ nguyên.',
          'invalid_request': 'Kiểm tra địa chỉ, tên và thông tin camera.',
          'offline': 'Không kết nối được dịch vụ camera. Mở Services, kiểm tra IP Camera Bridge đang chạy; nếu không khởi động được, cài lại ứng dụng. Ứng dụng đang tự thử lại.',
          'migration_failed': 'Không chuyển được cấu hình cũ. Dữ liệu cũ vẫn được giữ.',
          'busy': 'Service đang bận. Hãy thử lại.', 'timeout': 'Chưa nhận được xác nhận; bấm Tải lại để kiểm tra.'}

LOG = logging.getLogger('ip_camera_bridge')
PROBLEM_STATES = ('error', 'failed', 'retrying', 'lost')


def ui_icon(name, color='#52617b'):
    paths = {
        'camera': '<rect x="3" y="5" width="14" height="14" rx="3"/><circle cx="10" cy="12" r="3"/><path d="m17 9 4-3v12l-4-3"/>',
        'settings': '<circle cx="12" cy="12" r="3"/><path d="m9 3 6 0 1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z"/>',
        'refresh': '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
        'eye': '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
        'plus': '<path d="M12 5v14M5 12h14"/>',
        'network': '<circle cx="12" cy="12" r="2"/><path d="M8 8a6 6 0 0 0 0 8m8-8a6 6 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
        'play': '<path d="m7 4 13 8-13 8z"/>',
        'plug': '<path d="M8 3v5m8-5v5M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
        'history': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        'chevron': '<path d="m6 9 6 6 6-6"/>',
        'alert': '<circle cx="12" cy="12" r="9"/><path d="M12 6v7m0 3v1"/>',
    }
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pixmap)


def user_message(message):
    """Decoder messages are fixed/safe; hide diagnostic codes and explain recovery."""
    text = re.sub(r'\s*\[code=[^\]]*\]', '', message)
    if '401' in text:
        return 'Camera từ chối đăng nhập. Kiểm tra tên đăng nhập và mật khẩu; nhập mật khẩu nguyên bản, kể cả ký tự @.'
    if '403' in text:
        return 'Tài khoản không có quyền xem camera. Kiểm tra quyền xem luồng video trên trang quản trị camera.'
    if '404' in text:
        return 'Camera không tìm thấy luồng video. Kiểm tra đường dẫn RTSP hoặc mẫu đường dẫn của model camera.'
    if 'từ chối kết nối TCP' in text:
        return 'Camera từ chối kết nối. Kiểm tra cổng RTSP đã bật trên camera và cổng trong URL có đúng không.'
    if 'bộ giải mã' in text:
        return 'Không giải mã được video. Chọn H.264 trên camera và thử lại; nếu vẫn lỗi, cập nhật hoặc cài lại ứng dụng.'
    if 'kết thúc luồng video' in text:
        return 'Camera đã ngừng gửi video. Kiểm tra camera có bị khởi động lại hoặc vượt giới hạn số kết nối không; thử lại URL trong VLC.'
    if any(word in text.lower() for word in ('không mở', 'không đọc', 'không hợp lệ', 'hết thời gian', 'mất kết nối')):
        return text + '\nCần kiểm tra: IP, cổng và đường dẫn RTSP; thử cùng URL trong VLC, kiểm tra mạng/VLAN và quyền truy cập camera.'
    return text


class ServiceSession:
    def __init__(self, owner_sid, start_worker=True):
        self.owner_sid = owner_sid
        self.stop = threading.Event()
        self.commands = queue.Queue(16)
        self.results = queue.Queue(16)
        self.lock = threading.Lock()
        self.incoming = None
        self.status = None
        self.generation = 0
        self.pid = None
        self.shared = SharedFrame(mp.get_context('spawn'), 1280, 720)
        self.output = ManagedProcess(mp.get_context('spawn'))
        self.output_message = 'Webcam ảo chưa bật.'
        self.want_output = None
        self.output_running = False
        self.retry_at, self.attempt = 0, 0
        self.image = status_frame(1280, 720, 'DANG CHO SERVICE')
        self.image_at = time.monotonic()
        self.window = None
        self.tray = None
        self.pending_frame = None
        self.worker = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(40)
        if start_worker:
            self.worker = threading.Thread(target=self._network, daemon=True)
            self.worker.start()

    def submit(self, message):
        try:
            self.commands.put_nowait(copy.deepcopy(message))
            return True
        except queue.Full:
            if self.window:
                self.window.notice.setText(ERRORS['busy'])
            return False

    def _network(self):
        client = ServiceClient(self.owner_sid)
        pid, generation, sequence = None, 0, -1
        status_due, failures = 0, 0
        try:
            while not self.stop.is_set():
                command = None
                try:
                    try:
                        command = self.commands.get_nowait()
                    except queue.Empty:
                        pass
                    if command is not None or time.monotonic() >= status_due:
                        if command is not None and command['command'] == 'migrate':
                            # Authenticate first; never send legacy secrets to an unverified endpoint.
                            state = client.request({'command': 'status'})
                            from .ui import data_directory
                            from .config import load_config
                            legacy = load_profiles(data_directory() / 'cameras.dat')
                            if not legacy or not state.get('ok'):
                                raise ValueError
                            update = migrate_profiles(legacy, load_config(data_directory() / 'config.json')['resolution'])
                            update['revision'] = state['revision']
                            response = client.request({'command': 'configure', 'update': update})
                            if response.get('ok'):
                                set_startup(False)  # Only after durable service acknowledgement.
                            legacy = update = None
                        else:
                            response = client.request(command or {'command': 'status'})
                        response['_command'] = command['command'] if command else 'status'
                        if response.get('ok'):
                            new_pid = response['data']['pid']
                            if new_pid != pid:
                                pid, sequence = new_pid, -1
                            generation = response['generation']
                            if command and command['command'] in ('select', 'configure', 'disconnect', 'migrate'):
                                sequence = -1
                        self.results.put(response, timeout=.2)
                        command = None
                        status_due = time.monotonic() + .8
                    if pid is not None:
                        frame = client.read_frame(sequence, generation)
                        if frame is not None:
                            generation, sequence = frame[:2]
                            with self.lock:
                                self.incoming = (pid, frame)
                    failures = 0
                    self.stop.wait(.04)
                except (OSError, ValueError, KeyError, queue.Full):
                    client.close()
                    client = ServiceClient(self.owner_sid)
                    pid, generation, sequence, status_due = None, 0, -1, 0
                    try:
                        self.results.put_nowait({'ok': False, 'code': 'migration_failed' if command and command['command'] == 'migrate' else 'offline',
                                                 '_command': command['command'] if command else 'status'})
                    except queue.Full:
                        pass
                    self.stop.wait((1, 2, 4, 8, 15, 30)[min(failures, 5)])
                    failures += 1
        finally:
            client.close()

    def accept_frame(self, frame):
        generation, _, stamp, rgb = frame
        if generation < self.generation:
            return
        height, width = rgb.shape[:2]
        if (width, height) != (self.shared.width, self.shared.height):
            if self.output.active:
                self.output.stop()
                self.pending_frame = frame
                return
            self.shared = SharedFrame(mp.get_context('spawn'), width, height)
        self.generation = generation
        self.shared.publish(rgb, timestamp=stamp)
        self.image = rgb if time.monotonic() - stamp <= 5 else status_frame(width, height, 'MAT KET NOI')
        self.image_at = stamp

    def tick(self):
        for _ in range(16):
            try:
                response = self.results.get_nowait()
            except queue.Empty:
                break
            if response.get('ok'):
                if response['data']['pid'] != self.pid or response['generation'] != self.generation:
                    self.pid = response['data']['pid']
                    self.generation = response['generation']
                    self.image = status_frame(self.shared.width, self.shared.height, 'DANG CHUYEN NGUON')
                    self.shared.publish(self.image)
                self.status = response
                if self.want_output is None:
                    self.want_output = response['data']['config']['auto_connect']
                if self.window:
                    self.window.receive(response)
            else:
                message = ERRORS.get(response.get('code'), 'Không thực hiện được yêu cầu. Tải lại cấu hình và thử lại.')
                LOG.warning('Service request failed code=%s recovery=%s', response.get('code'), message)
                if response.get('code') == 'offline':
                    self.status = None
                if self.window:
                    self.window.notice.setText(message)
                    self.window.record_problem('Dịch vụ camera', response.get('code', 'unknown'), message)
                    self.window.busy = False
                    self.window.use_stage = None
        with self.lock:
            incoming, self.incoming = self.incoming, None
        if incoming and incoming[0] == self.pid:
            self.accept_frame(incoming[1])
        if time.monotonic() - self.image_at > 5:
            self.image = status_frame(self.shared.width, self.shared.height, 'MAT KET NOI')
        was_active = self.output.active
        for state, message in self.output.poll():
            LOG.info('Webcam state=%s message=%s', state, message)
            self.output_message = message
            self.output_running = state == 'running'
            if state == 'running':
                self.attempt = 0
        if was_active and not self.output.active:
            self.output_running = False
            self.retry_at = time.monotonic() + (1, 2, 4, 8, 15, 30)[min(self.attempt, 5)]
            self.attempt += 1
        if self.pending_frame is not None and not self.output.active:
            frame, self.pending_frame = self.pending_frame, None
            self.accept_frame(frame)
        if not self.stop.is_set() and self.want_output and self.status and not self.output.active and time.monotonic() >= self.retry_at:
            try:
                # A killed publisher can abandon the old multiprocessing semaphore.
                self.shared = SharedFrame(mp.get_context('spawn'), self.shared.width, self.shared.height)
                self.shared.publish(self.image, timestamp=self.image_at)
                self.output.start(run_output, (self.shared, 25))
            except RuntimeError:
                self.output_message = 'Không khởi động được webcam; đang thử lại.'
                self.retry_at = time.monotonic() + 30
        if self.window and self.window.isVisible():
            self.window.refresh()
        if self.stop.is_set() and not self.output.active and (self.worker is None or not self.worker.is_alive()):
            self.timer.stop()
            if self.tray:
                self.tray.hide()
                QApplication.instance().quit()

    def start_output(self):
        self.want_output, self.retry_at, self.attempt = True, 0, 0

    def stop_output(self):
        self.want_output = False
        self.output_running = False
        self.output.stop()
        self.output_message = 'Webcam đã dừng. Service vẫn nhận camera.'

    def open_window(self):
        if self.window is None:
            self.window = ServiceWindow(self)
            if self.status:
                self.window.receive(self.status)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def close(self):
        self.stop.set()
        self.stop_output()
        if self.window:
            self.window.hide()
            self.window.password.clear()
            self.window.config = None


class CameraDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        color = index.data(Qt.ItemDataRole.ForegroundRole)
        if color is not None:
            option.palette.setColor(QPalette.ColorRole.HighlightedText, color.color())


class NotificationLabel(QLabel):
    def setText(self, text):
        super().setText(text)
        failed = any(word in text.lower() for word in ('không ', 'lỗi', 'thất bại', 'chưa nhận', 'không hợp lệ', 'hủy', 'từ chối', 'hết số lần'))
        success = any(word in text.lower() for word in ('thành công', 'đã kết nối', 'sẵn sàng sử dụng', 'đã lưu', 'đã thêm'))
        color, background = ('#b42332', '#fff0f2') if failed else ('#166534', '#eafaf0') if success else ('#475569', '#f4f7fb')
        self.setStyleSheet(f'color: {color}; background: {background}; border: 1px solid {color}; border-radius: 7px; padding: 12px;')


class ServiceWindow(QMainWindow):
    def __init__(self, session):
        super().__init__()
        self.session, self.config, self.index, self.busy = session, None, 0, False
        self.use_stage = None
        self.use_camera_id = None
        self.problem_events = {}
        self.check_only = False
        self.setWindowTitle('IP Camera Bridge')
        self.resize(1240, 830)
        self.setMinimumSize(960, 640)
        self.setStyleSheet("""
            QWidget { color: #15233b; font-family: 'Segoe UI'; }
            QMainWindow, QWidget#shell { background: #f4f7fc; }
            QDialog, QMenu, QComboBox QAbstractItemView { background: white; color: #15233b; }
            QWidget#card { background: white; border: 1px solid #e0e7f1; border-radius: 12px; }
            QLabel { background: transparent; }
            QLabel#heading { font-size: 22px; font-weight: 700; }
            QLabel#cardTitle { font-size: 17px; font-weight: 700; }
            QLabel#muted { color: #65758e; }
            QLabel#badge { border-radius: 10px; padding: 6px 12px; font-size: 12px; }
            QLineEdit, QComboBox { background: #fcfdff; border: 1px solid #ccd7e8;
                border-radius: 6px; min-height: 34px; padding: 0 10px; color: #15233b; }
            QLineEdit:disabled, QComboBox:disabled { background: #f0f3f8; color: #65758e; }
            QPushButton { background: white; border: 1px solid #ccd7e8; border-radius: 6px;
                min-height: 34px; padding: 0 12px; color: #253753; }
            QPushButton:hover { background: #eef4ff; border-color: #77a6ee; }
            QPushButton:pressed, QPushButton:checked { background: #dfeaff; }
            QPushButton:disabled { color: #697991; background: #edf1f7; border-color: #e0e7f1; }
            QPushButton#primaryButton { background: #0b5ee8; color: white; border-color: #0b5ee8; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #094cc0; }
            QPushButton#primaryButton:disabled { background: #dce5f3; color: #65758e; border-color: #dce5f3; }
            QPushButton#stopButton { color: #b42332; background: #fff1f3; border-color: #f5bdc5; }
            QPushButton#retryButton { color: white; background: #e83348; border-color: #e83348; font-weight: 600; }
            QPushButton#retryButton:disabled { background: #f4d7dc; color: #8a5860; }
            QPushButton#disclosure { background: #f8faff; text-align: left; color: #354763; }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QPushButton:focus { border: 2px solid #0b5ee8; }
            QCheckBox { spacing: 7px; min-height: 26px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QListWidget { background: white; border: none; outline: 0; }
            QListWidget::item { border: 1px solid transparent; border-radius: 7px; padding: 12px 8px; margin-bottom: 6px; }
            QListWidget::item:selected { background: #edf5ff; border-color: #84b6fa; }
            QListWidget::item:hover { background: #f2f6fd; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #f4f7fc; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #c4d0e1; min-height: 24px; border-radius: 5px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        shell = QWidget()
        shell.setObjectName('shell')
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(16)

        header = QHBoxLayout()
        logo = QLabel()
        logo.setFixedSize(42, 42)
        logo.setStyleSheet('background: white; border-radius: 9px;')
        logo.setPixmap(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')).pixmap(48, 48))
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
        header.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(2)
        heading = QLabel('IP Camera Bridge')
        heading.setObjectName('heading')
        brand.addWidget(heading)
        subtitle = QLabel('Kết nối camera IP với OBS Virtual Camera')
        subtitle.setObjectName('muted')
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        self.service_badge = QLabel('Đang kiểm tra dịch vụ')
        self.service_badge.setObjectName('badge')
        header.addWidget(self.service_badge)
        settings = QPushButton()
        settings.setIcon(ui_icon('settings'))
        settings.setFixedWidth(40)
        settings.setAccessibleName('Cài đặt và tác vụ khác')
        settings.setToolTip('Cài đặt và tác vụ khác')
        menu = QMenu(settings)
        menu.addAction('Thêm camera thử (Test Pattern)').triggered.connect(self.add_test_camera)
        menu.addAction('Ngắt camera đang chọn').triggered.connect(lambda: self.selected_command('disconnect'))
        menu.addAction('Kết nối / ngắt tất cả').triggered.connect(self.toggle_all)
        menu.addSeparator()
        menu.addAction('Xóa camera đang chọn').triggered.connect(self.remove_camera)
        menu.addAction('Xóa mật khẩu đã lưu').triggered.connect(self.clear_password)
        menu.addAction('Tải lại cấu hình').triggered.connect(self.reload)
        menu.addAction('Nhập camera từ bản desktop cũ').triggered.connect(self.migrate)
        settings.setMenu(menu)
        header.addWidget(settings)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        sidebar = QWidget()
        sidebar.setObjectName('card')
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 16, 16, 16)
        sidebar_layout.setSpacing(10)
        side_heading = QHBoxLayout()
        self.camera_count = QLabel('Camera (0)')
        self.camera_count.setObjectName('cardTitle')
        side_heading.addWidget(self.camera_count)
        side_heading.addStretch()
        reload_button = QPushButton()
        reload_button.setIcon(ui_icon('refresh'))
        reload_button.setFixedWidth(36)
        reload_button.setAccessibleName('Tải lại danh sách camera')
        reload_button.setToolTip('Tải lại danh sách camera')
        reload_button.clicked.connect(self.reload)
        side_heading.addWidget(reload_button)
        sidebar_layout.addLayout(side_heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Tìm camera…')
        self.search.setAccessibleName('Tìm camera theo tên hoặc địa chỉ IP')
        self.search.textChanged.connect(self.filter_cameras)
        sidebar_layout.addWidget(self.search)
        self.cameras = QListWidget()
        self.cameras.setItemDelegate(CameraDelegate(self.cameras))
        self.cameras.setWordWrap(True)
        self.cameras.setAccessibleName('Danh sách camera. Chọn camera rồi bấm Kết nối camera để phát.')
        self.cameras.currentRowChanged.connect(self.select_editor)
        sidebar_layout.addWidget(self.cameras, 1)
        hint = QLabel('Chọn camera rồi bấm Kết nối camera để phát.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        sidebar_layout.addWidget(hint)
        add_button = QPushButton('Thêm camera')
        add_button.setIcon(ui_icon('plus', '#ffffff'))
        add_button.setObjectName('primaryButton')
        add_button.clicked.connect(self.add_camera)
        sidebar_layout.addWidget(add_button)
        self.scan_button = QPushButton('Quét camera LAN')
        self.scan_button.setIcon(ui_icon('network', '#0b5ee8'))
        self.scan_button.clicked.connect(self.scan_lan)
        sidebar_layout.addWidget(self.scan_button)
        columns.addWidget(sidebar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        columns.addWidget(scroll, 1)
        root.addLayout(columns, 1)

        camera_box = QWidget()
        camera_box.setObjectName('card')
        camera_layout = QVBoxLayout(camera_box)
        camera_layout.setContentsMargins(20, 16, 20, 16)
        camera_layout.setSpacing(8)
        camera_heading = QHBoxLayout()
        self.camera_title = QLabel('Chọn camera')
        self.camera_title.setTextFormat(Qt.TextFormat.PlainText)
        self.camera_title.setObjectName('cardTitle')
        camera_heading.addWidget(self.camera_title)
        self.camera_badge = QLabel('Chưa kết nối')
        self.camera_badge.setObjectName('badge')
        camera_heading.addWidget(self.camera_badge)
        camera_heading.addStretch()
        camera_layout.addLayout(camera_heading)
        description = QLabel('Cấu hình thông tin kết nối và phát luồng từ camera')
        description.setObjectName('muted')
        camera_layout.addWidget(description)
        self.editor = QWidget()
        form = QGridLayout(self.editor)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(4)
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 1)
        self.name, self.address, self.username, self.password = (QLineEdit() for _ in range(4))
        self.address.setPlaceholderText('IP hoặc URL RTSP đầy đủ')
        self.address.editingFinished.connect(self.expand_address)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Nhập mật khẩu')
        self.password.setToolTip('Nhập mật khẩu nguyên bản, kể cả ký tự @. Để trống để giữ mật khẩu đã lưu.')
        self.show_password = self.password.addAction(ui_icon('eye'), QLineEdit.ActionPosition.TrailingPosition)
        self.show_password.setText('Hiện mật khẩu')
        self.show_password.setCheckable(True)
        self.show_password.toggled.connect(self.toggle_password)
        self.resolution = QComboBox()
        for value in ('720p', '1080p'):
            self.resolution.addItem(value + ' · 25 fps', value)
        self.rtsp_path = QLineEdit(DEFAULT_PATH)
        self.rtsp_path.setToolTip('Mẫu i-PRO / Panasonic. Đổi đường dẫn theo model nếu dùng camera khác. URL đầy đủ được giữ nguyên.')
        for row, fields in enumerate(((('Tên camera', self.name), ('URL RTSP / IP', self.address)),
                (('Tên đăng nhập', self.username), ('Mật khẩu', self.password)),
                (('Chất lượng đầu ra', self.resolution), ('Đường dẫn stream', self.rtsp_path)))):
            for column, (label, field) in enumerate(fields):
                caption = QLabel(label)
                caption.setBuddy(field)
                form.addWidget(caption, row * 2, column)
                form.addWidget(field, row * 2 + 1, column)
        camera_layout.addWidget(self.editor)
        self.keep_saved_address = False
        self.saved_password_ids = set()
        self.address.textEdited.connect(lambda: setattr(self, 'keep_saved_address', False))

        self.auto = QCheckBox('Tự khởi động cùng Windows')
        self.auto.setToolTip('Service tự kết nối camera khi Windows khởi động; webcam tự bật sau đăng nhập.')
        self.remember = QCheckBox('Lưu cấu hình')
        self.remember.setChecked(True)
        self.remember.setToolTip('Lưu camera và mật khẩu được Windows mã hóa khi kết nối. Bỏ chọn: chỉ dùng tạm đến khi service khởi động lại.')
        self.remember.toggled.connect(self.remember_changed)
        self.auto.toggled.connect(lambda checked: self.remember.setChecked(True) if checked else None)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.auto)
        actions.addWidget(self.remember)
        actions.addStretch()
        self.save_button = QPushButton('Kết nối camera')
        self.save_button.setObjectName('primaryButton')
        self.save_button.setIcon(ui_icon('plug', '#ffffff'))
        self.save_button.clicked.connect(self.use_camera)
        actions.addWidget(self.save_button)
        self.test_button = QPushButton('Kiểm tra RTSP')
        self.test_button.setIcon(ui_icon('play'))
        self.test_button.clicked.connect(lambda: self.use_camera(preview_only=True))
        actions.addWidget(self.test_button)
        camera_layout.addLayout(actions)

        self.error_card = QWidget()
        self.error_card.setObjectName('errorCard')
        self.error_card.setStyleSheet('QWidget#errorCard { background: #fff4f5; border: 1px solid #ffc6cd; border-radius: 9px; }')
        error_layout = QHBoxLayout(self.error_card)
        error_layout.setContentsMargins(16, 14, 16, 14)
        error_text = QVBoxLayout()
        self.error_title = QLabel('Không thể kết nối RTSP')
        self.error_title.setStyleSheet('color: #b91c32; font-weight: 700; font-size: 15px;')
        self.error_summary = QLabel()
        self.error_summary.setWordWrap(True)
        self.error_summary.setTextFormat(Qt.TextFormat.PlainText)
        error_text.addWidget(self.error_title)
        error_text.addWidget(self.error_summary)
        error_layout.addLayout(error_text, 1)
        recovery_text = QVBoxLayout()
        recovery_heading = QLabel('Gợi ý khắc phục')
        recovery_heading.setStyleSheet('font-weight: 600; color: #873044;')
        recovery_text.addWidget(recovery_heading)
        self.error_message = QLabel()
        self.error_message.setTextFormat(Qt.TextFormat.PlainText)
        self.error_message.setWordWrap(True)
        self.error_message.setStyleSheet('color: #873044;')
        recovery_text.addWidget(self.error_message)
        error_layout.addLayout(recovery_text, 1)
        error_actions = QVBoxLayout()
        self.retry_button = QPushButton('Thử lại ngay')
        self.retry_button.setObjectName('retryButton')
        self.retry_button.setIcon(ui_icon('refresh', '#ffffff'))
        self.retry_button.clicked.connect(self.use_camera)
        error_actions.addWidget(self.retry_button)
        details = QPushButton('Xem chi tiết')
        details.clicked.connect(self.show_error_details)
        error_actions.addWidget(details)
        error_layout.addLayout(error_actions)
        self.error_card.hide()
        camera_layout.addWidget(self.error_card)
        layout.addWidget(camera_box)

        output_card = QWidget()
        output_card.setObjectName('card')
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(20, 18, 20, 18)
        output_layout.setSpacing(12)
        output_heading = QHBoxLayout()
        output_title = QLabel('Trạng thái đầu ra')
        output_title.setObjectName('cardTitle')
        output_heading.addWidget(output_title)
        self.output_badge = QLabel('Đang chờ camera')
        self.output_badge.setObjectName('badge')
        output_heading.addWidget(self.output_badge)
        output_heading.addStretch()
        output_layout.addLayout(output_heading)
        status_row = QHBoxLayout()
        status_text = QVBoxLayout()
        status_text.setSpacing(8)
        device_name = QLabel('OBS Virtual Camera')
        device_name.setObjectName('muted')
        status_text.addWidget(device_name)
        self.fps_label = QLabel('0 fps')
        self.fps_label.setStyleSheet('font-size: 24px; font-weight: 700;')
        stats = QHBoxLayout()
        stats.setSpacing(18)
        fps_column = QVBoxLayout()
        fps_column.setSpacing(2)
        fps_heading = QLabel('FPS nguồn hiện tại')
        fps_heading.setObjectName('muted')
        fps_column.addWidget(fps_heading)
        fps_column.addWidget(self.fps_label)
        stats.addLayout(fps_column)
        state_column = QVBoxLayout()
        state_column.setSpacing(4)
        self.source_status, self.output_status = QLabel(), QLabel('Webcam ảo chưa bật.')
        for label in (self.source_status, self.output_status):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            state_column.addWidget(label)
        stats.addLayout(state_column, 1)
        status_text.addLayout(stats)
        self.output_button = QPushButton('Dừng phát')
        self.output_button.setObjectName('stopButton')
        self.output_button.clicked.connect(self.toggle_output)
        status_text.addWidget(self.output_button, 0, Qt.AlignmentFlag.AlignLeft)
        status_text.addStretch()
        status_row.addLayout(status_text, 1)
        preview_column = QVBoxLayout()
        self.preview_placeholder = QLabel('Chưa có hình ảnh\nKết nối camera để xem trước')
        self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_placeholder.setMinimumSize(240, 90)
        self.preview_placeholder.setStyleSheet('background: #303946; color: #e1e8f2; border-radius: 7px; padding: 8px;')
        preview_column.addWidget(self.preview_placeholder)
        self.preview_button = QPushButton('Mở xem trước')
        self.preview_button.setIcon(ui_icon('eye', '#0b5ee8'))
        self.preview_button.setCheckable(True)
        preview_column.addWidget(self.preview_button)
        status_row.addLayout(preview_column)
        output_layout.addLayout(status_row)
        self.notice = NotificationLabel('Sẵn sàng. Chọn camera để kết nối.')
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setAccessibleName('Thông báo hiện tại')
        output_layout.addWidget(self.notice)
        layout.addWidget(output_card)
        preview_box = QGroupBox('Xem trước camera đang xuất')
        preview_box.hide()
        self.preview_button.toggled.connect(preview_box.setVisible)
        self.preview_button.toggled.connect(lambda checked: self.preview_button.setText('Đóng xem trước' if checked else 'Mở xem trước'))
        preview_layout = QVBoxLayout(preview_box)
        self.preview, self.state = QLabel(), QLabel()
        self.preview.setMinimumSize(320, 180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet('background: #0b0f16; border-radius: 6px;')
        self.state.setWordWrap(True)
        self.state.setObjectName('muted')
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addWidget(self.state)
        layout.addWidget(preview_box)
        layout.addStretch()
        self.discovery_results = queue.Queue(1)
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.finish_scan)
        self.discovery_cancel = threading.Event()
        self.discovery_progress = ''

    def toggle_password(self, checked):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.show_password.setText('Ẩn mật khẩu' if checked else 'Hiện mật khẩu')

    def filter_cameras(self):
        query = self.search.text().strip().casefold()
        for index in range(self.cameras.count()):
            item = self.cameras.item(index)
            profile = self.config['cameras'][index] if self.config else {}
            item.setHidden(query not in (profile.get('name', '') + ' ' + (profile.get('address') or '')).casefold())

    def show_error_details(self):
        QMessageBox.information(self, self.error_title.text(), self.error_detail)

    def remember_changed(self, checked):
        if not checked:
            self.auto.setChecked(False)
            self.notice.setText('Chỉ dùng tạm đến khi service khởi động lại. Cấu hình đã lưu trước đó vẫn được giữ.')

    def expand_address(self):
        if self.keep_saved_address:
            return
        try:
            previous = self.address.text().strip()
            expanded = expand_rtsp_address(previous, self.rtsp_path.text().strip())
            self.address.setText(expanded)
            if expanded != previous:
                self.notice.setText('Đã tạo URL theo mẫu đường dẫn. Kiểm tra mẫu phù hợp với model camera.')
        except ValueError:
            self.notice.setText('Nhập IP hợp lệ (có thể kèm cổng) hoặc URL RTSP đầy đủ; kiểm tra mẫu đường dẫn.')

    def receive(self, response):
        command = response.get('_command')
        if self.config is None or command in ('configure', 'migrate', 'reload'):
            self.saved_password_ids = {c['id'] for c in response['data']['config']['cameras'] if c.get('has_password')}
            self.config = copy.deepcopy(response['data']['config'])
            for camera in self.config['cameras']:
                camera['password'] = None
                camera.pop('has_password', None)
                camera.pop('address_hint', None)
            if self.config['revision'] == 0 and len(self.config['cameras']) == 1:
                first = self.config['cameras'][0]
                if first['kind'] == 'test' and first['name'] == 'Camera 1':
                    first['kind'] = 'rtsp'
            self.index = min(self.index, len(self.config['cameras']) - 1)
            self.populate()
            self.notice.setText('Sẵn sàng. Chọn camera để kiểm tra hoặc kết nối.')
        elif command == 'select':
            # Selection persists independently; preserve unsaved form edits.
            self.config['revision'] = response['revision']
            self.config['selected_id'] = response['data']['config']['selected_id']
        if command not in (None, 'status') or (command == 'status' and self.use_stage is None):
            self.busy = False
        if self.use_stage == 'disconnecting' and command == 'disconnect':
            self.use_stage = 'stopping'
        if self.use_stage == 'stopping' and all(c['state'] == 'stopped' for c in response['data']['cameras'] if c['id'] in self.stop_ids):
            self.use_stage = 'saving'
            self.save()
        elif self.use_stage == 'saving' and command == 'configure':
            self.use_stage = 'connecting'
            self.send('connect', camera_id=self.use_camera_id)
            self.notice.setText('Đã lưu. Đang kết nối camera…' if self.remember.isChecked() else 'Đã áp dụng tạm. Đang kết nối camera…')
        elif self.use_stage == 'connecting' and command == 'connect':
            self.use_stage = 'waiting'
        if self.use_stage == 'waiting':
            camera = next((c for c in response['data']['cameras'] if c['id'] == self.use_camera_id), None)
            if camera and camera['state'] == 'connected':
                if not self.check_only:
                    self.session.start_output()
                self.use_stage = None
                self.notice.setText('Kiểm tra RTSP thành công. Bấm Kết nối camera để phát.' if self.check_only else 'Đã kết nối. Đang khởi động webcam ảo…')
            elif camera:
                self.notice.setText(user_message(camera['message']))
                if camera['state'] in ('error', 'failed', 'retrying', 'lost'):
                    self.use_stage = None

    def capture(self):
        if not self.config:
            return
        self.expand_address()
        camera = self.config['cameras'][self.index]
        camera.update(name=self.name.text().strip(), username=self.username.text(),
                      address=None if self.keep_saved_address else self.address.text().strip(),
                      password=self.password.text() or camera['password'])
        if camera['address'] and camera['address'].lower().startswith(('rtsp://', 'rtsps://')):
            camera['kind'] = 'rtsp'
        self.config['resolution'], self.config['auto_connect'] = self.resolution.currentData(), self.auto.isChecked()

    def populate(self, rebuild=True):
        if rebuild:
            self.cameras.blockSignals(True)
            self.cameras.clear()
            for camera in self.config['cameras']:
                item = QListWidgetItem(camera['name'])
                item.setData(Qt.ItemDataRole.UserRole, camera['id'])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setIcon(ui_icon('camera'))
                self.cameras.addItem(item)
            self.cameras.setCurrentRow(self.index)
            self.cameras.blockSignals(False)
            self.camera_count.setText(f'Camera ({len(self.config["cameras"])})')
            self.filter_cameras()
        camera = self.config['cameras'][self.index]
        self.name.setText(camera['name'])
        self.address.setText(camera['address'] or '')
        self.keep_saved_address = camera['address'] is None
        self.address.setPlaceholderText('Địa chỉ đã lưu được che; nhập địa chỉ mới để thay' if camera['address'] is None else 'IP hoặc URL RTSP đầy đủ')
        self.username.setText(camera['username'])
        self.password.setText(camera['password'] or '')
        saved = camera['id'] in self.saved_password_ids
        self.password.setPlaceholderText('••••••••' if saved else 'Nhập mật khẩu')
        self.password.setAccessibleDescription('Mật khẩu đã được lưu' if saved else 'Chưa có mật khẩu đã lưu')
        self.show_password.setChecked(False)
        self.resolution.setCurrentIndex(self.resolution.findData(self.config['resolution']))
        self.auto.setChecked(self.config['auto_connect'])
        self.refresh()

    def record_problem(self, key, state, message):
        if self.problem_events.get(key) == (state, message):
            return
        self.problem_events[key] = (state, message)
        self.notice.setText(message)

    def select_editor(self, index):
        if self.config and index >= 0:
            self.capture()
            self.index = index
            self.populate(rebuild=False)

    def add_camera(self):
        if self.busy or self.use_stage or not self.config or len(self.config['cameras']) >= MAX_CAMERAS:
            return
        self.capture()
        if self.config['revision'] == 0 and len(self.config['cameras']) == 1 and self.config['cameras'][0]['kind'] == 'rtsp' and not self.config['cameras'][0]['address']:
            self.address.setFocus()
            return
        self.config['cameras'].append({'id': str(uuid4()), 'name': f'Camera {len(self.config["cameras"]) + 1}',
            'kind': 'rtsp', 'address': '', 'username': '', 'password': ''})
        self.index = len(self.config['cameras']) - 1
        self.populate()

    def add_test_camera(self):
        if self.busy or self.use_stage or not self.config or len(self.config['cameras']) >= MAX_CAMERAS:
            return
        if not (self.config['revision'] == 0 and len(self.config['cameras']) == 1 and not self.address.text()):
            self.add_camera()
        self.config['cameras'][self.index].update(kind='test', name='Camera thử (Test Pattern)')
        self.populate()

    def clear_password(self):
        if self.config and not self.busy and not self.use_stage:
            self.password.clear()
            self.config['cameras'][self.index]['password'] = ''
            self.saved_password_ids.discard(self.config['cameras'][self.index]['id'])
            self.password.setPlaceholderText('Nhập mật khẩu')
            self.password.setAccessibleDescription('Chưa có mật khẩu đã lưu')
            self.notice.setText('Mật khẩu sẽ được xóa khi lưu hoặc bấm Kết nối camera.')

    def use_camera(self, checked=False, *, preview_only=False):
        if self.config and not self.busy:
            self.capture()
            for index, camera in enumerate(self.config['cameras']):
                if camera['kind'] == 'rtsp' and camera['address'] is not None and (camera['address'] or index == self.index):
                    try:
                        validate_rtsp_url(camera['address'])
                    except ValueError:
                        self.cameras.setCurrentRow(index)
                        self.notice.setText(f'{camera["name"]}: URL RTSP không hợp lệ. Nhập IP hoặc URL RTSP trước khi kết nối.')
                        self.address.setFocus()
                        return
            self.use_camera_id = self.config['cameras'][self.index]['id']
            self.check_only = preview_only
            if not preview_only:
                self.config['selected_id'] = self.use_camera_id
            original = self.session.status['data']['config'] if self.session.status else None
            previous = {c['id']: c for c in original['cameras']} if original else {}
            self.stop_ids = set()
            for camera in self.config['cameras']:
                old = previous.get(camera['id'])
                if old and (self.config['resolution'] != original['resolution'] or
                        any(camera[k] is not None and camera[k] != old.get(k) for k in ('kind', 'address', 'username', 'password'))):
                    self.stop_ids.add(camera['id'])
            # Disconnect all only for a shared resolution change; otherwise stop the edited source.
            if self.stop_ids:
                self.use_stage = 'disconnecting'
                self.send('disconnect', camera_id=next(iter(self.stop_ids)) if len(self.stop_ids) == 1 else None)
            else:
                self.use_stage = 'saving'
                self.save()
            if not self.busy:
                self.use_stage = None

    def scan_lan(self):
        if self.discovery_timer.isActive():
            self.discovery_cancel.set()
            self.scan_button.setEnabled(False)
            self.notice.setText('Đang dừng quét…')
            return
        if self.busy or self.use_stage or not self.config or self.discovery_timer.isActive():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Quét camera LAN')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Chỉ quét các địa chỉ từ IP bắt đầu đến IP kết thúc (bao gồm cả hai).'))
        first, last = QLineEdit(), QLineEdit()
        first.setPlaceholderText('192.168.100.1')
        last.setPlaceholderText('192.168.100.254')
        form = QGridLayout()
        for row, (label, field) in enumerate((('IP bắt đầu', first), ('IP kết thúc', last))):
            field.setAccessibleName(label)
            caption = QLabel(label)
            caption.setBuddy(field)
            form.addWidget(caption, row, 0)
            form.addWidget(field, row, 1)
        layout.addLayout(form)
        hint = QLabel()
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Bắt đầu quét')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Hủy')
        def accept():
            try:
                hosts = scan_range(first.text(), last.text())
            except ValueError:
                # Validation errors contain fixed messages, never input/credentials.
                hint.setText('Dải IP không hợp lệ. Nhập hai địa chỉ IPv4 từ nhỏ đến lớn, tối đa 1024 địa chỉ; ví dụ 192.168.100.1 đến 192.168.100.254.')
                first.setFocus()
                return
            self.scan_bounds = (str(hosts[0]), str(hosts[-1]))
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start, end = self.scan_bounds
        self.discovery_cancel.clear()
        self.discovery_progress = f'Đang quét {start} → {end}…'
        self.scan_button.setText('Dừng quét LAN')
        self.scan_button.setObjectName('stopButton')
        self.scan_button.style().unpolish(self.scan_button)
        self.scan_button.style().polish(self.scan_button)
        def worker():
            try:
                def progress(done, total):
                    self.discovery_progress = f'Đang quét {start} → {end}: {done}/{total} địa chỉ…'
                result = scan_rtsp(start, self.discovery_cancel, progress, end=end)
            except (OSError, ValueError):
                LOG.warning('LAN range scan failed; check network and firewall')
                result = None
            self.discovery_results.put(result)
        threading.Thread(target=worker, daemon=True).start()
        self.discovery_timer.start(100)

    def finish_scan(self):
        try:
            devices = self.discovery_results.get_nowait()
        except queue.Empty:
            self.notice.setText(self.discovery_progress)
            return
        self.discovery_timer.stop()
        self.scan_button.setEnabled(True)
        self.scan_button.setText('Quét camera LAN')
        self.scan_button.setObjectName('')
        self.scan_button.style().unpolish(self.scan_button)
        self.scan_button.style().polish(self.scan_button)
        if self.discovery_cancel.is_set():
            self.notice.setText('Đã dừng quét LAN.')
            return
        if devices is None:
            message = 'Không quét được LAN. Kiểm tra kết nối mạng và quyền Windows Firewall.'
            self.notice.setText(message)
            self.record_problem('Quét LAN', 'error', message)
            return
        if not self.config:
            self.notice.setText('Cấu hình đang được tải lại. Hãy quét lại khi danh sách camera đã sẵn sàng.')
            return
        if not devices:
            message = 'Không tìm thấy camera trong dải đã nhập. Kiểm tra dải IP camera, cổng RTSP 554, mạng/VLAN và firewall. Camera dùng cổng khác cần thêm IP:cổng thủ công.'
            self.notice.setText(message)
            self.record_problem('Quét LAN', 'empty', message)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Kết quả quét camera')
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Tick camera cần thêm. URL sẽ được tạo theo mẫu bên dưới; nhập tài khoản sau khi thêm.'))
        template = QLineEdit(self.rtsp_path.text())
        layout.addWidget(QLabel('Mẫu đường dẫn (mặc định i-PRO / Panasonic)'))
        layout.addWidget(template)
        choices = QListWidget()
        for device in devices:
            item = QListWidgetItem(f'{device.name} — {device.host}')
            item.setData(Qt.ItemDataRole.UserRole, device)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            choices.addItem(item)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Thêm camera đã tick')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Hủy')
        def checked_items():
            return [choices.item(index) for index in range(choices.count())
                    if choices.item(index).checkState() == Qt.CheckState.Checked]
        def accept_devices():
            if not checked_items():
                return
            try:
                for item in checked_items():
                    expand_rtsp_address(item.data(Qt.ItemDataRole.UserRole).host, template.text())
            except ValueError:
                template.setFocus()
                return
            dialog.accept()
        buttons.accepted.connect(accept_devices)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.notice.setText('Đã hủy thêm camera từ LAN.')
            return
        self.capture()
        added = 0
        empty_first = self.config['revision'] == 0 and len(self.config['cameras']) == 1 and self.config['cameras'][0]['kind'] == 'rtsp' and not self.config['cameras'][0]['address']
        addresses = {camera.get('address') for camera in self.config['cameras']}
        for item in checked_items():
            if not empty_first and len(self.config['cameras']) >= MAX_CAMERAS:
                break
            device = item.data(Qt.ItemDataRole.UserRole)
            address = expand_rtsp_address(device.host, template.text())
            if address in addresses:
                continue
            if empty_first:
                self.config['cameras'][0].update(name=device.name, address=address)
                addresses.add(address)
                added += 1
                empty_first = False
                continue
            self.config['cameras'].append({'id': str(uuid4()), 'name': device.name, 'kind': 'rtsp',
                'address': address, 'username': '', 'password': ''})
            addresses.add(address)
            added += 1
        if added:
            self.index = len(self.config['cameras']) - 1
            self.populate()
            self.notice.setText(f'Đã thêm {added} camera. Hoàn thiện đường dẫn RTSP và thông tin đăng nhập rồi bấm Kết nối camera.')
        else:
            self.notice.setText('Các camera đã chọn đã có trong danh sách hoặc danh sách đã đủ 16 camera.')

    def remove_camera(self):
        if not self.busy and not self.use_stage and self.config and len(self.config['cameras']) > 1:
            self.capture()
            removed = self.config['cameras'].pop(self.index)
            self.index = min(self.index, len(self.config['cameras']) - 1)
            if removed['id'] == self.config['selected_id']:
                self.config['selected_id'] = self.config['cameras'][self.index]['id']
            self.populate()

    def send(self, command, **kwargs):
        if not self.busy and self.session.submit({'command': command, **kwargs}):
            self.busy = True
            self.notice.setText('Đang thực hiện…')

    def save(self):
        if self.config:
            self.capture()
            self.send('configure', update=self.config, persist=self.remember.isChecked())

    def selected_command(self, command):
        if command == 'disconnect':
            self.use_stage = None
        if self.config:
            self.send(command, camera_id=self.config['cameras'][self.index]['id'])

    def toggle_all(self):
        cameras = self.session.status['data']['cameras'] if self.session.status else []
        self.send('disconnect' if any(camera.get('wanted') for camera in cameras) else 'connect', camera_id=None)

    def toggle_output(self):
        if self.use_stage:
            self.use_stage = None
            self.notice.setText('Đã hủy thao tác sử dụng camera.')
            return
        self.use_stage = None
        if self.session.want_output:
            self.session.stop_output()
        else:
            self.session.start_output()

    def reload(self):
        if self.busy or self.use_stage:
            return
        self.config = None
        self.send('status')

    def migrate(self):
        self.send('migrate')

    def refresh(self):
        if self.preview.isVisible():
            rgb = self.session.image
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio))
        data = self.session.status['data'] if self.session.status else None
        selected_id = data['config']['selected_id'] if data else None
        camera = next((c for c in data['cameras'] if c['id'] == selected_id), None) if data else None
        name = next((c['name'] for c in data['config']['cameras'] if c['id'] == selected_id), 'Chưa chọn') if data else 'Chưa chọn'
        connected = bool(camera and camera['state'] == 'connected')
        running = bool(self.session.output_running and connected and self.session.want_output)
        def badge(label, text, tone):
            colors = {'success': ('#166534', '#eafaf0'), 'error': ('#b42332', '#ffecef'),
                      'waiting': ('#475569', '#edf1f7')}
            foreground, background = colors[tone]
            label.setText(text)
            label.setStyleSheet(f'color: {foreground}; background: {background};')
        badge(self.service_badge, 'Dịch vụ đang chạy' if data else 'Chưa kết nối dịch vụ', 'success' if data else 'error')
        states = {c['id']: c for c in data['cameras']} if data else {}
        profile = self.config['cameras'][self.index] if self.config else None
        edited_source = states.get(profile['id']) if profile else None
        self.camera_title.setText(profile['name'] if profile else 'Chọn camera')
        has_error = bool(edited_source and edited_source['state'] in PROBLEM_STATES)
        badge(self.camera_badge, 'Lỗi kết nối' if has_error else ('Đã kết nối' if edited_source and edited_source['state'] == 'connected' else 'Chưa kết nối'),
              'error' if has_error else ('success' if edited_source and edited_source['state'] == 'connected' else 'waiting'))
        message = ERRORS['offline'] if not data else user_message(edited_source['message']) if has_error else ''
        title = 'Chưa kết nối dịch vụ camera' if not data else 'Không thể kết nối RTSP'
        if not message and self.session.want_output and not self.session.output_running and any(word in self.session.output_message.lower() for word in ('không', 'thiếu', 'khóa', 'khác')):
            title, message = 'Không thể bật webcam ảo', self.session.output_message
        self.error_title.setText(title)
        self.error_detail = message
        summary, _, recovery = message.partition('. ')
        self.error_summary.setText(summary)
        self.error_message.setText(recovery or message)
        self.error_card.setVisible(bool(message))
        self.retry_button.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        badge(self.output_badge, 'Đang phát' if running else ('Đang kết nối' if self.session.want_output else 'Đang chờ camera'), 'success' if running else 'waiting')
        self.fps_label.setText(f'{camera["fps"]:g} fps' if connected else '0 fps')
        self.preview_placeholder.setText('Camera đầu ra: ' + name + '\nBấm Mở xem trước để kiểm tra hình ảnh' if connected else 'Chưa có hình ảnh\nKết nối camera để xem trước')
        self.source_status.setText(f'Camera đầu ra: {name}\nKết nối: {user_message(camera["message"])} · {camera["fps"]} fps' if camera else ERRORS['offline'])
        self.source_status.setStyleSheet('color: #176b3a;' if connected else 'color: #8a5a00;')
        if running:
            output_message = 'Đang phát video trong Meet / Zoom.'
        elif self.session.output_running and self.session.want_output:
            output_message = 'Webcam đã mở nhưng chưa có hình camera. Kiểm tra kết nối nguồn và dịch vụ camera.'
        else:
            output_message = self.session.output_message
        self.output_status.setText(output_message)
        if running and self.notice.text() == 'Đã kết nối. Đang khởi động webcam ảo…':
            self.notice.setText('Sẵn sàng sử dụng trong Meet / Zoom.')
        self.output_status.setStyleSheet('color: #176b3a; font-weight: 600;' if running else 'color: #5d6b80;')
        self.output_button.setVisible(bool(self.session.want_output or self.use_stage))
        self.output_button.setText('Hủy kết nối' if self.use_stage else 'Dừng phát')
        self.save_button.setText('Đang lưu…' if self.busy else ('Đang kết nối…' if self.use_stage else 'Kết nối camera'))
        self.save_button.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        self.test_button.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        self.remember.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        self.auto.setEnabled(not self.busy and not self.use_stage)
        self.editor.setEnabled(not self.busy and not self.use_stage)
        self.cameras.setEnabled(not self.busy and not self.use_stage)
        self.cameras.blockSignals(True)
        for index in range(self.cameras.count() if self.config else 0):
            profile = self.config['cameras'][index]
            source = states.get(profile['id'])
            selected = profile['id'] == selected_id
            pending = bool(self.use_stage and not self.check_only and profile['id'] == self.use_camera_id)
            if selected and running and not self.use_stage:
                suffix = 'Đang phát'
            elif pending:
                suffix = 'Đang chuẩn bị phát'
            elif source and source['state'] in PROBLEM_STATES:
                suffix = 'Lỗi kết nối · cần kiểm tra'
            elif source and source['state'] == 'connected':
                suffix = 'Đã kết nối · chưa phát'
            elif source and source.get('wanted'):
                suffix = 'Đang kết nối'
            else:
                suffix = 'Chưa kết nối'
            item = self.cameras.item(index)
            try:
                host = urlsplit(profile.get('address') or '').hostname or ('Test Pattern' if profile['kind'] == 'test' else 'Chưa nhập IP')
            except ValueError:
                host = 'Địa chỉ đã lưu'
            item.setText(profile['name'] + '\n' + host + ' · ' + suffix)
            tone = '#166534' if selected and running else '#b42332' if source and source['state'] in PROBLEM_STATES else '#475569'
            item.setIcon(ui_icon('camera', tone))
            item.setForeground(QColor(tone))
            item.setToolTip(user_message(source['message']) if source else 'Chọn camera rồi bấm Kết nối camera để phát.')
            if source and source['state'] in PROBLEM_STATES:
                self.record_problem(profile['id'], source['state'], f'{profile["name"]}: {user_message(source["message"])}')
            else:
                self.problem_events.pop(profile['id'], None)
        self.cameras.blockSignals(False)
        if self.session.want_output and not self.session.output_running and any(word in self.session.output_message.lower() for word in ('không', 'thiếu', 'khóa', 'khác')):
            self.record_problem('Webcam', 'error', 'Webcam ảo: ' + self.session.output_message)
        else:
            self.problem_events.pop('Webcam', None)
        if data:
            self.problem_events.pop('Dịch vụ camera', None)
        self.state.setText(f'Hình từ camera đầu ra: {name}')

    def closeEvent(self, event):
        event.ignore()
        # Never persist an unfinished form or stop the service when hiding the window.
        self.use_stage = None
        self.discovery_cancel.set()
        self.config = None
        self.password.clear()
        self.hide()


def run_service_session(hidden=False, stop_only=False):
    import win32api
    import win32event
    from .windows_service import current_identity, owner_policy, contain_children, session_events
    policy = owner_policy()
    sid, session_id, _ = current_identity()
    if not policy or policy['owner_sid'] != sid or session_id == 0:
        return 1
    app = QApplication.instance() or QApplication([])
    from .config import setup_logging
    from .ui import data_directory
    setup_logging(data_directory() / 'session.log')
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    app.setQuitOnLastWindowClosed(False)
    name = f'IPCameraBridge-{sid}-{session_id}'
    mutex = win32event.CreateMutex(None, False, 'Local\\' + name)
    events = []
    try:
        if win32api.GetLastError() == 183:
            socket = QLocalSocket()
            socket.connectToServer(name)
            if not socket.waitForConnected(1000):
                return 1
            socket.write(b'stop' if stop_only else (b'ping' if hidden else b'show'))
            socket.waitForBytesWritten(1000)
            return 0
        if stop_only:
            return 0
        contain_children()
        events = session_events(sid, create=True)
        for event in events:
            win32event.ResetEvent(event)
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(name)  # This process holds the singleton mutex.
        if not server.listen(name):
            return 1
        session = ServiceSession(sid)
        def check_stop():
            if win32event.WaitForSingleObject(events[0], 0) == win32event.WAIT_OBJECT_0:
                session.close()
        session.timer.timeout.connect(check_stop)
        def connected():
            socket = server.nextPendingConnection()
            def read():
                data = bytes(socket.read(16))
                if data == b'show':
                    session.open_window()
                elif data == b'stop':
                    session.close()
                socket.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
        server.newConnection.connect(connected)
        tray = QSystemTrayIcon(app.windowIcon())
        menu = QMenu()
        for label, callback in (('Mở quản lý', session.open_window), ('Bật webcam', session.start_output),
                                ('Dừng webcam', session.stop_output), ('Thoát bộ xuất (service vẫn chạy)', session.close)):
            menu.addAction(label).triggered.connect(callback)
        tray.setContextMenu(menu)
        tray.setToolTip('IP Camera Bridge — chạy nền')
        tray.activated.connect(lambda reason: session.open_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        tray.show()
        session.tray = tray
        app.aboutToQuit.connect(session.close)
        if not hidden:
            session.open_window()
        result = app.exec()
        win32event.SetEvent(events[1])
        return result
    finally:
        for event in events:
            event.Close()
        mutex.Close()
