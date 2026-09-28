import multiprocessing
import time
import unittest

import numpy as np

from ip_camera_bridge.frames import SharedFrame, letterbox, status_frame, test_pattern


class FrameTests(unittest.TestCase):
    def test_letterbox_preserves_rgb_and_aspect(self):
        source = np.zeros((2, 4, 3), dtype=np.uint8)
        source[:] = (230, 40, 10)
        output = letterbox(source, 8, 8)
        self.assertEqual(output.shape, (8, 8, 3))
        self.assertTrue(np.all(output[:2] == 0))
        self.assertTrue(np.all(output[2:6] == (230, 40, 10)))
        self.assertTrue(np.all(output[6:] == 0))
        self.assertEqual(output.dtype, np.uint8)

    def test_invalid_rgb_is_rejected(self):
        for source in (np.zeros((2, 2)), np.zeros((2, 2, 3)), np.zeros((0, 2, 3), dtype=np.uint8)):
            with self.assertRaises(ValueError):
                letterbox(source, 8, 8)

    def test_pattern_moves_and_status_is_visible(self):
        first, second = test_pattern(320, 180, 0), test_pattern(320, 180, 10)
        self.assertFalse(np.array_equal(first, second))
        self.assertEqual(first.dtype, np.uint8)
        self.assertGreater(np.unique(status_frame(320, 180, 'Disconnected')).size, 4)

    def test_shared_slot_overwrites_and_read_returns_owned_copy(self):
        slot = SharedFrame(multiprocessing.get_context('spawn'), 4, 2)
        self.assertIsNone(slot.read())
        self.assertTrue(slot.publish(np.full((2, 4, 3), 11, dtype=np.uint8)))
        self.assertTrue(slot.publish(np.full((2, 4, 3), 77, dtype=np.uint8)))
        seq, timestamp, output = slot.read()
        self.assertEqual(seq, 2)
        self.assertGreater(timestamp, 0)
        self.assertTrue(np.all(output == 77))
        output[:] = 0
        self.assertTrue(np.all(slot.read()[2] == 77))
        self.assertIsNone(slot.read(seq))

    def test_locked_slot_never_blocks_consumer_or_producer(self):
        slot = SharedFrame(multiprocessing.get_context('spawn'), 4, 2)
        slot._lock.acquire()
        try:
            start = time.monotonic()
            self.assertIsNone(slot.read())
            self.assertFalse(slot.publish(np.zeros((2, 4, 3), dtype=np.uint8)))
            self.assertLess(time.monotonic() - start, 0.25)
        finally:
            slot._lock.release()


if __name__ == '__main__':
    unittest.main()
