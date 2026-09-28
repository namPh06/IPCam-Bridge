import contextlib
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import av
import cv2
import numpy as np

from ip_camera_bridge.sources import SourceSpec, run_source


class Capture:
    width, height = 64, 48

    def __init__(self, stop, limit=100):
        self.stop, self.limit, self.frames = stop, limit, []

    def publish(self, frame):
        self.frames.append(frame.copy())
        if len(self.frames) >= self.limit:
            self.stop.set()
        return True


class RecordingEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.delays = []

    def wait(self, timeout=None):
        self.delays.append(timeout)
        return super().wait(timeout)


class SourceTests(unittest.TestCase):
    def test_safe_rtsp_diagnostic_preserves_code_without_secret(self):
        from ip_camera_bridge.sources import rtsp_error_message
        error = av.error.HTTPUnauthorizedError(401, 'rtsp://fake:secret@camera/live')
        message = rtsp_error_message(error)
        self.assertIn('401', message)
        self.assertNotIn('secret', message)
        self.assertNotIn('rtsp://', message)
        self.assertNotIn('private', rtsp_error_message(RuntimeError('private')))

    def test_test_source_is_paced_and_changes(self):
        stop, status = threading.Event(), queue.Queue(20)
        shared = Capture(stop, 4)
        start = time.monotonic()
        run_source(SourceSpec(), shared, 25, status, stop)
        self.assertGreaterEqual(time.monotonic() - start, 0.10)
        self.assertFalse(np.array_equal(shared.frames[0], shared.frames[-1]))
        self.assertIn('connected', [state for state, _ in list(status.queue)])
        self.assertEqual(status.get_nowait()[0], 'connecting')
        self.assertEqual(list(status.queue)[-1][0], 'stopped')

    def test_real_video_loops_at_media_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'loop.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for value in (30, 110, 210):
                writer.write(np.full((48, 64, 3), value, dtype=np.uint8))
            writer.release()
            stop, status = threading.Event(), queue.Queue(20)
            shared = Capture(stop, 6)
            start = time.monotonic()
            run_source(SourceSpec(kind='file', address=str(path)), shared, 25, status, stop)
            self.assertEqual(len(shared.frames), 6)
            self.assertGreaterEqual(time.monotonic() - start, 0.45)
            self.assertLess(abs(float(shared.frames[0].mean()) - 30), 3)
            self.assertLess(abs(float(shared.frames[3].mean()) - 30), 3)
            self.assertNotIn('error', [state for state, _ in list(status.queue)])

    def test_file_without_timestamps_uses_stream_rate(self):
        frames = [av.VideoFrame.from_ndarray(np.full((48, 64, 3), value, np.uint8), format='rgb24') for value in (40, 180)]
        container = SimpleNamespace(streams=SimpleNamespace(video=[SimpleNamespace(average_rate=20)]), decode=lambda stream: iter(frames))
        stop, status = threading.Event(), queue.Queue(20)
        shared = Capture(stop, 4)
        with patch('ip_camera_bridge.sources.av.open', side_effect=lambda *args, **kwargs: contextlib.nullcontext(container)):
            start = time.monotonic()
            run_source(SourceSpec(kind='file', address='missing-pts'), shared, 25, status, stop)
        self.assertEqual(len(shared.frames), 4)
        self.assertGreaterEqual(time.monotonic() - start, 0.13)

    def test_rtsp_failures_are_bounded_backed_off_and_redacted(self):
        stop, status = RecordingEvent(), queue.Queue(30)
        shared = Capture(stop)
        spec = SourceSpec(kind='rtsp', address='rtsp://127.0.0.1/live', username='admin', password='secret-value', max_retries=2, retry_base=0.005, retry_cap=0.008)
        with patch('ip_camera_bridge.sources.av.open', side_effect=OSError('rtsp://admin:secret-value@127.0.0.1/live')) as opener:
            run_source(spec, shared, 25, status, stop)
        states = list(status.queue)
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(opener.call_args.kwargs['options']['rtsp_transport'], 'tcp')
        self.assertEqual(opener.call_args.kwargs['timeout'], (5.0, 3.0))
        self.assertEqual(stop.delays, [0.005, 0.008])
        self.assertEqual([state for state, _ in states].count('retrying'), 2)
        self.assertIn('error', [state for state, _ in states])
        self.assertNotIn('secret-value', str(states))
        self.assertIn(('lost', 'Mất kết nối'), states)
        self.assertGreater(len(shared.frames), 0)

    def test_retry_wait_cancels_promptly(self):
        stop, status = threading.Event(), queue.Queue(30)
        shared = Capture(stop)
        spec = SourceSpec(kind='rtsp', address='rtsp://127.0.0.1/live', retry_base=10)
        timer = threading.Timer(0.04, stop.set)
        with patch('ip_camera_bridge.sources.av.open', side_effect=OSError('offline')) as opener:
            timer.start()
            start = time.monotonic()
            try:
                run_source(spec, shared, 25, status, stop)
            finally:
                timer.cancel()
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertEqual(opener.call_count, 1)

    def test_full_status_queue_does_not_block(self):
        stop, status = threading.Event(), queue.Queue(1)
        status.put_nowait(('occupied', 'occupied'))
        shared = Capture(stop, 2)
        run_source(SourceSpec(), shared, 25, status, stop)
        self.assertEqual(len(shared.frames), 2)

