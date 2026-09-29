"""RGB frame generation, resizing, and one bounded shared-memory frame slot."""

import time
import math

import cv2
import numpy as np


def letterbox(rgb, width, height):
    """Fit an RGB uint8 image inside the output canvas without distortion."""
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1:
        raise ValueError('Expected a nonempty RGB uint8 image')
    if width < 1 or height < 1:
        raise ValueError('Output dimensions must be positive')
    source_height, source_width = rgb.shape[:2]
    if (source_width, source_height) == (width, height):
        return np.ascontiguousarray(rgb)
    scale = min(width / source_width, height / source_height)
    scaled_width = max(1, min(width, round(source_width * scale)))
    scaled_height = max(1, min(height, round(source_height * scale)))
    resized = cv2.resize(rgb, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    output = np.zeros((height, width, 3), dtype=np.uint8)
    x, y = (width - scaled_width) // 2, (height - scaled_height) // 2
    output[y:y + scaled_height, x:x + scaled_width] = resized
    return output


def test_pattern(width, height, index):
    """Moving RGB bars with an explicit frame counter and elapsed time at 25 fps."""
    colors = np.array([(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)], dtype=np.uint8)
    image = np.repeat(colors[np.arange(width) * len(colors) // width][None, :, :], height, axis=0)
    band = max(12, height // 6)
    image[-band:] = 24
    x = (index * max(1, width // 100)) % width
    cv2.rectangle(image, (x, 0), (min(width - 1, x + max(4, width // 40)), height - band - 1), (20, 20, 20), -1)
    scale = max(0.35, width / 1280)
    cv2.putText(image, f'TEST  Frame {index:08d}  Time {index / 25:08.2f}s', (max(4, width // 50), height - band // 3), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), max(1, round(scale * 2)), cv2.LINE_AA)
    return image


def status_frame(width, height, message):
    image = np.full((height, width, 3), (22, 27, 35), dtype=np.uint8)
    scale = max(0.35, min(width / 900, width / max(1, len(message) * 22)))
    size, baseline = cv2.getTextSize(message, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, round(scale * 2)))
    position = (max(4, (width - size[0]) // 2), (height + size[1] - baseline) // 2)
    cv2.putText(image, message, position, cv2.FONT_HERSHEY_SIMPLEX, scale, (220, 225, 235), max(1, round(scale * 2)), cv2.LINE_AA)
    return image


class SharedFrame:
    """Latest frame only; an abandoned writer lock cannot stall the consumer."""

    def __init__(self, ctx, width, height):
        if width < 1 or height < 1:
            raise ValueError('Frame dimensions must be positive')
        self.width, self.height = width, height
        self._pixels = ctx.RawArray('B', width * height * 3)
        self._sequence = ctx.RawValue('q', 0)
        self._timestamp = ctx.RawValue('d', 0)
        self._lock = ctx.Lock()

    def publish(self, rgb, timestamp=None):
        if not isinstance(rgb, np.ndarray) or rgb.shape != (self.height, self.width, 3) or rgb.dtype != np.uint8:
            raise ValueError('Shared frame must match the RGB uint8 output canvas')
        if timestamp is not None and not math.isfinite(timestamp):
            raise ValueError('Frame timestamp must be finite')
        if not self._lock.acquire(timeout=0.01):
            return False
        try:
            np.copyto(np.frombuffer(self._pixels, dtype=np.uint8).reshape(rgb.shape), rgb)
            self._timestamp.value = time.monotonic() if timestamp is None else timestamp
            self._sequence.value += 1
            return True
        finally:
            self._lock.release()

    def read(self, last_seq=-1):
        if not self._lock.acquire(timeout=0.01):
            return None
        try:
            sequence = self._sequence.value
            if sequence == 0 or sequence == last_seq:
                return None
            pixels = np.frombuffer(self._pixels, dtype=np.uint8).reshape(self.height, self.width, 3).copy()
            return sequence, self._timestamp.value, pixels
        finally:
            self._lock.release()
