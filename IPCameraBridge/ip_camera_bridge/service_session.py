"""Owner-session publisher and tray UI; the service owns camera capture."""
import copy
import multiprocessing as mp
import queue
import threading
import time
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QPushButton, QScrollArea, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget)

from .controller import ManagedProcess
from .config import validate_rtsp_url
from .frames import SharedFrame, status_frame
from .output import run_output
from .onvif_discovery import discover_onvif, local_interfaces, scan_network, scan_rtsp
from .rtsp_address import DEFAULT_PATH, expand_rtsp_address
from .service_config import migrate_profiles
from .service_ipc import ServiceClient
from .windows_settings import MAX_CAMERAS, load_profiles, set_startup

ERRORS = {'source_busy': 'Ngắt nguồn trước khi sửa thông tin kết nối hoặc độ phân giải.',
          'stale_revision': 'Cấu hình đã thay đổi. Bấm Tải lại rồi chỉnh sửa.',
          'save_failed': 'Không lưu được cấu hình; dữ liệu trước đó được giữ nguyên.',
          'invalid_request': 'Kiểm tra địa chỉ, tên và thông tin camera.',
          'offline': 'Chưa kết nối được Windows Service. Đang thử lại.',
          'migration_failed': 'Không chuyển được cấu hình cũ. Dữ liệu cũ vẫn được giữ.',
          'busy': 'Service đang bận. Hãy thử lại.', 'timeout': 'Chưa nhận được xác nhận; bấm Tải lại để kiểm tra.'}


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
            elif self.window:
                self.window.notice.setText(ERRORS.get(response.get('code'), 'Không thực hiện được yêu cầu.'))
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
            QPushButton#stopButton { color: #a1261d; background: #fff5f4; border-color: #e4a39d;
                                     font-weight: 600; }
            QLabel#heading { font-size: 24px; font-weight: 700; color: #10213b; }
            QLabel#muted { color: #5d6b80; }
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

        camera_box = QGroupBox('1. Chọn và cấu hình camera')
        camera_layout = QVBoxLayout(camera_box)
        row = QHBoxLayout()
        self.cameras = QComboBox()
        self.cameras.setAccessibleName('Chọn camera; trạng thái phát được ghi cạnh tên')
        self.cameras.currentIndexChanged.connect(self.select_editor)
        row.addWidget(self.cameras, 1)
        for label, callback in (('+ Thêm camera', self.add_camera), ('Quét camera LAN', self.scan_lan)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
            if callback == self.scan_lan:
                self.scan_button = button
        camera_layout.addLayout(row)
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
        more = QPushButton('Tác vụ khác ▾')
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
        self.output_status = QLabel('Webcam ảo chưa bật.')
        self.output_status.setWordWrap(True)
        status_layout.addWidget(self.source_status)
        status_layout.addWidget(self.output_status)
        controls = QHBoxLayout()
        self.output_button = QPushButton('Dừng phát')
        self.output_button.clicked.connect(self.toggle_output)
        controls.addWidget(self.output_button)
        self.preview_button = QPushButton('Xem hình camera')
        self.preview_button.setCheckable(True)
        controls.addWidget(self.preview_button)
        controls.addStretch()
        status_layout.addLayout(controls)
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
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addWidget(self.state)
        layout.addWidget(preview_box, 1)
        layout.addWidget(self.notice)
        guide = QLabel('Meet / Zoom: chọn camera “OBS Virtual Camera”. Nếu báo camera đang được dùng, hãy đóng Zoom, Teams, Windows Camera và OBS; sau đó tải lại trang Meet và bấm Thử lại.')
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
                self.notice.setText(camera['message'])
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

    def populate(self):
        self.cameras.blockSignals(True)
        self.cameras.clear()
        self.cameras.addItems([camera['name'] + (' · Đầu ra đã chọn' if camera['id'] == self.config['selected_id'] else '')
                               for camera in self.config['cameras']])
        self.cameras.setCurrentIndex(self.index)
        self.cameras.blockSignals(False)
        camera = self.config['cameras'][self.index]
        self.name.setText(camera['name'])
        self.address.setText(camera['address'] or '')
        self.keep_address.setVisible(camera['address'] is None)
        self.keep_address.setChecked(camera['address'] is None)
        self.username.setText(camera['username'])
        self.password.setText(camera['password'] or '')
        self.resolution.setCurrentIndex(self.resolution.findData(self.config['resolution']))
        self.auto.setChecked(self.config['auto_connect'])

    def select_editor(self, index):
        if self.config and index >= 0:
            self.capture()
            self.index = index
            self.populate()

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
        layout.addWidget(QLabel('Chọn card mạng hoặc quét dải IP camera.'))
        interfaces = local_interfaces()
        adapter = QComboBox()
        adapter.addItem('Tất cả card mạng đang hoạt động', None)
        for name, address, network in interfaces:
            adapter.addItem(f'{name} — {address}', (address, network))
        layout.addWidget(adapter)
        tcp = QCheckBox('Quét thêm RTSP theo dải IP (kể cả khác VLAN)')
        layout.addWidget(tcp)
        subnet = QLineEdit()
        subnet.setPlaceholderText('Ví dụ: 192.168.100.0/24 — tối đa 1024 địa chỉ')
        subnet.setEnabled(False)
        tcp.toggled.connect(subnet.setEnabled)
        adapter.currentIndexChanged.connect(lambda: subnet.setText(adapter.currentData()[1]) if adapter.currentData() else None)
        layout.addWidget(subnet)
        hint = QLabel('Khác VLAN cần được router/firewall cho phép truy cập. RTSP quét cổng 554; tìm thấy thiết bị chưa có nghĩa là đã xác nhận đường dẫn stream.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Bắt đầu quét')
        def accept():
            if tcp.isChecked():
                try:
                    scan_network(subnet.text())
                except ValueError:
                    hint.setText('Dải IPv4 không hợp lệ hoặc quá lớn. Ví dụ: 192.168.100.0/24 (tối đa 1024 địa chỉ).')
                    return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [adapter.currentData()[0]] if adapter.currentData() else [entry[1] for entry in interfaces]
        target = subnet.text().strip() if tcp.isChecked() else None
        self.discovery_cancel.clear()
        self.discovery_progress = 'Đang quét ONVIF trên card mạng đã chọn…'
        self.scan_button.setText('Dừng quét LAN')
        def worker():
            try:
                try:
                    result = discover_onvif(interfaces=selected, cancel=self.discovery_cancel)
                except OSError:
                    if not target:
                        raise
                    result = []
                if target and not self.discovery_cancel.is_set():
                    def progress(done, total):
                        self.discovery_progress = f'Đang quét RTSP: {done}/{total} địa chỉ…'
                    devices = {device.host: device for device in result}
                    for device in scan_rtsp(target, self.discovery_cancel, progress):
                        devices.setdefault(device.host, device)
                    result = list(devices.values())
            except (OSError, ValueError):
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
        if self.discovery_cancel.is_set():
            self.notice.setText('Đã dừng quét LAN.')
            return
        if devices is None:
            self.notice.setText('Không quét được LAN. Kiểm tra mạng và quyền Windows Firewall.')
            return
        if not devices:
            self.notice.setText('Không tìm thấy camera. Thử chọn đúng card mạng hoặc quét dải IP bằng RTSP; kiểm tra VLAN/firewall. Bạn vẫn có thể nhập IP thủ công.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Kết quả quét camera')
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Chọn camera. URL sẽ được tạo theo mẫu bên dưới; nhập tài khoản sau khi thêm.'))
        template = QLineEdit(self.rtsp_path.text())
        layout.addWidget(QLabel('Mẫu đường dẫn (mặc định i-PRO / Panasonic)'))
        layout.addWidget(template)
        choices = QListWidget()
        choices.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for device in devices:
            item = QListWidgetItem(f'{device.name} — {device.host}')
            item.setData(Qt.ItemDataRole.UserRole, device)
            choices.addItem(item)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept_devices():
            if not choices.selectedItems():
                return
            try:
                expand_rtsp_address(choices.selectedItems()[0].data(Qt.ItemDataRole.UserRole).host, template.text())
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
        for item in choices.selectedItems():
            if len(self.config['cameras']) >= MAX_CAMERAS:
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
        self.source_status.setText(f'Camera đầu ra: {name}\nKết nối: {camera["message"]} · {camera["fps"]} fps' if camera else 'Chưa kết nối được dịch vụ camera.')
        self.source_status.setStyleSheet('color: #176b3a;' if connected else 'color: #8a5a00;')
        self.output_status.setText('Đang phát qua OBS Virtual Camera. Chọn thiết bị này trong Meet / Zoom.' if running else self.session.output_message)
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
        for index in range(self.cameras.count() if self.config else 0):
            profile = self.config['cameras'][index]
            suffix = (' · Đang phát' if running else ' · Đầu ra đã chọn') if profile['id'] == selected_id else ''
            self.cameras.setItemText(index, profile['name'] + suffix)
        self.state.setText(f'Hình từ camera đầu ra: {name}')

    def closeEvent(self, event):
        event.ignore()
        # Never persist an unfinished form or stop the service when hiding the window.
        self.use_stage = None
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
