"""Local, authenticated service pipes. Bounded messages; no serialized Python objects."""
import json
import math
import struct
import threading
import time
from uuid import UUID

import numpy as np
import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32pipe
import win32security
import win32service

from .service_config import MAX_JSON

SERVICE_NAME = 'IPCameraBridgeCapture'
PIPE_NAMES = (r'\\.\pipe\IPCameraBridge.control.v1', r'\\.\pipe\IPCameraBridge.frames.v1')
FRAME_HEADER = struct.Struct('!4sB3xIIQQdI')
LENGTH = struct.Struct('!I')
CLIENT_ACCESS = 0x12019B  # Specific read/write rights, excluding CREATE_PIPE_INSTANCE.
MAX_FRAME = FRAME_HEADER.size + 1920 * 1080 * 3


def _header(packet):
    try:
        magic, version, width, height, generation, sequence, stamp, length = FRAME_HEADER.unpack(packet)
        if (magic != b'IPCB' or version != 1 or (width, height) not in ((1280, 720), (1920, 1080))
                or length != width * height * 3 or not math.isfinite(stamp)):
            raise ValueError
        return width, height, generation, sequence, stamp, length
    except (ValueError, struct.error):
        raise ValueError('Invalid frame.') from None


def encode_frame(frame):
    generation, sequence, stamp, rgb = frame
    try:
        height, width, channels = rgb.shape
        if rgb.dtype != np.uint8 or channels != 3:
            raise ValueError
        header = FRAME_HEADER.pack(b'IPCB', 1, width, height, generation, sequence, stamp, rgb.nbytes)
        _header(header)
        return header + rgb.tobytes()
    except (ValueError, TypeError, AttributeError, struct.error):
        raise ValueError('Invalid frame.') from None


def decode_frame(packet):
    width, height, generation, sequence, stamp, length = _header(packet[:FRAME_HEADER.size])
    if len(packet) != FRAME_HEADER.size + length:
        raise ValueError('Invalid frame.')
    rgb = np.frombuffer(packet, dtype=np.uint8, offset=FRAME_HEADER.size).reshape(height, width, 3)
    return generation, sequence, stamp, rgb


def decode_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result
    try:
        if len(data) > MAX_JSON:
            raise ValueError
        result = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Invalid message.') from None


def validate_request(message):
    fields = {'status': set(), 'configure': {'update'}, 'select': {'camera_id', 'revision'},
              'connect': {'camera_id'}, 'disconnect': {'camera_id'}}
    try:
        command = message['command']
        if set(message) != fields[command] | {'command'}:
            raise ValueError
        if command == 'configure' and not isinstance(message['update'], dict):
            raise ValueError
        if command == 'select' and (type(message['revision']) is not int or message['revision'] < 0):
            raise ValueError
        if 'camera_id' in message:
            value = message['camera_id']
            if value is not None or command == 'select':
                if not isinstance(value, str) or str(UUID(value)) != value:
                    raise ValueError
        return message
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Invalid request.') from None


def _service_sid():
    return win32security.LookupAccountName(None, 'NT SERVICE\\' + SERVICE_NAME)[0]


def _close_handle(handle):
    if handle is not None:
        handle.Close()


def _service_pid():
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(scm, SERVICE_NAME, win32service.SERVICE_QUERY_STATUS)
        try:
            state = win32service.QueryServiceStatusEx(service)
            if state['CurrentState'] != win32service.SERVICE_RUNNING or not state['ProcessId']:
                raise PermissionError('Service is not running.')
            return state['ProcessId']
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(scm)


def _verify_server(handle):
    pid = win32pipe.GetNamedPipeServerProcessId(handle)
    if pid != _service_pid():
        raise PermissionError('Untrusted service.')
    process = win32api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    try:
        token = win32security.OpenProcessToken(process, win32security.TOKEN_QUERY)
        try:
            groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            if (win32security.GetTokenInformation(token, win32security.TokenSessionId) != 0
                    or win32security.ConvertSidToStringSid(user) != 'S-1-5-19'
                    or not any(sid == _service_sid() and flags & 4 for sid, flags in groups)):
                raise PermissionError('Untrusted service.')
        finally:
            token.Close()
    finally:
        process.Close()
    if pid != _service_pid():
        raise PermissionError('Service restarted.')


