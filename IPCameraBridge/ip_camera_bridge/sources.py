"""Spawn-safe camera/file decoders; no native decoder runs on the GUI thread."""

from dataclasses import dataclass, field
import math
import queue
import time

import av

from .config import build_rtsp_url, validate_rtsp_url
from .frames import letterbox, status_frame, test_pattern


@dataclass
class SourceSpec:
    kind: str = 'test'
    address: str = field(default='', repr=False)
    username: str = field(default='', repr=False)
    password: str = field(default='', repr=False)
    connect_timeout: float = 5.0
    read_timeout: float = 3.0
    retry_base: float = 1.0
    retry_cap: float = 15.0
    max_retries: int = 8


def _status(status_queue, state, message):
    try:
        status_queue.put_nowait((state, message))
    except (queue.Full, OSError, ValueError):
        pass


class TestPatternSource:
    def __init__(self, spec):
        self.spec = spec

    def run(self, shared, fps, status_queue, stop_event):
        connected = False
        started = time.monotonic()
        index = 0
        while not stop_event.is_set():
            if shared.publish(test_pattern(shared.width, shared.height, index)) and not connected:
                _status(status_queue, 'connected', 'Mẫu kiểm tra sẵn sàng')
                connected = True
            index = max(index + 1, int((time.monotonic() - started) * fps))
            if stop_event.wait(max(0, started + index / fps - time.monotonic())):
                return


class FileSource:
    def __init__(self, spec):
        self.spec = spec

    def run(self, shared, fps, status_queue, stop_event):
        connected = False
        while not stop_event.is_set():
            # Reopening also resets decoder state after codecs that cannot seek cleanly.
            with av.open(self.spec.address) as container:
                if not container.streams.video:
                    raise ValueError('File has no video stream')
                stream = container.streams.video[0]
                rate = float(stream.average_rate or fps)
                if not math.isfinite(rate) or rate <= 0:
                    rate = fps
                interval = 1 / rate
                started = time.monotonic()
                first_pts = None
                previous_offset = -interval
                count = 0
                for frame in container.decode(stream):
                    if stop_event.is_set():
                        return
                    timestamp = frame.time
                    if timestamp is not None and math.isfinite(timestamp):
                        if first_pts is None:
                            first_pts = timestamp - count * interval
                        offset = max(previous_offset + interval if timestamp - first_pts <= previous_offset else timestamp - first_pts, 0)
                    else:
                        offset = previous_offset + interval
                    previous_offset = offset
                    count += 1
                    delay = started + offset - time.monotonic()
                    if delay < -max(0.2, interval * 2):
                        continue  # Catch up to media time instead of queueing old frames.
                    if stop_event.wait(max(0, delay)):
                        return
                    published = shared.publish(letterbox(frame.to_ndarray(format='rgb24'), shared.width, shared.height))
                    if published and not connected:
                        _status(status_queue, 'connected', 'Tệp video sẵn sàng')
                        connected = True
                if count == 0:
                    raise ValueError('File has no decodable video frames')
                if stop_event.wait(max(0, started + previous_offset + interval - time.monotonic())):
                    return


def rtsp_error_message(error):
    # Only fixed labels and numeric codes; never expose exception text or URL.
    labels = {
        'HTTPUnauthorizedError': 'Camera từ chối xác thực (401); kiểm tra tài khoản/mật khẩu',
        'HTTPForbiddenError': 'Camera từ chối quyền truy cập (403)',
        'HTTPNotFoundError': 'Không tìm thấy đường dẫn RTSP (404)',
        'TimeoutError': 'Hết thời gian chờ camera',
        'ConnectionRefusedError': 'Camera từ chối kết nối TCP',
        'InvalidDataError': 'Dữ liệu RTSP/video không hợp lệ',
        'DecoderNotFoundError': 'Không có bộ giải mã video phù hợp',
        'EOFError': 'Camera kết thúc luồng video',
    }
    message = labels.get(type(error).__name__, 'Không mở hoặc đọc được RTSP')
    code = getattr(error, 'errno', None)
    return message + (f' [code={code}]' if type(code) is int else '')


class RtspSource:
    def __init__(self, spec):
        self.spec = spec

    def run(self, shared, fps, status_queue, stop_event):
        spec = self.spec
        validate_rtsp_url(spec.address)
        url = build_rtsp_url(spec.address, spec.username, spec.password)
        failures = 0
        slate = status_frame(shared.width, shared.height, 'MAT KET NOI - DANG THU LAI')
        while not stop_event.is_set():
            first_frame_at = None
            connected = False
            next_publish = None
            try:
                with av.open(url, mode='r', options={'rtsp_transport': 'tcp'}, timeout=(spec.connect_timeout, spec.read_timeout)) as container:
                    if not container.streams.video:
                        raise ValueError('RTSP has no video stream')
                    for frame in container.decode(container.streams.video[0]):
                        if stop_event.is_set():
                            return
                        now = time.monotonic()
                        if first_frame_at is None:
                            first_frame_at = now
                        if now - first_frame_at >= 10:
                            failures = 0
                        if next_publish is None or now + 1e-6 >= next_publish:
                            published = shared.publish(letterbox(frame.to_ndarray(format='rgb24'), shared.width, shared.height))
                            if published and not connected:
                                _status(status_queue, 'connected', 'Đã kết nối RTSP')
                                connected = True
                            next_publish = (now if next_publish is None else next_publish) + 1 / fps
                            if next_publish < now:
                                next_publish = now + 1 / fps
                    if stop_event.is_set():
                        return
                    raise EOFError('RTSP stream ended')
            except Exception as error:
                if stop_event.is_set():
                    return
                reason = rtsp_error_message(error)
                shared.publish(slate)
                _status(status_queue, 'lost', 'Mất kết nối')
                if failures >= spec.max_retries:
                    _status(status_queue, 'error', f'{reason}. Đã hết số lần thử lại; bấm Kết nối để thử tiếp')
                    return
                delay = min(spec.retry_cap, 15.0, spec.retry_base * 2 ** min(failures, 30))
                failures += 1
                _status(status_queue, 'retrying', f'{reason} — thử lại sau {delay:g} giây ({failures}/{spec.max_retries})')
                if stop_event.wait(delay):
                    return
                _status(status_queue, 'connecting', 'Mất kết nối — đang kết nối lại RTSP')


def run_source(spec, shared, fps, status_queue, stop_event):
    """Process entry point. The controller may terminate this process on native hangs."""
    # Neither FFmpeg diagnostics nor exception strings may disclose an RTSP password.
    av.logging.set_level(None)
    av.logging.set_libav_level(-8)  # AV_LOG_QUIET, including native C diagnostics.
    try:
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('Frame rate must be positive')
        for value in (spec.connect_timeout, spec.read_timeout, spec.retry_base, spec.retry_cap):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Timeouts and retry delays must be positive')
        if not isinstance(spec.max_retries, int) or spec.max_retries < 0:
            raise ValueError('Retry count must be a nonnegative integer')
        source_type = {'test': TestPatternSource, 'file': FileSource, 'rtsp': RtspSource}[spec.kind]
        _status(status_queue, 'connecting', 'Đang kết nối nguồn')
        source_type(spec).run(shared, fps, status_queue, stop_event)
    except Exception:
        if not stop_event.is_set():
            shared.publish(status_frame(shared.width, shared.height, 'KHONG DOC DUOC NGUON'))
            _status(status_queue, 'error', 'Không đọc được nguồn. Kiểm tra địa chỉ, tài khoản hoặc định dạng video.')
    finally:
        if stop_event.is_set():
            _status(status_queue, 'stopped', 'Đã dừng')

