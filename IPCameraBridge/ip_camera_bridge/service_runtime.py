"""Single-owner capture state; the SCM thread owns all mutations."""
import copy
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from .cameras import CameraGroup
from .controller import BridgeController
from .service_config import load_service_config, merge_update, public_config, save_service_config
from .service_ipc import validate_request
from .sources import SourceSpec

SIZES = {'720p': (1280, 720), '1080p': (1920, 1080)}


class CaptureRuntime:
    def __init__(self, config_path):
        self.path = Path(config_path)
        self.group = None
        self.config = None
        self.wanted = set()
        self.retry = {}
        self.generation = 0
        self.sequence = 0
        self.lock = threading.Lock()
        self.snapshot = None
        self.closing = False
        self.transient = False

    def start(self):
        camera_id = str(uuid4())
        self.config = load_service_config(self.path) or {
            'version': 2, 'revision': 0, 'resolution': '720p', 'auto_connect': False,
            'selected_id': camera_id, 'cameras': [
                {'id': camera_id, 'name': 'Camera 1', 'kind': 'test', 'address': '', 'username': '', 'password': ''}],
        }
        self.group = CameraGroup(*SIZES[self.config['resolution']])
        for _ in self.config['cameras'][1:]:
            self.group.add()
        self.group.select(self._index(self.config['selected_id']))
        if self.config['auto_connect']:
            self.wanted.update(camera['id'] for camera in self.config['cameras'])
        self.tick()

    def _index(self, camera_id):
        return next(index for index, camera in enumerate(self.config['cameras']) if camera['id'] == camera_id)

    def _response(self, code='ok'):
        return {'ok': code == 'ok', 'code': code, 'revision': self.config['revision'], 'generation': self.generation,
                'data': {'config': public_config(self.config), 'pid': os.getpid(), 'sequence': self.sequence,
                         'cameras': [{'id': spec['id'], 'state': camera.source_state, 'message': camera.source_message,
                                      'fps': round(camera.processing_fps, 1), 'wanted': spec['id'] in self.wanted}
                                     for spec, camera in zip(self.config['cameras'], self.group.cameras)]}}

    def handle(self, request):
        try:
            validate_request(request)
            command = request['command']
            if command == 'status':
                return self._response()
            if command in ('configure', 'select'):
                update = request.get('update')
                if command == 'select':
                    update = copy.deepcopy(self.config)
                    update.update(selected_id=request['camera_id'], revision=request['revision'])
                if update.get('revision') != self.config['revision']:
                    return self._response('stale_revision')
                new = merge_update(self.config, update)
                persist = request.get('persist', True if command == 'configure' else not self.transient)
                if not persist and new['auto_connect']:
                    return self._response('invalid_request')
                previous = {spec['id']: (spec, camera) for spec, camera in zip(self.config['cameras'], self.group.cameras)}
                incoming = {spec['id']: spec for spec in new['cameras']}
                if self.group.active and new['resolution'] != self.config['resolution']:
                    return self._response('source_busy')
                for camera_id, (old, camera) in previous.items():
                    replacement = incoming.get(camera_id)
                    if camera.source.active and (replacement is None or any(
                            old[key] != replacement[key] for key in ('kind', 'address', 'username', 'password'))):
                        return self._response('source_busy')
                # Allocate new decoders before saving; existing ones keep running if saving fails.
                width, height = SIZES[new['resolution']]
                cameras = [previous[spec['id']][1] if spec['id'] in previous else BridgeController(width, height)
                           for spec in new['cameras']]
                try:
                    if persist:
                        save_service_config(self.path, new)
                except ValueError:
                    return self._response('save_failed')
                for camera_id, (_, camera) in previous.items():
                    if camera_id not in incoming:
                        camera.close()
                        self.wanted.discard(camera_id)
                        self.retry.pop(camera_id, None)
                if new['resolution'] != self.config['resolution']:
                    self.group.set_resolution(width, height)
                self.group.cameras = cameras
                self.config = new
                self.transient = not persist
                self.group.select(self._index(new['selected_id']))
                self.generation += 1
                if new['auto_connect'] and command == 'configure':
                    self.wanted.update(incoming)
                self.tick()
            elif command in ('connect', 'disconnect'):
                ids = {request['camera_id']} if request['camera_id'] else {spec['id'] for spec in self.config['cameras']}
                if not ids <= {spec['id'] for spec in self.config['cameras']}:
                    return self._response('invalid_camera')
                for camera_id in ids:
                    self.retry.pop(camera_id, None)
                    if command == 'connect':
                        self.wanted.add(camera_id)
                    else:
                        self.wanted.discard(camera_id)
                        self.group.cameras[self._index(camera_id)].disconnect()
                self.generation += 1
                self.tick()
            return self._response()
        except (ValueError, KeyError, TypeError, StopIteration):
            return self._response('invalid_request')

    def tick(self):
        self.group.tick()
        now = time.monotonic()
        if not self.closing:
            for spec, camera in zip(self.config['cameras'], self.group.cameras):
                camera_id = spec['id']
                if camera_id not in self.wanted:
                    continue
                if camera.source_state == 'connected':
                    self.retry.pop(camera_id, None)
                if camera.source.active:
                    continue
                due, attempt = self.retry.get(camera_id, (0, 0))
                if now < due:
                    continue
                try:
                    if spec['kind'] == 'file':
                        path = Path(spec['address'])
                        if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
                            raise ValueError
                        with path.open('rb') as stream:
                            stream.read(1)
                    camera.connect(SourceSpec(**{key: spec[key] for key in ('kind', 'address', 'username', 'password')},
                                              max_retries=None, retry_cap=30))
                except (ValueError, OSError, RuntimeError):
                    camera.source_state, camera.source_message = 'error', 'Không đọc được nguồn; đang chờ thử lại.'
                self.retry[camera_id] = (now + min(30, 2 ** min(attempt, 5)), attempt + 1)
        current = self.group.current
        stamp = current._frame_at if current.source_state == 'connected' else now
        self.sequence += 1
        frame = (self.generation, self.sequence, stamp, current.preview().copy())
        with self.lock:
            self.snapshot = frame

    def frame(self):
        with self.lock:
            return self.snapshot

    def close(self):
        self.closing = True
        self.wanted.clear()
        if self.group is not None:
            self.group.close()

    @property
    def closed(self):
        return self.closing and (self.group is None or self.group.closed)