def _verify_client(handle, owner_sid):
    # Account lookup needs the service context, not an identification-only token.
    allowed = {owner_sid, 'S-1-5-18', 'S-1-5-32-544', win32security.ConvertSidToStringSid(_service_sid())}
    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token = win32security.OpenThreadToken(win32api.GetCurrentThread(), win32security.TOKEN_QUERY, True)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
            if (win32security.ConvertSidToStringSid(user) not in allowed
                    and not any(win32security.ConvertSidToStringSid(sid) in allowed and flags & 4 for sid, flags in groups)):
                raise PermissionError('Unauthorized client.')
        finally:
            token.Close()
    finally:
        win32security.RevertToSelf()


def _open_pipe(name):
    win32pipe.WaitNamedPipe(name, 1500)
    return win32file.CreateFile(name, CLIENT_ACCESS, 0, None, win32con.OPEN_EXISTING,
                               win32con.FILE_FLAG_OVERLAPPED | win32con.SECURITY_SQOS_PRESENT
                               | (win32security.SecurityIdentification << 16), None)


def _io(handle, stop, *, size=None, data=None, connect=False, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + 2
    if stop.is_set() or (not connect and time.monotonic() >= deadline):
        raise TimeoutError('Pipe operation cancelled.')
    overlap = pywintypes.OVERLAPPED()
    overlap.hEvent = win32event.CreateEvent(None, True, False, None)
    buffer = None
    pending = False
    try:
        if connect:
            code = win32pipe.ConnectNamedPipe(handle, overlap)
            if code == 535:  # Already connected; no completion event is signaled.
                return None
        elif size is not None:
            code, buffer = win32file.ReadFile(handle, size, overlap)
        else:
            code, _ = win32file.WriteFile(handle, data, overlap)
        pending = code == 997
        while pending and win32event.WaitForSingleObject(overlap.hEvent, 50) == win32event.WAIT_TIMEOUT:
            if stop.is_set() or (not connect and time.monotonic() >= deadline):
                raise TimeoutError('Pipe operation cancelled.')
        count = win32file.GetOverlappedResult(handle, overlap, False)
        pending = False
        return bytes(buffer[:count]) if size is not None else count
    finally:
        if pending:
            # pywin32 312 has CancelIo (not CancelIoEx); called on the issuing thread.
            win32file.CancelIo(handle)
            try:
                win32file.GetOverlappedResult(handle, overlap, True)
            except pywintypes.error:
                pass
        overlap.hEvent.Close()


def _read(handle, size, stop, deadline=None):
    deadline = time.monotonic() + 2 if deadline is None else deadline
    chunks = bytearray()
    while len(chunks) < size:
        chunk = _io(handle, stop, size=size - len(chunks), deadline=deadline)
        if not chunk:
            raise OSError('Pipe closed.')
        chunks.extend(chunk)
    return bytes(chunks)


def _write(handle, data, stop):
    deadline = time.monotonic() + 2
    offset = 0
    while offset < len(data):
        count = _io(handle, stop, data=data[offset:], deadline=deadline)
        if not count:
            raise OSError('Pipe closed.')
        offset += count


def _send_json(handle, message, stop):
    data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > MAX_JSON:
        raise ValueError('Message too large.')
    _write(handle, LENGTH.pack(len(data)) + data, stop)


def _receive_json(handle, stop):
    deadline = time.monotonic() + 2
    length, = LENGTH.unpack(_read(handle, LENGTH.size, stop, deadline))
    if not 0 < length <= MAX_JSON:
        raise ValueError('Invalid message length.')
    return decode_json(_read(handle, length, stop, deadline))


class PipeServer:
    def __init__(self, owner_sid, stop_event):
        self.owner_sid, self.stop = owner_sid, stop_event
        self.workers = []
        self.handles = []

    def start(self, on_request, get_frame):
        sid = win32security.ConvertSidToStringSid(_service_sid())
        sddl = f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid})(A;;0x{CLIENT_ACCESS:X};;;{self.owner_sid})'
        security = pywintypes.SECURITY_ATTRIBUTES()
        security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
        try:
            for index, name in enumerate(PIPE_NAMES):
                for instance in range(4):
                    handle = win32pipe.CreateNamedPipe(
                        name, win32pipe.PIPE_ACCESS_DUPLEX | win32con.FILE_FLAG_OVERLAPPED
                        | (0x80000 if instance == 0 else 0),  # FIRST_PIPE_INSTANCE
                        win32pipe.PIPE_TYPE_BYTE | 0x8, 4, 65536, 65536, 0, security)
                    self.handles.append(handle)
                    worker = threading.Thread(target=self._serve, args=(handle, index, on_request, get_frame), daemon=True)
                    self.workers.append(worker)
            for worker in self.workers:
                worker.start()
        except Exception:
            self.close()
            raise

    def _serve(self, handle, index, on_request, get_frame):
        while not self.stop.is_set():
            try:
                _io(handle, self.stop, connect=True)
                while not self.stop.is_set():
                    message = _receive_json(handle, self.stop)
                    _verify_client(handle, self.owner_sid)
                    if index == 0:
                        _send_json(handle, on_request(validate_request(message)), self.stop)
                    else:
                        if (set(message) != {'after', 'generation'} or type(message['after']) is not int
                                or type(message['generation']) is not int or message['generation'] < 0):
                            raise ValueError('Invalid frame request.')
                        frame = get_frame()
                        packet = b''
                        if frame is not None and frame[0] >= message['generation'] and (
                                frame[0] > message['generation'] or frame[1] > message['after']):
                            packet = encode_frame(frame)
                        _write(handle, LENGTH.pack(len(packet)) + packet, self.stop)
            except (OSError, pywintypes.error, ValueError, TypeError):
                pass  # Never log request bodies or raw OS errors containing credentials.
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(handle)
                except pywintypes.error:
                    pass

    def close(self):
        self.stop.set()
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(timeout=2.2)
        for handle in self.handles:
            _close_handle(handle)
        self.handles.clear()


