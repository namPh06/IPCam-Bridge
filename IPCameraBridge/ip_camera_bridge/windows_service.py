"""SCM host and elevated install operations. No GUI imports in the service."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import winreg

import pywintypes
import servicemanager
import win32api
import win32event
import win32job
import win32security
import win32service
import win32serviceutil

from .service_ipc import PipeServer, SERVICE_NAME
from .service_runtime import CaptureRuntime

POLICY_KEY = r'SOFTWARE\IPCameraBridge'
TASK_NAME = 'IPCameraBridgePublisher'
_JOB = None  # Keep open until process exit: closing it kills this service and its children.


def service_directory():
    return Path(os.environ['ProgramData']) / 'IPCameraBridge' / 'service'


def owner_policy():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return {'owner_sid': winreg.QueryValueEx(key, 'OwnerSid')[0],
                    'executable': winreg.QueryValueEx(key, 'Executable')[0]}
    except FileNotFoundError:
        return None


def service_installed():
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        try:
            handle = win32service.OpenService(scm, SERVICE_NAME, win32service.SERVICE_QUERY_STATUS)
        except pywintypes.error as error:
            if error.winerror == 1060:
                return False
            raise
        win32service.CloseServiceHandle(handle)
        return True
    finally:
        win32service.CloseServiceHandle(scm)


def current_identity(process=None):
    token = win32security.OpenProcessToken(process or win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        return (win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0]),
                win32security.GetTokenInformation(token, win32security.TokenSessionId),
                bool(win32security.GetTokenInformation(token, win32security.TokenElevation)))
    finally:
        token.Close()


def service_report(destination, mode='status', duration=5):
    """Read-only acceptance report: never include camera addresses, names or credentials."""
    from .service_ipc import ServiceClient
    report = {'mode': mode, 'ok': False}
    client = None
    try:
        policy = owner_policy()
        if not policy:
            report['code'] = 'not_installed'
        else:
            client = ServiceClient(policy['owner_sid'])
            status = client.request({'command': 'status'})
            report.update(ok=status.get('ok', False), code=status.get('code'), pid=status.get('data', {}).get('pid'))
            if status.get('ok'):
                report['cameras'] = [{'state': camera['state'], 'fps': camera['fps']} for camera in status['data']['cameras']]
                report['revision'] = status['revision']
                if mode == 'frames':
                    started, sequence, count, stale = time.monotonic(), -1, 0, 0
                    while time.monotonic() - started < duration:
                        frame = client.read_frame(sequence, status['generation'])
                        if frame:
                            sequence = frame[1]
                            count += 1
                            stale += time.monotonic() - frame[2] > 5
                            report['size'] = list(frame[3].shape)
                        time.sleep(.01)
                    report.update(frames=count, stale=stale, seconds=round(time.monotonic() - started, 2))
                    report['fps'] = round(count / report['seconds'], 2)
                    report['ok'] = count > 0 and stale == 0
                elif mode == 'access':
                    try:
                        with (service_directory() / 'cameras.dat').open('rb'):
                            pass
                    except PermissionError:
                        report['private_config_access_denied'] = True
                    except FileNotFoundError:
                        report.update(ok=False, code='no_saved_configuration')
                    else:
                        report.update(ok=False, private_config_access_denied=False)
    except Exception:
        report.update(ok=False, code='service_unavailable')
    finally:
        if client:
            client.close()
    Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1


class ControlQueue:
    def __init__(self, stop):
        self.stop = stop
        self.pending = queue.Queue(16)

    def request(self, message):
        if self.stop.is_set():
            return {'ok': False, 'code': 'stopping'}
        done, result = threading.Event(), []
        try:
            self.pending.put_nowait((message, done, result))
        except queue.Full:
            return {'ok': False, 'code': 'busy'}
        deadline = time.monotonic() + 1.8
        while not done.wait(.04):
            if self.stop.is_set() or time.monotonic() > deadline:
                return {'ok': False, 'code': 'timeout'}
        return result[0]

    def drain(self, runtime):
        for _ in range(16):
            try:
                message, done, result = self.pending.get_nowait()
            except queue.Empty:
                break
            result.append(runtime.handle(message))
            done.set()


def contain_children():
    global _JOB
    _JOB = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(_JOB, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(_JOB, win32job.JobObjectExtendedLimitInformation, limits)
    win32job.AssignProcessToJobObject(_JOB, win32api.GetCurrentProcess())


def grant_owner_identity_query(owner_sid):
    """Allow identity verification only; never grant memory access or token duplication."""
    service_sid = win32security.LookupAccountName(None, 'NT SERVICE\\' + SERVICE_NAME)[0]
    service_text = win32security.ConvertSidToStringSid(service_sid)
    def grant(handle, rights):
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{service_text})(A;;0x{rights:X};;;{owner_sid})', 1)
        win32security.SetSecurityInfo(handle, win32security.SE_KERNEL_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION,
            service_sid, None, descriptor.GetSecurityDescriptorDacl(), None)
    grant(win32api.GetCurrentProcess(), 0x1000)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
        win32security.TOKEN_QUERY | win32security.TOKEN_ADJUST_DEFAULT | 0x20000 | 0x40000 | 0x80000)
    try:
        # New DPAPI/config/temp files must belong to this service, not every LocalService process.
        win32security.SetTokenInformation(token, win32security.TokenOwner, service_sid)
        default_sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{service_text})', 1)
        win32security.SetTokenInformation(token, win32security.TokenDefaultDacl, default_sd.GetSecurityDescriptorDacl())
        grant(token, win32security.TOKEN_QUERY)
    finally:
        token.Close()


class CaptureService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = 'IP Camera Bridge Capture'

    def __init__(self, args):
        super().__init__(args)
        self.stop = threading.Event()
        self.state = win32service.SERVICE_START_PENDING

    def SvcStop(self):
        self.state = win32service.SERVICE_STOP_PENDING
        self.ReportServiceStatus(self.state, waitHint=10000)
        self.stop.set()

    SvcShutdown = SvcStop

    def SvcInterrogate(self):
        self.ReportServiceStatus(self.state)

    def SvcRun(self):
        self.ReportServiceStatus(self.state, waitHint=10000)
        contain_children()
        policy = owner_policy()
        if not policy:
            raise RuntimeError('Service enrollment is missing.')
        grant_owner_identity_query(policy['owner_sid'])
        from .config import setup_logging
        logger = setup_logging(service_directory() / 'service.log')
        runtime = CaptureRuntime(service_directory() / 'cameras.dat')
        server = PipeServer(policy['owner_sid'], self.stop)
        commands = ControlQueue(self.stop)
        try:
            runtime.start()
            server.start(commands.request, runtime.frame)
            self.state = win32service.SERVICE_RUNNING
            self.ReportServiceStatus(self.state)
            logger.info('service ready pid=%d session=0', os.getpid())
            while not self.stop.wait(.04):
                commands.drain(runtime)
                runtime.tick()
        finally:
            self.state = win32service.SERVICE_STOP_PENDING
            self.ReportServiceStatus(self.state, waitHint=10000)
            server.close()
            runtime.close()
            deadline = time.monotonic() + 8
            while not runtime.closed and time.monotonic() < deadline:
                runtime.tick()
                self.ReportServiceStatus(self.state, waitHint=10000)
                time.sleep(.04)
            logger.info('service stopping children_closed=%s', runtime.closed)
            if not runtime.closed:
                raise RuntimeError('Capture workers did not stop.')


def run_service():
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(CaptureService)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


def validate_executable(executable):
    path = Path(executable).absolute()
    expected = Path(os.environ['ProgramFiles']) / 'IPCameraBridge' / 'IPCameraBridge.exe'
    if path != expected or not path.is_file():
        raise ValueError('Install only into Program Files\\IPCameraBridge.')
    for ancestor in (path, *path.parents):
        if ancestor.stat().st_file_attributes & 0x400:
            raise ValueError('Reparse install paths are not supported.')
    return path


def _process_image(handle):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    query.restype = wintypes.BOOL
    buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
    if not query(int(handle), 0, buffer, ctypes.byref(length)):
        raise OSError('Cannot identify installer.')
    return Path(buffer.value)


def _installer_parent():
    # PROCESS_BASIC_INFORMATION: six pointer-sized fields; last is parent PID.
    info = (ctypes.c_void_p * 6)()
    ntdll = ctypes.WinDLL('ntdll')
    query = ntdll.NtQueryInformationProcess
    query.argtypes = (wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)
    query.restype = wintypes.LONG
    if query(int(win32api.GetCurrentProcess()), 0, info, ctypes.sizeof(info), None) != 0:
        raise OSError('Cannot identify installer.')
    return win32api.OpenProcess(0x1000 | 0x100000, False, info[5])


def _owner_handle(owner_pid):
    handle = win32api.OpenProcess(0x1000 | 0x100000, False, owner_pid)
    try:
        sid, session, elevated = current_identity(handle)
        if elevated or session == 0 or session != current_identity()[1]:
            raise ValueError('Run Setup normally from the intended Windows account.')
        parent = _installer_parent()
        try:
            original, installer = _process_image(handle), _process_image(parent)
            if original.suffix.lower() != '.tmp' or installer.suffix.lower() != '.tmp':
                raise ValueError('Installer handoff missing.')
            def digest(path):
                with path.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').digest()
            if digest(original) != digest(installer) or win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_TIMEOUT:
                raise ValueError('Installer identity mismatch.')
        finally:
            parent.Close()
        return handle, sid
    except Exception:
        handle.Close()
        raise


def _protected_data(service_sid):
    directory = service_directory()
    trusted = {'S-1-5-18', 'S-1-5-32-544', service_sid}
    # Refuse hostile pre-created owners/links before changing any ACL. Ownership
    # grants WRITE_DAC even when a restrictive DACL has been applied.
    existing = [directory.parent]
    if directory.parent.exists():
        existing.extend(directory.parent.rglob('*'))
    for path in existing:
        if path.exists():
            owner = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                win32security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner()
            if path.stat().st_file_attributes & 0x400 or win32security.ConvertSidToStringSid(owner) not in trusted:
                raise ValueError('Untrusted service data path.')
    for path in (directory.parent, directory):
        if path.exists() and path.stat().st_file_attributes & 0x400:
            raise ValueError('Reparse data paths are not supported.')
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{service_sid})', 1)
    # Parent is a container only. Protect it before creating the private child.
    for path in (directory.parent, directory):
        if not path.exists():
            attrs = pywintypes.SECURITY_ATTRIBUTES()
            attrs.SECURITY_DESCRIPTOR = descriptor
            import win32file
            win32file.CreateDirectory(str(path), attrs)
        win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION,
            win32security.ConvertStringSidToSid('S-1-5-32-544'), None, descriptor.GetSecurityDescriptorDacl(), None)


def _scheduler():
    import win32com.client
    scheduler = win32com.client.Dispatch('Schedule.Service')
    scheduler.Connect()
    return scheduler, scheduler.GetFolder('\\')


def session_events(sid, create=False):
    handles = []
    security = pywintypes.SECURITY_ATTRIBUTES()
    security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid})', 1)
    try:
        for suffix in ('Stop', 'Stopped'):
            name = f'Global\\IPCameraBridge{suffix}-{sid}'
            handles.append(win32event.CreateEvent(security, True, False, name) if create
                           else win32event.OpenEvent(0x100000 | 2, False, name))
        return handles
    except Exception:
        for handle in handles:
            handle.Close()
        raise


def _register_task(executable, sid):
    scheduler, folder = _scheduler()
    task = scheduler.NewTask(0)
    task.RegistrationInfo.Description = 'Publish IP Camera Bridge video in the enrolled user session.'
    task.Principal.UserId, task.Principal.LogonType, task.Principal.RunLevel = sid, 3, 0
    task.Settings.MultipleInstances = 2
    task.Settings.ExecutionTimeLimit = 'PT0S'
    task.Settings.DisallowStartIfOnBatteries = False
    task.Settings.StopIfGoingOnBatteries = False
    task.Settings.StartWhenAvailable = True
    trigger = task.Triggers.Create(9)
    trigger.UserId = sid
    action = task.Actions.Create(0)
    action.Path, action.Arguments, action.WorkingDirectory = str(executable), '--session-helper', str(executable.parent)
    return folder.RegisterTaskDefinition(TASK_NAME, task, 2, sid, None, 3)


def install_service(executable, owner_pid):
    executable = validate_executable(executable)
    owner, sid = _owner_handle(owner_pid)
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    service, task = None, None
    old_policy = owner_policy()
    try:
        if old_policy and old_policy['owner_sid'] != sid:
            raise ValueError('Existing data belongs to a different Windows account.')
        service = win32service.CreateService(scm, SERVICE_NAME, 'IP Camera Bridge Capture',
            win32service.SERVICE_ALL_ACCESS, win32service.SERVICE_WIN32_OWN_PROCESS,
            win32service.SERVICE_AUTO_START, win32service.SERVICE_ERROR_NORMAL,
            f'"{executable}" --service', None, 0, None, 'NT AUTHORITY\\LocalService', None)
        win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_SERVICE_SID_INFO, 1)
        win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
            {'ResetPeriod': 86400, 'RebootMsg': None, 'Command': None, 'Actions': [(1, 5000), (1, 15000), (1, 30000)]})
        win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
        service_sid = win32security.ConvertSidToStringSid(win32security.LookupAccountName(None, 'NT SERVICE\\' + SERVICE_NAME)[0])
        _protected_data(service_sid)
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, 'OwnerSid', 0, winreg.REG_SZ, sid)
            winreg.SetValueEx(key, 'Executable', 0, winreg.REG_SZ, str(executable))
        task = _register_task(executable, sid)
        win32service.StartService(service, None)
        deadline = time.monotonic() + 20
        while win32service.QueryServiceStatusEx(service)['CurrentState'] != win32service.SERVICE_RUNNING:
            if time.monotonic() >= deadline:
                raise RuntimeError('Service startup timed out.')
            time.sleep(.2)
        task.Run(None)
    except Exception:
        if task is not None:
            task.Enabled = False
            task.Stop(0)
            _scheduler()[1].DeleteTask(TASK_NAME, 0)
        if service is not None:
            try:
                win32service.ControlService(service, win32service.SERVICE_CONTROL_STOP)
            except pywintypes.error:
                pass
            win32service.DeleteService(service)
        if old_policy is None:
            try:
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, winreg.KEY_WOW64_64KEY)
            except FileNotFoundError:
                pass
        raise
    finally:
        if service is not None:
            win32service.CloseServiceHandle(service)
        win32service.CloseServiceHandle(scm)
        owner.Close()


def uninstall_service(remove_data=False):
    _, folder = _scheduler()
    try:
        task = folder.GetTask(TASK_NAME)
    except pywintypes.com_error as error:
        code = error.excepinfo[5] if error.excepinfo else error.hresult
        if code != -2147024894:  # FILE_NOT_FOUND, including IDispatch's wrapped exception.
            raise
    else:
        task.Enabled = False
        policy = owner_policy()
        if policy:
            try:
                stop, stopped = session_events(policy['owner_sid'])
            except pywintypes.error as error:
                if error.winerror != 2:
                    raise
            else:
                try:
                    win32event.SetEvent(stop)
                    if win32event.WaitForSingleObject(stopped, 5000) != win32event.WAIT_OBJECT_0:
                        raise RuntimeError('Publisher did not stop; uninstall cancelled.')
                finally:
                    stop.Close()
                    stopped.Close()
        task.Stop(0)
        folder.DeleteTask(TASK_NAME, 0)
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        try:
            service = win32service.OpenService(scm, SERVICE_NAME, win32service.SERVICE_ALL_ACCESS)
        except pywintypes.error as error:
            if error.winerror != 1060:
                raise
        else:
            try:
                state = win32service.QueryServiceStatusEx(service)
                if state['CurrentState'] != win32service.SERVICE_STOPPED:
                    win32service.ControlService(service, win32service.SERVICE_CONTROL_STOP)
                    deadline = time.monotonic() + 20
                    while win32service.QueryServiceStatusEx(service)['CurrentState'] != win32service.SERVICE_STOPPED:
                        if time.monotonic() > deadline:
                            raise RuntimeError('Service did not stop; uninstall cancelled.')
                        time.sleep(.2)
                win32service.DeleteService(service)
            finally:
                win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(scm)
    if remove_data:
        directory = service_directory()
        # Delete only known protected files; never recursively follow a data path.
        for filename in ('cameras.dat', 'service.log', 'service.log.1', 'service.log.2'):
            path = directory / filename
            if path.exists() and not path.stat().st_file_attributes & 0x400:
                path.unlink()
        try:
            winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            pass
