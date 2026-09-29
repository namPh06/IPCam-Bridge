import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ip_camera_bridge import windows_service as service


class InstallTests(unittest.TestCase):
    def test_uninstall_accepts_task_scheduler_wrapped_file_not_found(self):
        folder = MagicMock()
        folder.GetTask.side_effect = service.pywintypes.com_error(-2147352567, 'Exception',
            (0, 'Task Scheduler', 'missing', None, 0, -2147024894), None)
        with patch.object(service, '_scheduler', return_value=(MagicMock(), folder)), \
             patch.object(service.win32service, 'OpenSCManager', return_value=MagicMock()), \
             patch.object(service.win32service, 'OpenService', side_effect=service.pywintypes.error(1060, 'OpenService', 'missing')), \
             patch.object(service.win32service, 'CloseServiceHandle'):
            service.uninstall_service()

    def test_untrusted_precreated_data_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'IPCameraBridge' / 'service'
            directory.mkdir(parents=True)
            with patch.object(service, 'service_directory', return_value=directory):
                with self.assertRaises(ValueError):
                    service._protected_data('S-1-5-80-1-2-3-4-5')

    def test_retained_policy_does_not_mean_service_is_installed(self):
        with patch.object(service.win32service, 'OpenSCManager', return_value=MagicMock()), \
             patch.object(service.win32service, 'OpenService', side_effect=service.pywintypes.error(1060, 'OpenService', 'missing')), \
             patch.object(service.win32service, 'CloseServiceHandle'):
            self.assertFalse(service.service_installed())

    def test_logon_task_uses_owner_and_has_no_battery_or_runtime_limit(self):
        scheduler, folder, task = MagicMock(), MagicMock(), MagicMock()
        scheduler.NewTask.return_value = task
        path = Path('C:/Program Files/IPCameraBridge/IPCameraBridge.exe')
        with patch.object(service, '_scheduler', return_value=(scheduler, folder)):
            service._register_task(path, 'owner-sid')
        self.assertEqual(task.Principal.UserId, 'owner-sid')
        self.assertEqual(task.Principal.LogonType, 3)
        self.assertEqual(task.Principal.RunLevel, 0)
        self.assertEqual(task.Settings.ExecutionTimeLimit, 'PT0S')
        self.assertFalse(task.Settings.DisallowStartIfOnBatteries)
        self.assertFalse(task.Settings.StopIfGoingOnBatteries)
        self.assertEqual(task.Triggers.Create.return_value.UserId, 'owner-sid')
        self.assertEqual(task.Actions.Create.return_value.Arguments, '--session-helper')
        self.assertIsNone(folder.RegisterTaskDefinition.call_args.args[4])

    def test_failure_rolls_back_created_service_and_preserves_previous_owner(self):
        path = Path('C:/Program Files/IPCameraBridge/IPCameraBridge.exe')
        original = {'owner_sid': 'owner', 'executable': str(path)}
        owner, scm, handle = MagicMock(), MagicMock(), MagicMock()
        with patch.object(service, 'validate_executable', return_value=path), \
             patch.object(service, '_owner_handle', return_value=(owner, 'owner')), \
             patch.object(service, 'owner_policy', return_value=original), \
             patch.object(service.win32service, 'OpenSCManager', return_value=scm), \
             patch.object(service.win32service, 'CreateService', return_value=handle) as create, \
             patch.object(service.win32service, 'ChangeServiceConfig2', side_effect=OSError), \
             patch.object(service.win32service, 'ControlService'), \
             patch.object(service.win32service, 'DeleteService') as delete, \
             patch.object(service.win32service, 'CloseServiceHandle'), \
             patch.object(service.winreg, 'DeleteKeyEx') as delete_policy:
            with self.assertRaises(OSError):
                service.install_service(path, 123)
        self.assertEqual(create.call_args.args[-2], 'NT AUTHORITY\\LocalService')
        self.assertEqual(create.call_args.args[7], f'"{path}" --service')
        delete.assert_called_once_with(handle)
        delete_policy.assert_not_called()
        owner.Close.assert_called_once()

    def test_reinstall_cannot_silently_change_owner(self):
        with patch.object(service, 'validate_executable', return_value=Path('app.exe')), \
             patch.object(service, '_owner_handle', return_value=(MagicMock(), 'new-owner')), \
             patch.object(service, 'owner_policy', return_value={'owner_sid': 'old-owner'}), \
             patch.object(service.win32service, 'OpenSCManager', return_value=MagicMock()), \
             patch.object(service.win32service, 'CloseServiceHandle'), \
             patch.object(service.win32service, 'CreateService') as create:
            with self.assertRaises(ValueError):
                service.install_service(Path('app.exe'), 123)
        create.assert_not_called()
