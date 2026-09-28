"""Process lifetime and UI-independent bridge state."""
import logging
import multiprocessing as mp
from pathlib import Path
from queue import Empty
import time

from .config import validate_rtsp_url
from .frames import SharedFrame, status_frame
from .output import run_output
from .sources import run_source

LOG = logging.getLogger("ip_camera_bridge")


class ManagedProcess:
    """Poll-based shutdown keeps the GUI responsive even when a decoder hangs."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.process = None
        self.stop_event = None
        self.statuses = None
        self.stopping_at = None
        self.terminated = False
        self.exitcode = None

    @property
    def active(self):
        return self.process is not None

    def start(self, target, args):
        if self.active:
            raise RuntimeError("Tiến trình đang hoạt động.")
        self.stop_event = self.ctx.Event()
        self.statuses = self.ctx.Queue(maxsize=16)
        self.stopping_at = None
        self.terminated = False
        self.exitcode = None
        process = self.ctx.Process(
            target=target, args=(*args, self.statuses, self.stop_event), daemon=True
        )
        try:
            process.start()
        except Exception:
            self.statuses.close()
            self.statuses = None
            raise RuntimeError("Không khởi động được tiến trình.") from None
        self.process = process

    def stop(self):
        if self.active and self.stopping_at is None:
            self.stopping_at = time.monotonic()
            self.stop_event.set()

    def poll(self):
        if not self.active:
            return []
        events = []
        # Ignore a stopped child's queue: terminating a queue writer may corrupt it.
        if self.stopping_at is None:
            for _ in range(16):
                try:
                    events.append(self.statuses.get_nowait())
                except (Empty, EOFError, OSError):
                    break
        if self.process.is_alive() and self.stopping_at is not None:
            elapsed = time.monotonic() - self.stopping_at
            if elapsed > 0.5 and not self.terminated:
                self.process.terminate()
                self.terminated = True
            elif elapsed > 1.5:
                self.process.kill()
        if not self.process.is_alive():
            self.process.join(timeout=0)
            self.exitcode = self.process.exitcode
            self.process.close()
            self.process = None
            self.statuses.cancel_join_thread()
            self.statuses.close()
            self.statuses = None
        return events


class BridgeController:
    def __init__(self, width=1280, height=720, fps=25):
        self.ctx = mp.get_context("spawn")
        self.fps = fps
        self.shared = SharedFrame(self.ctx, width, height)
        self.source = ManagedProcess(self.ctx)
        self.output = ManagedProcess(self.ctx)
        self.source_state = "stopped"
        self.output_state = "stopped"
        self.source_message = "Chưa kết nối nguồn."
        self.output_message = "Webcam ảo chưa bật."
        self.processing_fps = 0.0
        self.closing = False
        self._watchdog_lost = False
        self._seq = -1
        self._image = status_frame(width, height, "CHUA KET NOI")
        self._frame_at = 0.0
        self._fps_at = time.monotonic()
        self._fps_seq = 0
        self._lost_image = status_frame(width, height, "MAT KET NOI")
        self.shared.publish(self._image)

    @property
    def closed(self):
        return self.closing and not self.source.active and not self.output.active

    def set_resolution(self, width, height):
        if self.source.active or self.output.active:
            raise RuntimeError("Hãy dừng nguồn và webcam trước khi đổi độ phân giải.")
        self.shared = SharedFrame(self.ctx, width, height)
        self._seq = -1
        self._image = status_frame(width, height, "CHUA KET NOI")
        self._lost_image = status_frame(width, height, "MAT KET NOI")
        self.shared.publish(self._image)

    def connect(self, spec):
        if self.closing or self.source.active or self.output.stopping_at is not None and self.output.active:
            raise RuntimeError("Nguồn đang hoạt động hoặc đang dừng.")
        if spec.kind == "file" and not Path(spec.address).is_file():
            raise ValueError("Hãy chọn một file video có sẵn.")
        if spec.kind == "rtsp":
            validate_rtsp_url(spec.address)
        if spec.kind not in ("test", "file", "rtsp"):
            raise ValueError("Loại nguồn không hợp lệ.")
        # A terminated child could own the old frame lock. With no output attached,
        # renew the shared slot rather than reuse an abandoned OS semaphore.
        if not self.output.active:
            self.set_resolution(self.shared.width, self.shared.height)
        self.shared.publish(status_frame(self.shared.width, self.shared.height, "DANG KET NOI"))
        self._watchdog_lost = False
        self.source.start(run_source, (spec, self.shared, self.fps))
        self.source_state = "connecting"
        self.source_message = "Đang kết nối…"
        self._fps_at = time.monotonic()
        self._fps_seq = 0
        LOG.info("source connecting kind=%s", spec.kind)

    def disconnect(self):
        self._watchdog_lost = False
        self.stop_output()
        self.source.stop()
        self.source_state = "stopping" if self.source.active else "stopped"
        self.source_message = "Đang ngắt nguồn…" if self.source.active else "Đã ngắt nguồn."
        self._image = status_frame(self.shared.width, self.shared.height, "DA NGAT NGUON")
        self.shared.publish(self._image)
        self.processing_fps = 0.0

    def start_output(self):
        if self.closing or self.output.active:
            raise RuntimeError("Webcam đang hoạt động hoặc đang dừng.")
        if self.source_state != "connected":
            raise RuntimeError("Hãy kết nối nguồn và chờ có hình trước.")
        self.output.start(run_output, (self.shared, self.fps))
        self.output_state = "starting"
        self.output_message = "Đang mở OBS Virtual Camera…"
        LOG.info("virtual camera starting")

    def stop_output(self):
        self.output.stop()
        self.output_state = "stopping" if self.output.active else "stopped"
        self.output_message = "Đang dừng webcam…" if self.output.active else "Webcam ảo đã dừng."

    def tick(self):
        was_source = self.source.active
        was_output = self.output.active
        for state, message in self.source.poll():
            self._watchdog_lost = False
            self.source_state, self.source_message = state, message
            LOG.info("source state=%s message=%s", state, message)
        for state, message in self.output.poll():
            self.output_state, self.output_message = state, message
            LOG.info("output state=%s", state)
        if was_source and not self.source.active:
            self._watchdog_lost = False
            stopped = self.source.stopping_at is not None
            if stopped:
                self.source_state, self.source_message = "stopped", "Đã ngắt nguồn."
            elif self.source_state != "error":
                self.source_state, self.source_message = "error", "Nguồn đã dừng. Hãy kết nối lại."
            self.processing_fps = 0.0
            self._image = status_frame(self.shared.width, self.shared.height, "DA NGAT NGUON")
            self.shared.publish(self._image)
        if was_output and not self.output.active:
            stopped = self.output.stopping_at is not None
            if stopped:
                self.output_state, self.output_message = "stopped", "Webcam ảo đã dừng."
            elif self.output_state != "error":
                self.output_state, self.output_message = "error", "Webcam đã dừng. Hãy thử bật lại."
        result = self.shared.read(self._seq)
        now = time.monotonic()
        if result is not None:
            self._seq, self._frame_at, rgb = result
            if self._watchdog_lost and self.source_state == "lost" and now - self._frame_at <= 5:
                self._watchdog_lost = False
                self.source_state, self.source_message = "connected", "Đã nhận lại hình từ nguồn."
            if self.source_state not in ("stopped", "stopping", "error"):
                self._image = rgb
        if now - self._fps_at >= 1:
            self.processing_fps = (
                max(0, self._seq - self._fps_seq) / (now - self._fps_at)
                if self.source_state == "connected" else 0
            )
            self._fps_seq, self._fps_at = self._seq, now
        if self.source_state == "connected" and now - self._frame_at > 5:
            self._watchdog_lost = True
            self.source_state, self.source_message = "lost", "Mất kết nối — đang chờ nguồn."
            self._image = self._lost_image
            self.processing_fps = 0.0
        elif self.source_state in ("lost", "retrying"):
            self._image = self._lost_image

    def preview(self):
        return self._image

    def close(self):
        self.closing = True
        self.disconnect()

