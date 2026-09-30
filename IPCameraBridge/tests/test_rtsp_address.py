import threading
import unittest
from unittest.mock import patch, MagicMock

from ip_camera_bridge.rtsp_address import expand_rtsp_address
from ip_camera_bridge.onvif_discovery import scan_network, scan_rtsp, discover_onvif
from ip_camera_bridge.config import build_rtsp_url


class AddressTests(unittest.TestCase):
    def test_ip_template_and_full_url_credentials_encoded_once(self):
        self.assertEqual(expand_rtsp_address('192.168.100.77'),
                         'rtsp://192.168.100.77:554/Src/MediaInput/stream_1')
        self.assertEqual(expand_rtsp_address('192.168.1.1:8554', '/live'), 'rtsp://192.168.1.1:8554/live')
        self.assertEqual(expand_rtsp_address('::1', '/live'), 'rtsp://[::1]:554/live')
        url = 'rtsp://192.168.1.1:554/custom?channel=2'
        self.assertEqual(expand_rtsp_address(url), url)
        self.assertIn('admin:pass%40word@', build_rtsp_url(url, 'admin', 'pass@word'))
        for invalid in ('192.168.1.1:0', '192.168.1.1:99999', 'not-an-ip', 'http://192.168.1.1', 'admin:pw@192.168.1.1'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                expand_rtsp_address(invalid)

    def test_subnet_bound_and_cancel(self):
        self.assertEqual(str(scan_network('192.168.100.77/24')), '192.168.100.0/24')
        for invalid in ('10.0.0.0/8', '::/64', '224.0.0.0/24', '127.0.0.0/24'):
            with self.assertRaises(ValueError):
                scan_network(invalid)
        cancel = threading.Event()
        cancel.set()
        with patch('ip_camera_bridge.onvif_discovery.socket.create_connection') as connect:
            self.assertEqual(scan_rtsp('192.168.1.0/30', cancel), [])
            connect.assert_not_called()

    def test_rtsp_response_required_and_auth_challenge_is_detected(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        with patch('ip_camera_bridge.onvif_discovery.socket.create_connection', return_value=sock):
            sock.recv.return_value = b'HTTP/1.1 200 OK\r\n'
            self.assertEqual(scan_rtsp('192.168.1.1/32', threading.Event()), [])
            sock.recv.return_value = b'RTSP/1.0 401 Unauthorized\r\n'
            self.assertEqual(scan_rtsp('192.168.1.1/32', threading.Event())[0].host, '192.168.1.1')

    def test_discovery_binds_each_adapter_and_closes_sockets(self):
        sockets = [MagicMock(), MagicMock()]
        cancel = threading.Event()
        cancel.set()
        with patch('ip_camera_bridge.onvif_discovery.socket.socket', side_effect=sockets):
            self.assertEqual(discover_onvif(interfaces=['10.1.1.2', '192.168.1.2'], cancel=cancel), [])
        for sock, address in zip(sockets, ['10.1.1.2', '192.168.1.2']):
            sock.bind.assert_called_once_with((address, 0))
            self.assertEqual(sock.sendto.call_count, 2)
            sock.close.assert_called_once()
