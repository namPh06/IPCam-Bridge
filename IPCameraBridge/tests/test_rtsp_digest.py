"""Exercise the installed FFmpeg Digest implementation with fake credentials."""
import hashlib
import queue
import re
import socket
import threading
import unittest

import av
from ip_camera_bridge.config import build_rtsp_url


class DigestIntegrationTests(unittest.TestCase):
    def test_ffmpeg_digest_decodes_special_password_once(self):
        password = 'fake@password%40:/?#'
        result = queue.Queue()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(5)

            def serve():
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(5)
                        pending = b''
                        for attempt in range(2):
                            while b'\r\n\r\n' not in pending:
                                data = connection.recv(8192)
                                if not data:
                                    raise RuntimeError('Client closed')
                                pending += data
                            request, pending = pending.split(b'\r\n\r\n', 1)
                            text = request.decode('ascii')
                            seq = re.search(r'(?im)^CSeq:\s*(\d+)', text)[1]
                            if attempt == 0:
                                connection.sendall((f'RTSP/1.0 401 Unauthorized\r\nCSeq: {seq}\r\nWWW-Authenticate: Digest realm="test", nonce="local-test", algorithm=MD5, qop="auth"\r\nContent-Length: 0\r\n\r\n').encode())
                            else:
                                fields = dict(re.findall(r'(\w+)="([^"]*)"', text))
                                nc = re.search(r'\bnc=(\w+)', text)[1]
                                md5 = lambda value: hashlib.md5(value.encode()).hexdigest()
                                ha1 = md5('admin:test:' + password)
                                ha2 = md5(text.split(' ', 1)[0] + ':' + fields['uri'])
                                expected = md5(f"{ha1}:local-test:{nc}:{fields['cnonce']}:auth:{ha2}")
                                result.put(fields['response'] == expected)
                                connection.sendall((f'RTSP/1.0 403 Forbidden\r\nCSeq: {seq}\r\nContent-Length: 0\r\n\r\n').encode())
                except Exception:
                    result.put(False)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            av.logging.set_level(None)
            av.logging.set_libav_level(-8)
            url = build_rtsp_url(f'rtsp://127.0.0.1:{listener.getsockname()[1]}/live', 'admin', password)
            try:
                with av.open(url, options={'rtsp_transport': 'tcp'}, timeout=(3, 3)):
                    pass
            except av.error.FFmpegError:
                pass
            thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertTrue(result.get(timeout=1), 'Digest must use the original raw password')