class RtspContinuityTests(unittest.TestCase):
    def test_rtsp_publishing_preserves_25fps_at_common_camera_rates(self):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 120, np.uint8), format='rgb24')
        for rate in (25, 30, 50, 60):
            with self.subTest(rate=rate):
                clock = [0.0]
                stop, status = threading.Event(), queue.Queue(20)
                shared = Capture(stop, 10000)
                def decode(stream):
                    for index in range(rate * 4):
                        clock[0] = index / rate
                        yield frame
                    stop.set()
                container = SimpleNamespace(streams=SimpleNamespace(video=[object()]), decode=decode)
                with patch('ip_camera_bridge.sources.time.monotonic', side_effect=lambda: clock[0]), patch('ip_camera_bridge.sources.av.open', return_value=contextlib.nullcontext(container)):
                    run_source(SourceSpec(kind='rtsp', address='rtsp://localhost/live'), shared, 25, status, stop)
                self.assertAlmostEqual(len(shared.frames), 100, delta=1)

    def test_eof_retries_without_resetting_budget_for_flapping_connections(self):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 120, np.uint8), format='rgb24')
        container = SimpleNamespace(streams=SimpleNamespace(video=[object()]), decode=lambda stream: iter([frame]))
        stop, status = threading.Event(), queue.Queue(30)
        shared = Capture(stop)
        spec = SourceSpec(kind='rtsp', address='rtsp://127.0.0.1/live', max_retries=2, retry_base=0.001)
        with patch('ip_camera_bridge.sources.av.open', side_effect=lambda *args, **kwargs: contextlib.nullcontext(container)) as opener:
            run_source(spec, shared, 25, status, stop)
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(len(shared.frames), 6)  # Each brief video is followed by a disconnected slate.
        self.assertEqual(list(status.queue)[-1][0], 'error')
        self.assertTrue(np.all(shared.frames[0] == 120))
        self.assertFalse(np.array_equal(shared.frames[0], shared.frames[1]))

    def test_ten_seconds_of_frames_resets_retry_budget(self):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 120, np.uint8), format='rgb24')
        clock = [0.0]

        def decode(stream):
            yield frame
            clock[0] += 10.1
            yield frame

        container = SimpleNamespace(streams=SimpleNamespace(video=[object()]), decode=decode)
        stop, status = threading.Event(), queue.Queue(30)
        shared = Capture(stop)
        spec = SourceSpec(kind='rtsp', address='rtsp://127.0.0.1/live', max_retries=1, retry_base=0.001)
        with patch('ip_camera_bridge.sources.time.monotonic', side_effect=lambda: clock[0]), patch('ip_camera_bridge.sources.av.open', side_effect=[OSError('offline'), contextlib.nullcontext(container), OSError('offline')]) as opener:
            run_source(spec, shared, 25, status, stop)
        self.assertEqual(opener.call_count, 3)
        self.assertEqual([state for state, _ in list(status.queue)].count('retrying'), 2)
        self.assertEqual(list(status.queue)[-1][0], 'error')

    def test_late_file_frames_are_skipped_before_rgb_conversion(self):
        from fractions import Fraction

        clock = [0.0]
        frames = []
        for index in range(5):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 20 + index * 40, np.uint8), format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 10)
            frames.append(frame)

        def decode(stream):
            clock[0] += 0.35
            yield from frames

        container = SimpleNamespace(streams=SimpleNamespace(video=[SimpleNamespace(average_rate=10)]), decode=decode)
        stop, status = threading.Event(), queue.Queue(20)
        shared = Capture(stop, 3)

        def wait(delay):
            clock[0] += delay
            return stop.is_set()

        with patch('ip_camera_bridge.sources.time.monotonic', side_effect=lambda: clock[0]), patch.object(stop, 'wait', side_effect=wait), patch('ip_camera_bridge.sources.av.open', return_value=contextlib.nullcontext(container)):
            run_source(SourceSpec(kind='file', address='late'), shared, 25, status, stop)
        self.assertEqual([int(frame[0, 0, 0]) for frame in shared.frames], [100, 140, 180])

class SourceSafetyTests(unittest.TestCase):
    def test_source_description_omits_credentials(self):
        spec = SourceSpec(kind='rtsp', address='rtsp://url-user:url-password@localhost/live?token=secret-token', username='form-user', password='form-password')
        description = repr(spec)
        for secret in ('url-user', 'url-password', 'secret-token', 'form-user', 'form-password'):
            self.assertNotIn(secret, description)

    def test_connected_is_reported_after_first_published_frame(self):
        stop = threading.Event()
        shared = Capture(stop, 2)
        frame_counts = []

        class StatusQueue(queue.Queue):
            def put_nowait(self, item):
                if item[0] == 'connected':
                    frame_counts.append(len(shared.frames))
                super().put_nowait(item)

        run_source(SourceSpec(), shared, 25, StatusQueue(20), stop)
        self.assertEqual(frame_counts, [1])


if __name__ == '__main__':
    unittest.main()

