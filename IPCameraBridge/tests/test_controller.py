"""Lifecycle checks use real spawn workers, including a deliberately hung worker."""
import multiprocessing as mp
import time
import unittest
from unittest.mock import patch

from ip_camera_bridge.controller import BridgeController, ManagedProcess
from ip_camera_bridge.sources import SourceSpec


def hang(statuses, stop):
    while True:
        time.sleep(60)


def hold_output(shared, fps, statuses, stop):
    statuses.put(("running", "test output"))
    stop.wait(60)


def wait_until(predicate, tick=lambda: None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tick()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for worker state")


class LifecycleTests(unittest.TestCase):
    def test_hung_worker_stop_is_bounded_and_reaped(self):
        worker = ManagedProcess(mp.get_context("spawn"))
        worker.start(hang, ())
        pid = worker.process.pid
        started = time.monotonic()
        worker.stop()
        wait_until(lambda: not worker.active, worker.poll, timeout=3)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertNotIn(pid, [p.pid for p in mp.active_children()])

    def test_pattern_repeated_connect_disconnect_no_worker_leak(self):
        bridge = BridgeController(width=320, height=180)
        for _ in range(3):
            bridge.connect(SourceSpec())
            with self.assertRaises(RuntimeError):
                bridge.connect(SourceSpec())
            wait_until(lambda: bridge.source_state == "connected", bridge.tick)
            frame = bridge.preview()
            self.assertEqual(frame.shape, (180, 320, 3))
            bridge.disconnect()
            wait_until(lambda: not bridge.source.active, bridge.tick)
        bridge.close()
        wait_until(lambda: bridge.closed, bridge.tick)
        self.assertEqual(bridge.source_state, "stopped")

    def test_close_active_source_and_retry_stop(self):
        bridge = BridgeController(width=320, height=180)
        bridge.connect(SourceSpec())
        wait_until(lambda: bridge.source_state == "connected", bridge.tick)
        bridge.close()
        wait_until(lambda: bridge.closed, bridge.tick)
        self.assertFalse(bridge.source.active)
        self.assertFalse(bridge.output.active)

    def test_repeated_output_start_stop_rejects_duplicates_and_releases(self):
        bridge = BridgeController(width=320, height=180)
        bridge.source_state = 'connected'
        with patch('ip_camera_bridge.controller.run_output', hold_output):
            for _ in range(3):
                bridge.start_output()
                with self.assertRaises(RuntimeError):
                    bridge.start_output()
                wait_until(lambda: bridge.output_state == 'running', bridge.tick)
                bridge.stop_output()
                wait_until(lambda: not bridge.output.active, bridge.tick)
        bridge.close()
        wait_until(lambda: bridge.closed, bridge.tick)

    def test_missing_file_rejected_before_spawning(self):
        bridge = BridgeController(width=320, height=180)
        with self.assertRaises(ValueError):
            bridge.connect(SourceSpec(kind="file", address="does-not-exist.mp4"))
        self.assertFalse(bridge.source.active)


    def test_watchdog_recovers_when_native_read_resumes(self):
        bridge = BridgeController(width=320, height=180)
        bridge.source_state = "connected"
        old = bridge.shared.read()
        with patch.object(bridge.shared, "read", return_value=(old[0], time.monotonic() - 10, old[2])):
            bridge.tick()
        self.assertEqual(bridge.source_state, "lost")
        bridge.shared.publish(old[2])
        bridge.tick()
        self.assertEqual(bridge.source_state, "connected")

    def test_disconnect_after_watchdog_cannot_resurrect_source(self):
        bridge = BridgeController(width=320, height=180)
        bridge.source_state = 'connected'
        old = bridge.shared.read()
        with patch.object(bridge.shared, 'read', return_value=(old[0], time.monotonic() - 10, old[2])):
            bridge.tick()
        self.assertEqual(bridge.source_state, 'lost')
        bridge.disconnect()
        bridge.tick()
        self.assertEqual(bridge.source_state, 'stopped')

    def test_manual_disconnect_also_reaps_output(self):
        bridge = BridgeController(width=320, height=180)
        bridge.output.start(hang, ())
        try:
            bridge.disconnect()
            wait_until(lambda: not bridge.output.active, bridge.tick, timeout=3)
        finally:
            bridge.close()
            wait_until(lambda: bridge.closed, bridge.tick)


if __name__ == "__main__":
    unittest.main()

