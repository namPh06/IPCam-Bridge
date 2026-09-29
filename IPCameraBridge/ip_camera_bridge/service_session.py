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
from .frames import SharedFrame, status_frame
from .output import run_output
from .onvif_discovery import discover_onvif
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
        with self.lock:
            incoming, self.incoming = self.incoming, None
        if incoming and incoming[0] == self.pid:
            self.accept_frame(incoming[1])
        if time.monotonic() - self.image_at > 5:
            self.image = status_frame(self.shared.width, self.shared.height, 'MAT KET NOI')
        was_active = self.output.active
        for state, message in self.output.poll():
            self.output_message = message
            if state == 'running':
                self.attempt = 0
        if was_active and not self.output.active:
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
        self.setWindowTitle('IP Camera Bridge — Windows Service')
        self.resize(1100, 950)
        self.setMinimumSize(900, 720)
        self.setStyleSheet('''
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
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)
        heading = QLabel('IP Camera Bridge')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        subtitle = QLabel('Chọn camera  →  Kết nối nguồn  →  Phát qua OBS Virtual Camera')
        subtitle.setObjectName('muted')
        layout.addWidget(subtitle)

        camera_box = QGroupBox('1. Chọn và cấu hình camera')
        camera_layout = QVBoxLayout(camera_box)
        row = QHBoxLayout()
        self.cameras = QComboBox()
        self.cameras.setAccessibleName('Camera đang chỉnh sửa; camera đang xuất có dấu chọn')
        self.cameras.currentIndexChanged.connect(self.select_editor)
        row.addWidget(self.cameras, 1)
        for label, callback in (('+ Thêm camera', self.add_camera), ('Quét camera LAN', self.scan_lan),
                                ('Xóa camera', self.remove_camera)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
            if callback == self.scan_lan:
                self.scan_button = button
        camera_layout.addLayout(row)
        form = QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.name, self.address, self.username, self.password = (QLineEdit() for _ in range(4))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Nhập mật khẩu nguyên bản, không đổi @ thành %40')
        self.replace_password = QCheckBox('Thay mật khẩu (để trống để xóa)')
        self.password.setEnabled(False)
        self.replace_password.toggled.connect(self.password.setEnabled)
        self.keep_address = QCheckBox('Giữ địa chỉ đã lưu đang được che')
        self.kind, self.resolution = QComboBox(), QComboBox()
        for label, value in (('Test Pattern', 'test'), ('RTSP', 'rtsp'), ('Video File', 'file')):
            self.kind.addItem(label, value)
        for value in ('720p', '1080p'):
            self.resolution.addItem(value, value)
        form.addWidget(QLabel('Tên camera'), 0, 0)
        form.addWidget(self.name, 0, 1)
        form.addWidget(QLabel('Nguồn'), 0, 2)
        form.addWidget(self.kind, 0, 3)
        form.addWidget(QLabel('URL RTSP / file'), 1, 0)
        form.addWidget(self.address, 1, 1, 1, 3)
        form.addWidget(self.keep_address, 2, 1, 1, 3)
        form.addWidget(QLabel('Tên đăng nhập'), 3, 0)
        form.addWidget(self.username, 3, 1)
        form.addWidget(QLabel('Mật khẩu'), 3, 2)
        form.addWidget(self.password, 3, 3)
        form.addWidget(QLabel('Đầu ra 25 fps'), 4, 0)
        form.addWidget(self.resolution, 4, 1)
        form.addWidget(self.replace_password, 4, 3)
        camera_layout.addLayout(form)
        self.auto = QCheckBox('Tự kết nối camera khi Windows khởi động; tự bật webcam sau đăng nhập')
        camera_layout.addWidget(self.auto)
        row = QHBoxLayout()
        self.save_button = QPushButton('Lưu cấu hình')
        self.save_button.setObjectName('primaryButton')
        self.save_button.clicked.connect(self.save)
        self.output_select_button = QPushButton('Chọn camera này để xuất')
        self.output_select_button.clicked.connect(self.select_output)
        more = QPushButton('Tác vụ khác ▾')
        menu = QMenu(more)
        menu.addAction('Tải lại cấu hình').triggered.connect(self.reload)
        menu.addAction('Nhập camera từ bản desktop cũ').triggered.connect(self.migrate)
        more.setMenu(menu)
        row.addWidget(self.save_button, 1)
        row.addWidget(self.output_select_button, 1)
        row.addWidget(more)
        camera_layout.addLayout(row)
        layout.addWidget(camera_box)

        action_row = QHBoxLayout()
        source_box = QGroupBox('2. Kết nối nguồn')
        source_layout = QVBoxLayout(source_box)
        self.source_status = QLabel('Chưa có trạng thái camera.')
        self.source_status.setWordWrap(True)
        source_layout.addWidget(self.source_status)
        source_buttons = QHBoxLayout()
        self.connect_button = QPushButton('Kết nối camera đang chọn')
        self.connect_button.setObjectName('primaryButton')
        self.connect_button.clicked.connect(self.toggle_selected)
        self.all_button = QPushButton('Kết nối tất cả')
        self.all_button.clicked.connect(self.toggle_all)
        source_buttons.addWidget(self.connect_button, 1)
        source_buttons.addWidget(self.all_button)
        source_layout.addLayout(source_buttons)
        action_row.addWidget(source_box, 1)

        output_box = QGroupBox('3. Phát vào ứng dụng họp')
        output_layout = QVBoxLayout(output_box)
        self.output_status = QLabel('Webcam ảo chưa bật.')
        self.output_status.setWordWrap(True)
        output_layout.addWidget(self.output_status)
        self.output_button = QPushButton('Bật webcam ảo')
        self.output_button.setObjectName('primaryButton')
        self.output_button.clicked.connect(self.toggle_output)
        output_layout.addWidget(self.output_button)
        action_row.addWidget(output_box, 1)
        layout.addLayout(action_row)

        preview_box = QGroupBox('Xem trước camera đang xuất')
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
        self.discovery_results = queue.Queue(1)
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.finish_scan)

    def receive(self, response):
        command = response.get('_command')
        if self.config is None or command in ('configure', 'migrate', 'reload'):
            self.config = copy.deepcopy(response['data']['config'])
            for camera in self.config['cameras']:
                camera['password'] = None
                camera.pop('has_password', None)
                camera.pop('address_hint', None)
            self.index = min(self.index, len(self.config['cameras']) - 1)
            self.populate()
            self.notice.setText('Đã tải cấu hình. Mật khẩu đã lưu không được trả về giao diện.')
        elif command == 'select':
            # Selection persists independently; preserve unsaved form edits.
            self.config['revision'] = response['revision']
            self.config['selected_id'] = response['data']['config']['selected_id']
        if command is not None:
            self.busy = False
            if command in ('configure', 'migrate'):
                self.session.want_output = self.auto.isChecked()

    def capture(self):
        if not self.config:
            return
        camera = self.config['cameras'][self.index]
        camera.update(name=self.name.text().strip(), kind=self.kind.currentData(), username=self.username.text(),
                      address=None if self.keep_address.isChecked() else self.address.text().strip(),
                      password=self.password.text() if self.replace_password.isChecked() else camera['password'])
        self.config['resolution'], self.config['auto_connect'] = self.resolution.currentData(), self.auto.isChecked()

    def populate(self):
        self.cameras.blockSignals(True)
        self.cameras.clear()
        self.cameras.addItems([camera['name'] + ('  ✓ ĐANG XUẤT' if camera['id'] == self.config['selected_id'] else '')
                               for camera in self.config['cameras']])
        self.cameras.setCurrentIndex(self.index)
        self.cameras.blockSignals(False)
        camera = self.config['cameras'][self.index]
        self.name.setText(camera['name'])
        self.kind.setCurrentIndex(self.kind.findData(camera['kind']))
        self.address.setText(camera['address'] or '')
        self.keep_address.setVisible(camera['address'] is None)
        self.keep_address.setChecked(camera['address'] is None)
        self.username.setText(camera['username'])
        self.password.setText(camera['password'] or '')
        self.replace_password.setChecked(camera['password'] is not None)
        self.resolution.setCurrentIndex(self.resolution.findData(self.config['resolution']))
        self.auto.setChecked(self.config['auto_connect'])

    def select_editor(self, index):
        if self.config and index >= 0:
            self.capture()
            self.index = index
            self.populate()

    def add_camera(self):
        if not self.config or len(self.config['cameras']) >= MAX_CAMERAS:
            return
        self.capture()
        self.config['cameras'].append({'id': str(uuid4()), 'name': f'Camera {len(self.config["cameras"]) + 1}',
            'kind': 'test', 'address': '', 'username': '', 'password': ''})
        self.index = len(self.config['cameras']) - 1
        self.populate()

    def scan_lan(self):
        if not self.config or self.discovery_timer.isActive():
            return
        self.scan_button.setEnabled(False)
        self.notice.setText('Đang quét camera ONVIF trong LAN…')
        def worker():
            try:
                result = discover_onvif()
            except OSError:
                result = None
            self.discovery_results.put(result)
        threading.Thread(target=worker, daemon=True).start()
        self.discovery_timer.start(100)

    def finish_scan(self):
        try:
            devices = self.discovery_results.get_nowait()
        except queue.Empty:
            return
        self.discovery_timer.stop()
        self.scan_button.setEnabled(True)
        if devices is None:
            self.notice.setText('Không quét được LAN. Kiểm tra mạng và quyền Windows Firewall.')
            return
        if not devices:
            self.notice.setText('Không tìm thấy camera ONVIF. Bạn vẫn có thể thêm URL RTSP thủ công.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Camera ONVIF trong LAN')
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Chọn camera cần thêm. Sau đó nhập đường dẫn RTSP, tài khoản và mật khẩu.'))
        choices = QListWidget()
        choices.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for device in devices:
            item = QListWidgetItem(f'{device.name} — {device.host}')
            item.setData(Qt.ItemDataRole.UserRole, device)
            choices.addItem(item)
        choices.selectAll()
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.notice.setText('Đã hủy thêm camera từ LAN.')
            return
        self.capture()
        added = 0
        addresses = {camera.get('address') for camera in self.config['cameras']}
        for item in choices.selectedItems():
            if len(self.config['cameras']) >= MAX_CAMERAS:
                break
            device = item.data(Qt.ItemDataRole.UserRole)
            if device.rtsp_url in addresses:
                continue
            self.config['cameras'].append({'id': str(uuid4()), 'name': device.name, 'kind': 'rtsp',
                'address': device.rtsp_url, 'username': '', 'password': ''})
            addresses.add(device.rtsp_url)
            added += 1
        if added:
            self.index = len(self.config['cameras']) - 1
            self.populate()
            self.notice.setText(f'Đã thêm {added} camera. Hoàn thiện đường dẫn RTSP và thông tin đăng nhập rồi bấm Lưu cấu hình.')
        else:
            self.notice.setText('Các camera đã chọn đã có trong danh sách hoặc danh sách đã đủ 16 camera.')

    def remove_camera(self):
        if self.config and len(self.config['cameras']) > 1:
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
            self.send('configure', update=self.config)

    def selected_command(self, command):
        if self.config:
            self.send(command, camera_id=self.config['cameras'][self.index]['id'])

    def selected_status(self):
        if not self.config or not self.session.status:
            return None
        camera_id = self.config['cameras'][self.index]['id']
        return next((camera for camera in self.session.status['data']['cameras'] if camera['id'] == camera_id), None)

    def toggle_selected(self):
        camera = self.selected_status()
        self.selected_command('disconnect' if camera and camera.get('wanted') else 'connect')

    def toggle_all(self):
        cameras = self.session.status['data']['cameras'] if self.session.status else []
        self.send('disconnect' if any(camera.get('wanted') for camera in cameras) else 'connect', camera_id=None)

    def toggle_output(self):
        if self.session.want_output:
            self.session.stop_output()
        else:
            self.session.start_output()

    def select_output(self):
        if self.config:
            self.capture()
            self.config['selected_id'] = self.config['cameras'][self.index]['id']
            self.save()

    def reload(self):
        self.config = None
        self.send('status')

    def migrate(self):
        self.send('migrate')

    def refresh(self):
        rgb = self.session.image
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio))
        cameras = self.session.status['data']['cameras'] if self.session.status else []
        selected = self.selected_status()
        selected_id = self.config['selected_id'] if self.config else None
        output_name = next((camera['name'] for camera in self.config['cameras'] if camera['id'] == selected_id), 'Chưa chọn') if self.config else 'Chưa chọn'
        if selected:
            self.source_status.setText(f'{selected["message"]}  ·  {selected["fps"]} fps')
            source_color = '#176b3a' if selected['state'] == 'connected' else ('#8a5a00' if selected.get('wanted') else '#a1261d')
            self.source_status.setStyleSheet(f'color: {source_color}; font-weight: 600;')
            self.connect_button.setText('Ngắt camera đang chọn' if selected.get('wanted') else 'Kết nối camera đang chọn')
            self.connect_button.setObjectName('stopButton' if selected.get('wanted') else 'primaryButton')
        else:
            self.source_status.setText('Camera mới chưa được lưu. Hãy lưu cấu hình trước khi kết nối.')
            self.source_status.setStyleSheet('color: #8a5a00; font-weight: 600;')
        any_wanted = any(camera.get('wanted') for camera in cameras)
        self.all_button.setText('Ngắt tất cả' if any_wanted else 'Kết nối tất cả')
        self.output_status.setText(f'Camera xuất: {output_name}\n{self.session.output_message}')
        output_wanted = bool(self.session.want_output)
        output_color = '#176b3a' if self.session.output.active else ('#8a5a00' if output_wanted else '#5d6b80')
        self.output_status.setStyleSheet(f'color: {output_color}; font-weight: 600;')
        self.output_button.setText('Dừng webcam ảo' if output_wanted else 'Bật webcam ảo')
        self.output_button.setObjectName('stopButton' if output_wanted else 'primaryButton')
        current_id = self.config['cameras'][self.index]['id'] if self.config else None
        is_output = current_id == selected_id
        self.output_select_button.setText('✓ Camera đang xuất' if is_output else 'Chọn camera này để xuất')
        self.output_select_button.setEnabled(not is_output and not self.busy)
        self.connect_button.setEnabled(bool(selected) and not self.busy)
        self.all_button.setEnabled(bool(cameras) and not self.busy)
        self.output_button.setEnabled(bool(self.session.status))
        self.save_button.setEnabled(bool(self.config) and not self.busy)
        for button in (self.connect_button, self.output_button):
            button.style().unpolish(button)
            button.style().polish(button)
        lines = []
        for profile, camera in zip(self.config['cameras'] if self.config else [], cameras):
            marker = 'ĐANG XUẤT · ' if camera['id'] == selected_id else ''
            lines.append(f'{marker}{profile["name"]}: {camera["message"]} ({camera["fps"]} fps)')
        self.state.setText('\n'.join(lines))

    def closeEvent(self, event):
        event.ignore()
        # Never persist an unfinished form or stop the service when hiding the window.
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
