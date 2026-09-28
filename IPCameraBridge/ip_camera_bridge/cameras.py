"""Concurrent decoders feeding one switchable virtual-camera output."""
from .controller import BridgeController, ManagedProcess, LOG
from .frames import SharedFrame
from .output import run_output
from .windows_settings import MAX_CAMERAS


class CameraGroup:
    def __init__(self, width=1280, height=720):
        self.cameras = [BridgeController(width, height)]
        self.selected = 0
        self.shared = SharedFrame(self.cameras[0].ctx, width, height)
        self.output = ManagedProcess(self.cameras[0].ctx)
        self.output_state = 'stopped'
        self.output_message = 'Webcam ảo chưa bật.'
        self.closing = False
        self.auto_output = False

    @property
    def current(self):
        return self.cameras[self.selected]

    @property
    def closed(self):
        return self.closing and not self.output.active and all(camera.closed for camera in self.cameras)

    @property
    def active(self):
        return self.output.active or any(camera.source.active for camera in self.cameras)

    def add(self):
        if len(self.cameras) >= MAX_CAMERAS:
            raise RuntimeError(f'Tối đa {MAX_CAMERAS} camera trong một phiên.')
        self.cameras.append(BridgeController(self.shared.width, self.shared.height))

    def remove(self, index):
        if len(self.cameras) == 1 or self.cameras[index].source.active:
            raise RuntimeError('Hãy ngắt camera trước khi xóa; cần giữ ít nhất một camera.')
        self.cameras[index].close()
        del self.cameras[index]
        self.selected = min(self.selected - (index < self.selected), len(self.cameras) - 1)
        self.publish_selected()

    def select(self, index):
        if not 0 <= index < len(self.cameras):
            raise ValueError('Camera không hợp lệ.')
        self.selected = index
        self.publish_selected()

    def set_resolution(self, width, height):
        if self.active:
            raise RuntimeError('Hãy ngắt tất cả camera và dừng webcam trước khi đổi độ phân giải.')
        for camera in self.cameras:
            camera.set_resolution(width, height)
        self.shared = SharedFrame(self.current.ctx, width, height)

    def publish_selected(self):
        # Never retain another camera's frame after a switch, even if target is offline.
        self.shared.publish(self.current.preview())

    def start_output(self):
        if self.closing or self.output.active:
            raise RuntimeError('Webcam đang hoạt động hoặc đang dừng.')
        if self.current.source_state != 'connected':
            raise RuntimeError('Hãy chọn camera đã kết nối trước.')
        # A terminated publisher might have abandoned the old shared-frame lock.
        self.shared = SharedFrame(self.current.ctx, self.shared.width, self.shared.height)
        self.publish_selected()
        self.output.start(run_output, (self.shared, 25))
        self.output_state, self.output_message = 'starting', 'Đang mở OBS Virtual Camera…'

    def stop_output(self):
        self.auto_output = False
        self.output.stop()
        self.output_state = 'stopping' if self.output.active else 'stopped'
        self.output_message = 'Đang dừng webcam…' if self.output.active else 'Webcam ảo đã dừng.'

    def tick(self):
        for camera in self.cameras:
            camera.tick()
        self.publish_selected()
        was_output = self.output.active
        for state, message in self.output.poll():
            self.output_state, self.output_message = state, message
            LOG.info('output state=%s', state)
        if was_output and not self.output.active and self.output_state != 'error':
            self.output_state = 'stopped'
            self.output_message = 'Webcam ảo đã dừng.'
        if self.auto_output and not self.closing and self.current.source_state == 'connected':
            self.auto_output = False
            try:
                self.start_output()
            except RuntimeError:
                self.output_state, self.output_message = 'error', 'Không tự bật được webcam. Hãy thử bật lại.'

    def disconnect_all(self):
        self.stop_output()
        for camera in self.cameras:
            camera.disconnect()

    def close(self):
        self.closing = True
        self.stop_output()
        for camera in self.cameras:
            camera.close()
