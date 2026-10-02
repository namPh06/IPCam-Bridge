"""Owner-session publisher and tray UI; the service owns camera capture."""
import copy
import logging
import multiprocessing as mp
import queue
import re
import threading
import time
from ipaddress import ip_network
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget)

from .controller import ManagedProcess
from .config import validate_rtsp_url
from .frames import SharedFrame, status_frame
from .output import run_output
from .onvif_discovery import local_interfaces, scan_range, scan_rtsp
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


class ServiceWindow(QMainWindow):
    def __init__(self, session):
        super().__init__()
        self.session, self.config, self.index, self.busy = session, None, 0, False
        self.use_stage = None
        self.use_camera_id = None
        self.problem_events = {}
        self.setWindowTitle('IP Camera Bridge — Windows Service')
        self.resize(1050, 740)
        self.setMinimumSize(800, 600)
        self.setStyleSheet('''
            QWidget { color: #172033; }
            QDialog, QMenu, QListWidget, QComboBox QAbstractItemView { background: #ffffff; color: #172033; }
            QMainWindow { background: #f3f6fb; color: #172033; }
            QGroupBox { background: white; border: 1px solid #d8e0ec; border-radius: 8px;
                        margin-top: 12px; padding: 14px 10px 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QComboBox { min-height: 34px; border: 1px solid #bcc8d8; border-radius: 5px;
                                  padding: 0 8px; background: white; }
            QPushButton { min-height: 34px; padding: 0 14px; border: 1px solid #aebbd0;
                          border-radius: 5px; background: #fff; }
            QPushButton:hover { background: #eef4ff; border-color: #6f9ee8; }
            QPushButton:pressed { background: #d9e8ff; }
            QPushButton:disabled { color: #8b96a8; background: #edf0f4; border-color: #d7dde6; }
            QPushButton#primaryButton { color: white; background: #0b57d0; border-color: #0b57d0;
                                        font-weight: 600; }
            QPushButton#primaryButton:hover { background: #0949b4; }
            QPushButton#primaryButton:disabled { color: #68758a; background: #e2e8f1; border-color: #d7dde6; }
            QPushButton#stopButton { color: #a1261d; background: #fff5f4; border-color: #e4a39d;
                                     font-weight: 600; }
            QLabel#heading { font-size: 24px; font-weight: 700; color: #10213b; }
            QLabel#muted { color: #5d6b80; }
            QListWidget, QPlainTextEdit { border: 1px solid #bcc8d8; border-radius: 5px; background: white; }
            QListWidget::item { padding: 10px 8px; border-bottom: 1px solid #edf0f4; }
            QListWidget::item:selected { background: #e8f0fe; color: #10213b; }
            QListWidget::item:hover { background: #f0f5fc; }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus, QPushButton:focus { border: 2px solid #0b57d0; }
            QCheckBox { spacing: 8px; min-height: 28px; }
        ''')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: 0; background: #f3f6fb; }')
        self.setCentralWidget(scroll)
        body = QWidget()
        body.setObjectName('pageBody')
        body.setStyleSheet('QWidget#pageBody { background: #f3f6fb; }')
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)
        heading = QLabel('IP Camera Bridge')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        subtitle = QLabel('Thêm camera → Kết nối và sử dụng → Chọn OBS Virtual Camera trong ứng dụng họp')
        subtitle.setObjectName('muted')
        layout.addWidget(subtitle)

        camera_box = QGroupBox('1. Danh sách camera')
        camera_layout = QVBoxLayout(camera_box)
        row = QHBoxLayout()
        self.cameras = QListWidget()
        self.cameras.setFixedHeight(100)
        self.cameras.setAccessibleName('Danh sách camera. Tick một camera để phát; chọn dòng để sửa thông tin.')
        self.cameras.currentRowChanged.connect(self.select_editor)
        self.cameras.itemChanged.connect(self.choose_output)
        row.addWidget(self.cameras, 1)
        for label, callback in (('+ Thêm camera', self.add_camera), ('Quét camera LAN', self.scan_lan)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
            if callback == self.scan_lan:
                self.scan_button = button
        camera_layout.addLayout(row)
        hint = QLabel('Tick camera để kết nối và phát. Chỉ một camera phát tại một thời điểm; chọn dòng để sửa thông tin.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        camera_layout.addWidget(hint)
        self.edit_button = QPushButton('Ẩn thông tin camera')
        self.edit_button.setCheckable(True)
        self.edit_button.setChecked(True)
        self.editor = QWidget()
        self.edit_button.toggled.connect(self.editor.setVisible)
        self.edit_button.toggled.connect(lambda checked: self.edit_button.setText('Ẩn thông tin camera' if checked else 'Sửa thông tin camera'))
        camera_layout.addWidget(self.edit_button)
        form = QGridLayout(self.editor)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.name, self.address, self.username, self.password = (QLineEdit() for _ in range(4))
        self.address.setPlaceholderText('192.168.100.77 hoặc URL RTSP đầy đủ')
        self.address.editingFinished.connect(self.expand_address)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Để trống để giữ mật khẩu đã lưu')
        self.password.setToolTip('Nhập mật khẩu nguyên bản, kể cả ký tự @. Để trống để giữ mật khẩu đã lưu.')
        self.keep_address = QCheckBox('Giữ địa chỉ đã lưu đang được che')
        self.resolution = QComboBox()
        for value in ('720p', '1080p'):
            self.resolution.addItem(value, value)
        form.addWidget(QLabel('Tên camera'), 0, 0)
        form.addWidget(self.name, 0, 1, 1, 3)
        form.addWidget(QLabel('IP / URL RTSP'), 1, 0)
        form.addWidget(self.address, 1, 1, 1, 3)
        form.addWidget(self.keep_address, 2, 1, 1, 3)
        form.addWidget(QLabel('Tên đăng nhập'), 3, 0)
        form.addWidget(self.username, 3, 1)
        form.addWidget(QLabel('Mật khẩu'), 3, 2)
        form.addWidget(self.password, 3, 3)
        form.addWidget(QLabel('Đầu ra 25 fps'), 4, 0)
        form.addWidget(self.resolution, 4, 1)
        self.rtsp_path = QLineEdit(DEFAULT_PATH)
        self.rtsp_path.setToolTip('Mẫu i-PRO / Panasonic. Đổi đường dẫn theo model nếu dùng camera khác. URL đầy đủ được giữ nguyên.')
        form.addWidget(QLabel('Mẫu đường dẫn'), 5, 0)
        form.addWidget(self.rtsp_path, 5, 1, 1, 3)
        camera_layout.addWidget(self.editor)
        self.auto = QCheckBox('Tự chạy khi đăng nhập Windows')
        options = QHBoxLayout()
        options.addWidget(self.auto, 1)
        self.remember = QCheckBox('Lưu cấu hình')
        self.remember.setChecked(True)
        self.remember.setToolTip('Lưu camera và mật khẩu được Windows mã hóa khi bấm Kết nối và sử dụng. Bỏ chọn: chỉ áp dụng đến khi service khởi động lại; cấu hình đã lưu trước đó vẫn giữ nguyên.')
        self.remember.toggled.connect(self.remember_changed)
        self.auto.toggled.connect(lambda checked: self.remember.setChecked(True) if checked else None)
        options.addWidget(self.remember)
        camera_layout.addLayout(options)
        row = QHBoxLayout()
        self.save_button = QPushButton('Kết nối và sử dụng')
        self.save_button.setObjectName('primaryButton')
        self.save_button.clicked.connect(self.use_camera)
        more = QPushButton('Tác vụ khác')
        menu = QMenu(more)
        menu.addAction('Thêm camera thử (Test Pattern)').triggered.connect(self.add_test_camera)
        menu.addAction('Ngắt camera đang chọn').triggered.connect(lambda: self.selected_command('disconnect'))
        menu.addAction('Kết nối / ngắt tất cả').triggered.connect(self.toggle_all)
        menu.addAction('Xóa camera').triggered.connect(self.remove_camera)
        menu.addAction('Xóa mật khẩu đã lưu').triggered.connect(self.clear_password)
        menu.addAction('Tải lại cấu hình').triggered.connect(self.reload)
        menu.addAction('Nhập camera từ bản desktop cũ').triggered.connect(self.migrate)
        more.setMenu(menu)
        row.addWidget(self.save_button, 1)
        row.addWidget(more)
        camera_layout.addLayout(row)
        layout.addWidget(camera_box)

        status_box = QGroupBox('Trạng thái sử dụng')
        status_layout = QVBoxLayout(status_box)
        self.source_status = QLabel('Chọn camera rồi bấm Kết nối và sử dụng.')
        self.source_status.setWordWrap(True)
        self.source_status.setTextFormat(Qt.TextFormat.PlainText)
        self.output_status = QLabel('Webcam ảo chưa bật.')
        self.output_status.setWordWrap(True)
        self.output_status.setTextFormat(Qt.TextFormat.PlainText)
        status_layout.addWidget(self.source_status)
        status_layout.addWidget(self.output_status)
        controls = QHBoxLayout()
        self.output_button = QPushButton('Dừng phát')
        self.output_button.setObjectName('stopButton')
        self.output_button.clicked.connect(self.toggle_output)
        controls.addWidget(self.output_button)
        self.preview_button = QPushButton('Xem hình camera')
        self.preview_button.setCheckable(True)
        controls.addWidget(self.preview_button)
        controls.addStretch()
        status_layout.addLayout(controls)
        self.problem_button = QPushButton('Lịch sử sự cố')
        self.problem_button.setCheckable(True)
        self.problem_button.setToolTip('Xem nguyên nhân và việc cần kiểm tra. Không hiển thị mã lỗi kỹ thuật.')
        controls.addWidget(self.problem_button)
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.problems.setMaximumBlockCount(120)
        self.problems.setFixedHeight(140)
        self.problems.setAccessibleName('Lịch sử sự cố và hướng dẫn kiểm tra')
        self.problems.hide()
        self.problem_button.toggled.connect(self.problems.setVisible)
        status_layout.addWidget(self.problems)
        layout.addWidget(status_box)

        preview_box = QGroupBox('Xem trước camera đang xuất')
        preview_box.hide()
        self.preview_button.toggled.connect(preview_box.setVisible)
        self.preview_button.toggled.connect(lambda checked: self.preview_button.setText('Ẩn hình camera' if checked else 'Xem hình camera'))
        preview_layout = QVBoxLayout(preview_box)
        self.preview, self.state, self.notice = QLabel(), QLabel(), QLabel()
        self.preview.setMinimumSize(320, 180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet('background: #0b0f16; border-radius: 6px;')
        self.state.setWordWrap(True)
        self.state.setObjectName('muted')
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addWidget(self.state)
        layout.addWidget(preview_box, 1)
        layout.addWidget(self.notice)
        guide = QLabel('Trong Meet / Zoom, chọn “OBS Virtual Camera”. OBS chỉ cần cài sẵn; không bật Virtual Camera trong OBS khi tool đang phát.')
        guide.setWordWrap(True)
        guide.setStyleSheet('padding: 9px; color: #604600; background: #fff7d6; border: 1px solid #ead074; border-radius: 6px;')
        layout.addWidget(guide)
        layout.addStretch()
        self.discovery_results = queue.Queue(1)
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.finish_scan)
        self.discovery_cancel = threading.Event()
        self.discovery_progress = ''

    def remember_changed(self, checked):
        if not checked:
            self.auto.setChecked(False)
            self.notice.setText('Chỉ dùng tạm đến khi service khởi động lại. Cấu hình đã lưu trước đó vẫn được giữ.')

    def expand_address(self):
        if self.keep_address.isChecked():
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
            self.notice.setText('Đã tải cấu hình. Mật khẩu đã lưu không được trả về giao diện.')
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
                self.session.start_output()
                self.use_stage = None
                self.edit_button.setChecked(False)
                self.notice.setText('Đã kết nối. Đang khởi động webcam ảo…')
            elif camera:
                self.notice.setText(user_message(camera['message']))
                if camera['state'] in ('error', 'failed', 'retrying', 'lost'):
                    self.use_stage = None
                    self.edit_button.setChecked(True)

    def capture(self):
        if not self.config:
            return
        self.expand_address()
        camera = self.config['cameras'][self.index]
        camera.update(name=self.name.text().strip(), username=self.username.text(),
                      address=None if self.keep_address.isChecked() else self.address.text().strip(),
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
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.cameras.addItem(item)
            self.cameras.setCurrentRow(self.index)
            self.cameras.blockSignals(False)
            self.cameras.setFixedHeight(min(170, max(90, len(self.config['cameras']) * 38 + 4)))
        camera = self.config['cameras'][self.index]
        self.name.setText(camera['name'])
        self.address.setText(camera['address'] or '')
        self.keep_address.setVisible(camera['address'] is None)
        self.keep_address.setChecked(camera['address'] is None)
        self.username.setText(camera['username'])
        self.password.setText(camera['password'] or '')
        self.resolution.setCurrentIndex(self.resolution.findData(self.config['resolution']))
        self.auto.setChecked(self.config['auto_connect'])
        self.refresh()

    def choose_output(self, item):
        if not self.config or self.busy or self.use_stage:
            return
        if item.checkState() == Qt.CheckState.Checked:
            index = self.cameras.row(item)
            if index != self.index:
                self.cameras.setCurrentRow(index)
            self.use_camera()
        elif self.session.status and item.data(Qt.ItemDataRole.UserRole) == self.session.status['data']['config']['selected_id']:
            self.session.stop_output()
        self.refresh()

    def record_problem(self, key, state, message):
        if self.problem_events.get(key) == (state, message):
            return
        self.problem_events[key] = (state, message)
        self.problems.appendPlainText(f'[{time.strftime("%H:%M:%S")}] {message}')
        self.problem_button.setText('Lịch sử sự cố · có thông báo')

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
            self.edit_button.setChecked(True)
            self.address.setFocus()
            return
        self.config['cameras'].append({'id': str(uuid4()), 'name': f'Camera {len(self.config["cameras"]) + 1}',
            'kind': 'rtsp', 'address': '', 'username': '', 'password': ''})
        self.index = len(self.config['cameras']) - 1
        self.populate()
        self.edit_button.setChecked(True)

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
            self.notice.setText('Mật khẩu sẽ được xóa khi lưu hoặc bấm Kết nối và sử dụng.')

    def use_camera(self):
        if self.config and not self.busy:
            self.capture()
            for camera in self.config['cameras']:
                if camera['kind'] == 'rtsp' and camera['address'] is not None:
                    try:
                        validate_rtsp_url(camera['address'])
                    except ValueError:
                        self.notice.setText('URL RTSP không hợp lệ. Nhập rtsp://địa-chỉ-camera:554/đường-dẫn-stream.')
                        self.edit_button.setChecked(True)
                        self.address.setFocus()
                        return
            self.use_camera_id = self.config['cameras'][self.index]['id']
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
        interfaces = local_interfaces()
        adapter = QComboBox()
        adapter.addItem('Nhập dải IP thủ công', None)
        for name, address, network in interfaces:
            adapter.addItem(f'{name} — {address}', (address, network))
        layout.addWidget(adapter)
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
        def suggest_range():
            if adapter.currentData():
                address, subnet = adapter.currentData()
                network = ip_network(subnet)
                if network.num_addresses > 1024:
                    network = ip_network(f'{address}/24', strict=False)
                hosts = list(network.hosts())
                first.setText(str(hosts[0]))
                last.setText(str(hosts[-1]))
        adapter.currentIndexChanged.connect(suggest_range)
        if interfaces:
            adapter.setCurrentIndex(1)
        hint = QLabel('Tối đa 1024 địa chỉ. Kiểm tra RTSP tại cổng 554, không gửi tài khoản/mật khẩu. Khác VLAN cần router/firewall cho phép truy cập. Không quét ONVIF multicast ngoài dải đã nhập.')
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
            self.edit_button.setChecked(True)
            self.notice.setText(f'Đã thêm {added} camera. Hoàn thiện đường dẫn RTSP và thông tin đăng nhập rồi bấm Kết nối và sử dụng.')
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
        self.source_status.setText(f'Camera đầu ra: {name}\nKết nối: {user_message(camera["message"])} · {camera["fps"]} fps' if camera else ERRORS['offline'])
        self.source_status.setStyleSheet('color: #176b3a;' if connected else 'color: #8a5a00;')
        if running:
            output_message = 'Đang phát qua OBS Virtual Camera. Chọn thiết bị này trong Meet / Zoom.'
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
        self.save_button.setText('Đang lưu…' if self.busy else ('Đang kết nối…' if self.use_stage else 'Kết nối và sử dụng'))
        self.save_button.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        self.remember.setEnabled(bool(self.config) and not self.busy and not self.use_stage)
        self.auto.setEnabled(not self.busy and not self.use_stage)
        self.editor.setEnabled(not self.busy and not self.use_stage)
        self.cameras.setEnabled(not self.busy and not self.use_stage)
        states = {c['id']: c for c in data['cameras']} if data else {}
        self.cameras.blockSignals(True)
        for index in range(self.cameras.count() if self.config else 0):
            profile = self.config['cameras'][index]
            source = states.get(profile['id'])
            selected = profile['id'] == selected_id
            pending = bool(self.use_stage and profile['id'] == self.use_camera_id)
            checked = pending or (selected and self.session.want_output and not self.use_stage)
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
            item.setText(profile['name'] + ' · ' + suffix)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setToolTip(user_message(source['message']) if source else 'Tick để kết nối và phát camera này.')
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
        tray = QSystemTrayIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
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