class ServiceClient:
    def __init__(self, owner_sid):
        self.owner_sid = owner_sid
        self.stop = threading.Event()
        self.handles = [None, None]
        self.locks = [threading.Lock(), threading.Lock()]

    def _connection(self, index):
        if self.stop.is_set():
            raise OSError('Client closed.')
        if self.handles[index] is None:
            handle = _open_pipe(PIPE_NAMES[index])
            try:
                _verify_server(handle)
            except Exception:
                _close_handle(handle)
                raise
            self.handles[index] = handle
        return self.handles[index]

    def request(self, message):
        validate_request(message)
        with self.locks[0]:
            try:
                handle = self._connection(0)
                _send_json(handle, message, self.stop)
                return _receive_json(handle, self.stop)
            except (OSError, pywintypes.error):
                _close_handle(self.handles[0])
                self.handles[0] = None
                raise OSError('Không kết nối được Windows Service.') from None

    def read_frame(self, last_sequence, min_generation):
        with self.locks[1]:
            try:
                handle = self._connection(1)
                _send_json(handle, {'after': last_sequence, 'generation': min_generation}, self.stop)
                deadline = time.monotonic() + 2
                length, = LENGTH.unpack(_read(handle, LENGTH.size, self.stop, deadline))
                if length == 0:
                    return None
                if not FRAME_HEADER.size < length <= MAX_FRAME:
                    raise ValueError('Invalid frame length.')
                header = _read(handle, FRAME_HEADER.size, self.stop, deadline)
                metadata = _header(header)
                if metadata[-1] + FRAME_HEADER.size != length:
                    raise ValueError('Invalid frame length.')
                frame = decode_frame(header + _read(handle, metadata[-1], self.stop, deadline))
                return frame if frame[0] >= min_generation else None
            except (OSError, pywintypes.error, ValueError):
                _close_handle(self.handles[1])
                self.handles[1] = None
                raise OSError('Không nhận được hình từ Windows Service.') from None

    def close(self):
        self.stop.set()
        for index, lock in enumerate(self.locks):
            with lock:
                _close_handle(self.handles[index])
                self.handles[index] = None
