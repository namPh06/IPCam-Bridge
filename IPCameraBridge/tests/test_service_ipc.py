import json
import os
import struct
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import win32api
import win32security

from ip_camera_bridge import service_ipc as ipc


class PipeTests(unittest.TestCase):
    def test_identity_lookup_precedes_client_impersonation(self):
        impersonating = [False]
        sid = win32security.ConvertStringSidToSid('S-1-5-18')
        def lookup():
            self.assertFalse(impersonating[0])
            return sid
        def impersonate(handle):
            impersonating[0] = True
            raise OSError('stop after checking ordering')
        with patch.object(ipc, '_service_sid', side_effect=lookup), \
             patch.object(win32security, 'ImpersonateNamedPipeClient', side_effect=impersonate):
            with self.assertRaises(OSError):
                ipc._verify_client(None, 'S-1-5-18')

    def test_partial_reads_share_one_message_deadline(self):
        clock = [0.0]
        def read(handle, stop, *, size, deadline):
            clock[0] += .7
            if clock[0] >= deadline:
                raise TimeoutError
            return b'x'
        with patch.object(ipc.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(ipc, '_io', side_effect=read):
            with self.assertRaises(TimeoutError):
                ipc._read(object(), 5, threading.Event())
        self.assertLess(clock[0], 3)

    def test_bounded_frame_protocol_and_json_validation(self):
        rgb = np.zeros((720, 1280, 3), np.uint8)
        rgb[0, 0] = (255, 13, 7)
        packet = ipc.encode_frame((4, 23, 100.5, rgb))
        generation, sequence, timestamp, decoded = ipc.decode_frame(packet)
        self.assertEqual((generation, sequence, timestamp), (4, 23, 100.5))
        self.assertEqual(decoded[0, 0].tolist(), [255, 13, 7])
        for bad in (b'x' + packet[1:], packet[:-1], packet + b'x',
                    ipc.FRAME_HEADER.pack(b'IPCB', 1, 999999, 1080, 0, 1, 1.0, 999999)):
            with self.assertRaises(ValueError):
                ipc.decode_frame(bad)
        with self.assertRaises(ValueError):
            ipc.encode_frame((0, 1, float('nan'), rgb))
        for bad in (b'{"command":"status","command":"select"}', b'{"n":NaN}', b'{"n":1e400}',
                    b'[]', b' ' * (ipc.MAX_JSON + 1)):
            with self.assertRaises(ValueError):
                ipc.decode_json(bad)
        for message in ({'command': 'shell'}, {'command': 'status', 'extra': 1},
                        {'command': 'select', 'camera_id': 'bad', 'revision': True}):
            with self.assertRaises(ValueError):
                ipc.validate_request(message)

    def test_pipe_rejects_forged_server_before_sending_credentials(self):
        with patch.object(ipc, '_open_pipe', return_value=object()), \
             patch.object(ipc, '_verify_server', side_effect=PermissionError), \
             patch.object(ipc, '_close_handle'), patch.object(ipc, '_send_json') as send:
            client = ipc.ServiceClient('S-1-5-21-1-2-3-1001')
            with self.assertRaises(OSError):
                client.request({'command': 'status'})
            send.assert_not_called()
            client.close()

    def test_real_pipe_roundtrip_reconnect_and_cancel_idle_read(self):
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            owner = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        names = tuple(r'\\.\pipe\ipcb-test-' + str(uuid4()) for _ in range(2))
        stop = threading.Event()
        rgb = np.zeros((720, 1280, 3), np.uint8)
        rgb[0, 0] = [1, 2, 3]
        server = ipc.PipeServer(owner, stop)
        observed = []
        def handle(request):
            observed.append(request)
            return {'ok': True, 'pid': os.getpid()}
        # Only the trust anchor differs: these endpoints run in this test process,
        # not the registered service. Exercise real Windows DACL, I/O and framing.
        service_sid = win32security.ConvertStringSidToSid(owner)
        with patch.object(ipc, 'PIPE_NAMES', names), patch.object(ipc, '_service_sid', return_value=service_sid), \
             patch.object(ipc, '_verify_server') as verify:
            client = ipc.ServiceClient(owner)
            try:
                server.start(handle, lambda: (3, 9, time.monotonic(), rgb))
                self.assertEqual(client.request({'command': 'status'})['pid'], os.getpid())
                self.assertEqual(client.read_frame(-1, 0)[3][0, 0].tolist(), [1, 2, 3])
                self.assertIsNone(client.read_frame(-1, 4))
                client.close()
                client = ipc.ServiceClient(owner)
                self.assertTrue(client.request({'command': 'status'})['ok'])
                self.assertGreaterEqual(verify.call_count, 3)
                self.assertEqual(observed, [{'command': 'status'}, {'command': 'status'}])
            finally:
                client.close()
                started = time.monotonic()
                server.close()
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(any(worker.is_alive() for worker in server.workers))
