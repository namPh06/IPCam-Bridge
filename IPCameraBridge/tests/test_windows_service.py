import threading
import subprocess
import sys
import win32api
import win32event
import pywintypes
import unittest
from unittest.mock import patch

from ip_camera_bridge import windows_service as service


class ServiceHostTests(unittest.TestCase):
    def test_real_job_creation_in_isolated_host(self):
        result = subprocess.run([sys.executable, '-c',
            'from ip_camera_bridge.windows_service import contain_children; '
            'import subprocess,sys; contain_children(); '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
            'print("job-ready",child.pid,flush=True)'],
            capture_output=True, text=True, timeout=10)
        self.assertIn('job-ready', result.stdout, result.stderr)
        pid = int(result.stdout.split()[-1])
        try:
            process = win32api.OpenProcess(0x100000, False, pid)
        except pywintypes.error as error:
            self.assertEqual(error.winerror, 87)  # Child already reaped.
        else:
            try:
                self.assertEqual(win32event.WaitForSingleObject(process, 2000), win32event.WAIT_OBJECT_0)
            finally:
                process.Close()

    def test_control_queue_has_bounded_backpressure_and_cancellation(self):
        stop = threading.Event()
        requests = service.ControlQueue(stop)
        for _ in range(16):
            requests.pending.put_nowait(({'command': 'status'}, threading.Event(), []))
        self.assertEqual(requests.request({'command': 'status'})['code'], 'busy')
        stop.set()
        self.assertEqual(requests.request({'command': 'status'})['code'], 'stopping')

    def test_owner_policy_absent_is_not_an_installed_service(self):
        with patch.object(service.winreg, 'OpenKey', side_effect=FileNotFoundError):
            self.assertIsNone(service.owner_policy())

    def test_fixed_install_path_rejects_workspace_and_reparse(self):
        with self.assertRaises(ValueError):
            service.validate_executable(__file__)
