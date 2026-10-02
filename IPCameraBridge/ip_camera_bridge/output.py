"""Windows OBS virtual-camera publisher, isolated from capture and the GUI."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from queue import Full
import time

from .frames import status_frame


class _OutputError(RuntimeError):
    """A fixed, safe message that can be shown directly to the user."""


@contextmanager
def _publisher_mutex(name='Local\\IPCameraBridgePublisher'):
    if os.name != 'nt':
        raise _OutputError('Webcam ảo OBS của ứng dụng này chỉ hỗ trợ Windows 10/11.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise _OutputError('Không tạo được khóa webcam ảo. Hãy đóng ứng dụng khác và thử lại.')
    already_exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    try:
        if already_exists:
            raise _OutputError('Một cửa sổ IP Camera Bridge khác đang phát webcam. Hãy dừng webcam ở cửa sổ đó trước.')
        yield
    finally:
        kernel.CloseHandle(handle)


def _backend_error(error):
    if isinstance(error, ImportError):
        return 'Bộ phát webcam bị thiếu thành phần. Hãy cài lại IP Camera Bridge và chọn cài OBS Studio trong bộ cài.'
    # Match known backend failures, but never forward third-party exception text.
    detail = str(error).lower()
    if 'device not found' in detail or 'did you install obs' in detail:
        return 'Không tìm thấy OBS Virtual Camera. Hãy cài OBS Studio (khuyến nghị 30 trở lên), rồi mở lại ứng dụng. Nếu cần, sửa cài đặt driver webcam của OBS.'
    return 'Không mở hoặc duy trì được OBS Virtual Camera; thiết bị có thể đang bận. Trong OBS, chọn Stop Virtual Camera; dừng ứng dụng phát webcam khác rồi thử lại.'


class VirtualCameraOutput:
    def __init__(self, shared, fps, status_queue, stop_event):
        self.shared = shared
        self.fps = fps
        self.status_queue = status_queue
        self.stop_event = stop_event

    def _status(self, state, message):
        try:
            self.status_queue.put_nowait((state, message))
        except (Full, OSError, ValueError):
            pass

    def run(self):
        self._status('starting', 'Đang mở OBS Virtual Camera…')
        try:
            if self.fps != 25:
                raise _OutputError('Webcam ảo hiện hỗ trợ tốc độ cố định 25 FPS.')
            with _publisher_mutex():
                # Import only inside the spawned output worker. Preview needs no backend.
                import pyvirtualcam
                with pyvirtualcam.Camera(width=self.shared.width, height=self.shared.height,
                                         fps=25, fmt=pyvirtualcam.PixelFormat.RGB,
                                         backend='obs', device='OBS Virtual Camera') as camera:
                    self._status('running', 'Webcam ảo đang phát — chọn OBS Virtual Camera trong ứng dụng nhận.')
                    disconnected = status_frame(self.shared.width, self.shared.height, 'MAT KET NOI')
                    sequence, timestamp, latest = -1, 0.0, None
                    deadline = time.monotonic()
                    while not self.stop_event.is_set():
                        result = self.shared.read(sequence)
                        if result is not None:
                            sequence, timestamp, latest = result
                        now = time.monotonic()
                        frame = latest if latest is not None and now - timestamp <= 5 else disconnected
                        camera.send(frame)
                        deadline = max(deadline + 1 / 25, time.monotonic())
                        self.stop_event.wait(max(0.0, deadline - time.monotonic()))
        except _OutputError as error:
            self._status('error', str(error))
        except Exception as error:
            self._status('error', _backend_error(error))
        else:
            self._status('stopped', 'Webcam ảo đã dừng.')


def run_output(shared, fps, status_queue, stop_event):
    """Top-level multiprocessing spawn target; no camera handle crosses processes."""
    VirtualCameraOutput(shared, fps, status_queue, stop_event).run()
