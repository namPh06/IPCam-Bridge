# Windows Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cấu hình camera một lần; service nhận hình trước đăng nhập và bộ xuất ẩn cung cấp OBS Virtual Camera cho Meet/Zoom sau đăng nhập.

**Architecture:** Capture chạy dưới LocalService; named pipe có xác thực truyền trạng thái, cấu hình và khung mới nhất sang đúng tài khoản sở hữu. Publisher và cửa sổ quản lý dùng chung một tiến trình trong phiên đăng nhập, tái sử dụng bộ xuất OBS hiện tại. Giữ nguyên chế độ desktop độc lập khi chưa cài service.

**Tech Stack:** Windows x64, CPython 3.13, PySide6/PyAV/numpy/pyvirtualcam và unittest hiện có; thêm pywin32==312; PyInstaller onedir và Inno Setup để tạo bộ cài EXE.

**Spec:** [2026-09-28-windows-service-design.md](../specs/2026-09-28-windows-service-design.md)

Trạng thái: kế hoạch để duyệt; chưa triển khai hoặc cài service. Ngày 2026-09-28 chạy lại bộ kiểm thử hiện tại: 50 tests, OK, 11.994 giây. Kết quả này chỉ là baseline của bản desktop.

## Global Constraints

- Tên service `IPCameraBridgeCapture`, startup Automatic, chạy bằng `NT AUTHORITY\LocalService` với service SID riêng.
- Một tài khoản Windows sở hữu cấu hình trên một laptop Windows x64; không hỗ trợ nhiều phiên RDP đồng thời.
- Giữ nhiều camera kết nối đồng thời, chọn một camera để xuất, giữ Test Pattern.
- Mật khẩu nhập riêng trong UI, ký tự đặc biệt được mã hóa URL đúng một lần, không hardcode hoặc ghi vào log.
- Service mã hóa bằng DPAPI dưới danh tính của service; `%ProgramData%\IPCameraBridge\service\cameras.dat` chỉ service SID, SYSTEM và Administrators được truy cập.
- Named pipe chỉ trên máy, từ chối remote client; không pickle, không HTTP/TCP listener, không lệnh thực thi shell/ghi file tùy ý.
- Camera RTSP mất mạng tiếp tục thử có backoff tối đa 30 giây; ảnh trạng thái sau 5 giây không có khung mới.
- Khung RGB tối đa 1920×1080×3 bytes; đầu ra giữ 25 fps, hai lựa chọn 720p/1080p.
- Đóng cửa sổ quản lý không dừng nguồn hoặc publisher; đăng xuất dừng publisher nhưng service vẫn chạy.
- Không tự reboot, logoff hoặc dừng phiên camera người dùng đang dùng để nghiệm thu.

## Review Focus

- Sửa/xóa/đổi thứ tự camera không gán nhầm mật khẩu: dùng ID bền vững và revision, kiểm thử Task 1.
- UAC dùng tài khoản quản trị khác không đổi owner: giữ PID/token của người khởi chạy trước nâng quyền, kiểm thử Task 6.
- Pipe giả hoặc bị thay thế sau service restart không nhận được mật khẩu: kiểm tra peer lại trên mỗi kết nối, kiểm thử Task 2.
- Đổi camera trong lúc frame cũ còn đang truyền không hiện lại camera trước: generation trong header và loại frame cũ, kiểm thử Tasks 3/5.
- Mạng chỉ sẵn sàng sau hơn tám retry, hoặc OBS đang bận khi đăng nhập: tự phục hồi nhưng Stop thủ công hủy retry, kiểm thử Tasks 3/5.

## File map và thứ tự

Đường dẫn dưới đây tính từ `IPCameraBridge/`; chạy lệnh kiểm thử trong thư mục đó.
Tasks 1–6 xây cùng một luồng service → pipe → publisher; không giao nhiều người sửa cùng file đồng thời.

| File | Trách nhiệm |
|---|---|
| `ip_camera_bridge/service_config.py` (mới) | Schema service, ID/revision, giữ/thay secret, DPAPI và chuyển cấu hình cũ |
| `ip_camera_bridge/service_ipc.py` (mới) | Named pipe, ACL/peer, JSON giới hạn và frame có generation |
| `ip_camera_bridge/service_runtime.py` (mới) | Điều phối CameraGroup không GUI/publisher, trạng thái và lệnh |
| `ip_camera_bridge/windows_service.py` (mới) | SCM host, Job Object, cài/gỡ service và thư mục/quyền |
| `ip_camera_bridge/service_session.py` (mới) | Client, publisher, retry, singleton và khay hệ thống |
| `sources.py`, `controller.py`, `cameras.py` | Retry lâu dài và tái sử dụng vòng điều phối; không đổi mặc định desktop |
| `ui.py`, `__main__.py`, `windows_settings.py` | Chọn mode, quản lý từ xa, migration, startup owner |
| `IPCameraBridge-service.spec`, `installer.iss` (mới), `build.ps1` | Payload onedir, installer EXE và uninstall |
| `tests/test_service_*.py`, `tools/service_acceptance.py` (mới) | Kiểm thử hành vi và nghiệm thu có báo cáo đã che secret |
| `README.md`, `docs/MANUAL_TESTS.md`, `docs/TEST_REPORT.md`, `docs/DEPENDENCIES.md` | Hướng dẫn và bằng chứng thực tế |

## Task 1: Cấu hình service và chuyển credential an toàn

**Files:** Create `ip_camera_bridge/service_config.py`, `tests/test_service_config.py`; modify `windows_settings.py` chỉ để dùng lại DPAPI qua hàm có tên công khai, giữ tương thích `_crypt`/các test cũ.

**Interfaces:**
- `validate_service_config(data: dict) -> dict`: schema version 2; `revision: int >= 0`, `resolution: '720p'|'1080p'`, `auto_connect: bool`, `selected_id: str`, 1..16 cameras có UUID `id`, `name`, `kind`, `address`, `username`, `password`.
- `merge_update(current: dict, update: dict) -> dict`: revision phải khớp; ID đã có + `password: null` giữ secret; chuỗi kể cả rỗng thay secret; camera mới không chấp nhận null. `address: null` tương tự giữ địa chỉ cũ theo ID. Sau khi giữ/thay, validate cấu hình đầy đủ rồi tăng revision.
- `public_config(config: dict) -> dict`: bỏ password, thay bằng `has_password: bool`; không đưa userinfo/password trong URL trở lại UI. URL có query trả `address: null` cùng `address_hint` chỉ scheme/host/path; UI gửi null để giữ hoặc chuỗi để thay địa chỉ. Update chỉ nhận các field chỉnh sửa, không nhận has_password/address_hint từ snapshot.
- `migrate_profiles(legacy: dict, resolution: str) -> dict`: tạo UUID, giữ camera đang chọn, không đổi password nguyên bản.
- `load_service_config(path: Path) -> dict|None`, `save_service_config(path: Path, config: dict) -> None`: DPAPI của tiến trình gọi + atomic_write hiện có; không tự tạo thư mục chưa có ACL.

- [ ] **Test đỏ:** thêm test `test_reorder_preserves_password_by_id_and_rejects_stale_revision`; các assert chính: `merged['cameras'][0]['password'] == original_by_id[id]['password']`, `merged['revision'] == old_revision + 1`; stale revision và null cho ID mới phải raise ValueError.
- [ ] **Test đỏ:** thêm `test_service_config_keeps_secret_out_of_public_data_and_disk`; assert mật khẩu giả `fake@%40` không xuất hiện trong JSON public/ciphertext/exception, lỗi atomic replace giữ nguyên file cũ. Reject duplicate ID, bool làm index/revision, 17 camera, URL sai; tên ≤128, URL/path ≤4096, username/password ≤1024 ký tự, JSON ≤1 MiB. Với URL cũ có userinfo, tách/giải mã userinfo đúng một lần vào trường riêng trong migration; nếu có secret ở query thì public URL được che và UI có lựa chọn giữ địa chỉ cũ.
- [ ] Chạy `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_service_config.py -v`; xác nhận fail vì thiếu chức năng, không phải lỗi import dependency không liên quan.
- [ ] Implement các interface trên; tận dụng `_crypt`, `atomic_write`, `validate_rtsp_url`, không tạo kho password thứ hai hoặc đổi DPAPI sang machine-scope.
- [ ] Chạy lại test mới cùng `test_windows_settings.py`, `test_config.py`, `test_rtsp_digest.py`; PASS. Commit riêng task: `feat: add protected service camera configuration`.

## Task 2: Kênh named pipe có kiểm tra danh tính

**Files:** Create `ip_camera_bridge/service_ipc.py`, `tests/test_service_ipc.py`; modify `requirements.txt`, `requirements-lock.txt`, `docs/DEPENDENCIES.md` để pin `pywin32==312` cho Windows.

**Interfaces:**
- `PipeServer(owner_sid: str, stop_event: threading.Event)`; `start(on_request: Callable[[dict], dict], get_frame: Callable[[], tuple|None]) -> None`, `close() -> None`.
- `ServiceClient(owner_sid: str)`; `request(message: dict) -> dict`, `read_frame(last_sequence: int, min_generation: int) -> tuple|None`, `close() -> None`.
- Frame tuple: `(generation: int, sequence: int, timestamp: float, rgb: ndarray[uint8])`. `encode_frame(...) -> bytes`, `decode_frame(packet: bytes) -> tuple` kiểm tra header trước cấp phát payload.
- Hai pipe cố định `\\.\pipe\IPCameraBridge.control.v1` và `\\.\pipe\IPCameraBridge.frames.v1`; wire version 1 độc lập schema config version 2.
- JSON request/response UTF-8 với prefix uint32 độ dài; trần 1 MiB, cấm NaN/Infinity, field lạ, kiểu sai. Frame header network order `!4sB3xIIQQdI`: magic `IPCB`, version 1, width, height, generation, sequence, timestamp, payload length; chỉ 1280×720 hoặc 1920×1080 và payload đúng width×height×3.

- [ ] **Test đỏ:** `test_pipe_rejects_forged_server_and_revalidates_after_reconnect`: giả token/PID server phải bị từ chối trước lần write đầu tiên (`write_mock.assert_not_called()`); service PID thay đổi bắt buộc kiểm tra lại.
- [ ] **Test đỏ:** `test_bounded_frame_protocol_and_cancellable_io`: header giả, length quá lớn, RGB thiếu byte, timestamp không finite đều raise ValueError trước đọc/cấp phát lớn; frame generation thấp bị bỏ; stop phải hủy I/O đang treo ≤2 giây.
- [ ] Cài dependency đã pin trong venv và chạy `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_service_ipc.py -v`; xác nhận test đỏ.
- [ ] Implement bằng pywin32 overlapped I/O, explicit DACL cho owner/service SID/SYSTEM/Admin, `PIPE_REJECT_REMOTE_CLIENTS`, `FILE_FLAG_FIRST_PIPE_INSTANCE` cho instance đầu. Không cấp quyền tạo server pipe cho client qua generic-all. Client so PID pipe với `QueryServiceStatusEx`, SID service trong token và Session 0. Server kiểm tra token client/owner, luôn RevertToSelf trước DPAPI/lưu file; không tin SID trong message.
- [ ] Control request đưa vào hàng đợi tối đa 16, 4 client đồng thời, trả lỗi cố định khi đầy. Frame mỗi lần chỉ lấy bản mới nhất, 1 frame đang ghi/client, không queue backlog; timeout I/O 2 giây, stop CancelIoEx, join có hạn. Không giữ lock CameraGroup trong I/O.
- [ ] Chạy test mới PASS; kiểm tra thực tế quyền bằng Task 7 dưới service account và tài khoản không phải owner. Commit `feat: add authenticated local service pipes`.

## Task 3: Nhận nhiều camera liên tục trong service

**Files:** Create `ip_camera_bridge/service_runtime.py`, `tests/test_service_runtime.py`; modify `sources.py`, `cameras.py`; chỉ sửa `controller.py` nếu cần truyền tùy chọn đã định nghĩa.

**Interfaces:**
- `SourceSpec.max_retries: int|None = 8`; None nghĩa không giới hạn. Desktop giữ retry_cap 15; service dùng retry_cap 30, max_retries None. Validation từ chối bool/giá trị âm; tính delay không hardcode trần 15 bên ngoài retry_cap.
- `CaptureRuntime(config_path: Path)`; `start() -> None`, `handle(request: dict) -> dict`, `tick() -> None`, `frame() -> tuple|None`, `close() -> None`, property `closed: bool`.
- Lệnh control: `status`, `configure` (config update + revision), `select` (camera_id + revision), `connect`/`disconnect` (camera_id hoặc null cho tất cả). Response `{ok, code, revision, generation, data}`; code cố định, không gửi exception text.
- Status gồm config public, trạng thái từng camera/fps, PID service và frame sequence/generation; không chứa raw SourceSpec. Runtime là nơi duy nhất chỉnh CameraGroup; không bật CameraGroup.auto_output hoặc gọi start_output.

- [ ] **Test đỏ:** `test_service_rtsp_recovers_after_more_than_eight_failures`: giả 10 lỗi rồi khung hợp lệ; assert connected, max(delay) ≤30 và stop không đợi hết backoff. Desktop test ngân sách 8 phải giữ kết quả cũ; lỗi 401 sau lần đầu chờ 30 giây giữa các lần thử.
- [ ] **Test đỏ:** `test_switch_offline_never_replays_previous_camera`: hai nguồn khác màu; select nguồn offline tăng generation và ngay lập tức có slate, camera còn lại không bị restart; disconnect/xóa hủy retry tương ứng.
- [ ] **Test đỏ:** `test_failed_save_does_not_replace_running_configuration`: save lỗi giữ revision/config/decoder hiện có. Sửa nguồn đang chạy phải trả `source_busy` trước ghi; cho sửa tên/chọn nguồn đang chạy; thay resolution yêu cầu tất cả nguồn đã ngắt.
- [ ] Chạy test mới để thấy fail. Implement CaptureRuntime dùng CameraGroup/BridgeController hiện có với vòng tick 40 ms; trạng thái cùng frame được chụp dưới lock ngắn. Commit generation ngay khi select/disconnect/đổi resolution; timestamp frame dùng đồng hồ monotonic, không thời gian nhận ở client.
- [ ] Cấu hình trống chưa có file: service ready với một Test Pattern chưa kết nối, auto_connect false. Khi save thành công và auto_connect true thì nối các nguồn; boot đọc cấu hình tự nối theo lựa chọn đã lưu. Worker chết ngoài ý muốn được runtime khởi động lại có backoff 1..30 giây; Stop thủ công không khởi động lại.
- [ ] Select lưu selected_id nguyên tử trước ack để lần khởi động sau dùng đúng camera; save lỗi giữ camera cũ. FileSource chỉ chấp nhận đường dẫn local service đọc được, từ chối UNC/mapped drive thiếu quyền bằng mã lỗi cố định; không tự sửa ACL video. Thêm assert lựa chọn tồn tại sau tạo lại runtime và file không đọc được không chặn camera khác.
- [ ] Chạy `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_service_runtime.py -v` và test_sources/test_cameras/test_controller, PASS. Commit `feat: run persistent multi-camera capture without a GUI`.

## Task 4: Windows Service host và dọn worker

**Files:** Create `ip_camera_bridge/windows_service.py`, `tests/test_service_host.py`; modify `__main__.py`, `run.py` nếu cần dispatcher trước Qt.

**Interfaces:**
- `CameraCaptureService(win32serviceutil.ServiceFramework)` với `_svc_name_='IPCameraBridgeCapture'`, `SvcDoRun`, `SvcStop`, `SvcShutdown`; override `SvcRun` vì implementation mặc định báo RUNNING trước SvcDoRun. Chỉ báo RUNNING sau tạo runtime/pipe xong, không chờ camera có frame.
- `dispatch_service() -> int`: frozen EXE `--service` gọi servicemanager.Initialize/PrepareToHostSingle/StartServiceCtrlDispatcher; không import Qt hoặc tạo QApplication.
- `install_service(executable: Path, owner_pid: int) -> None`, `uninstall_service(remove_data: bool=False) -> None`: chỉ từ helper nâng quyền/bộ cài, đường dẫn install cố định; không expose trên pipe.
- `owner_policy() -> dict`: HKLM Software\IPCameraBridge chứa owner SID, schema và đường dẫn cố định, user chỉ đọc; data/log nằm dưới ProgramData/service với protected DACL.

- [ ] **Test đỏ:** `test_scm_ready_does_not_wait_for_camera`: runtime camera offline không ngăn báo RUNNING; stop báo STOP_PENDING trước close, STOPPED sau cleanup, không mở Qt/pyvirtualcam.
- [ ] **Test đỏ:** `test_service_job_reaps_worker_on_host_crash`: tiến trình host thử nghiệm tham gia Job Object KILL_ON_JOB_CLOSE rồi spawn worker; terminate host khiến worker hết tồn tại. Job handle không inheritable; gán job trước khi spawn, fail closed nếu không gán được. Giữ handle đến khi host exit, không close trong cleanup vì sẽ giết cả service trước báo STOPPED.
- [ ] Chạy `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_service_host.py -v`; xác nhận fail, rồi implement SCM wrapper quanh CaptureRuntime/PipeServer, reuse logging rotation có lọc. Báo shutdown checkpoint khi đang đợi; teardown pipe/runtime có hạn 10 giây.
- [ ] Cài service Automatic, LocalService, service SID unrestricted; SCM recovery lần 1/2/sau đó: 5/15/30 giây, reset counter sau 86400 giây; không recovery cho Stop đúng chủ đích. Không cho user thường sửa binary path, owner policy hoặc quyền service.
- [ ] PASS test; frozen service thật kiểm tra ở Tasks 6/7. Commit `feat: host capture in Windows Service Control Manager`.

## Task 5: Publisher ẩn và UI quản lý service

**Files:** Create `ip_camera_bridge/service_session.py`, `tests/test_service_session.py`; modify `ui.py`, `__main__.py`, `windows_settings.py`, `tests/test_ui.py`.

**Interfaces:**
- `ServiceSession(owner_sid: str)` sở hữu ServiceClient, SharedFrame, ManagedProcess output, timer/retry và khay; `open_window()`, `start_output()`, `stop_output()`, `close()`. close chỉ đóng helper/publisher, không Stop service.
- `run_service_session(hidden: bool) -> int`: logon `--session-helper` không show window; mở app thường khi đã cài service thì mở quản lý trong singleton hiện có. `--desktop` chỉ dùng khi service/helper không xuất; smoke cũ luôn desktop và không chạm cấu hình service.
- `MainWindow(..., session: ServiceSession|None=None)`: nhánh service đọc snapshot và gửi lệnh qua session; không tạo CameraGroup, decoder, auto-save profile desktop hoặc ghi HKCU startup cũ. Giữ UI hiện tại, không xây hệ thống plugin/abstract factory.
- Migration UI dùng migrate_profiles ở Task 1, gửi cấu hình qua client Task 2; ack save mới bỏ HKCU Run desktop cũ. Task logon tạo ở Task 6 chạy `"<installed exe>" --session-helper`; helper chờ cấu hình service, không tự nhập hoặc xóa profile cũ khi chưa có lựa chọn người dùng. Không tắt autostart cũ khi request/ack thất bại.

- [ ] **Test đỏ:** `test_hidden_logon_and_close_window_keep_capture_running`: helper tạo zero visible window, mở/đóng UI không gọi disconnect/SCM stop; reopen không tạo decoder/publisher thứ hai.
- [ ] **Test đỏ:** `test_busy_obs_and_service_restart_recover_without_reviving_manual_stop`: retry 1,2,4,8,15,30 giây (trần 30), Stop thủ công hủy lịch thử; đăng nhập phiên mới đọc auto_connect đã lưu; mất pipe cho slate sau ≤5 giây. Service restart phải lấy snapshot đã xác thực rồi reset sequence/min_generation về epoch mới, không giữ generation cũ khiến mọi frame mới bị bỏ.
- [ ] **Test đỏ:** `test_old_generation_is_dropped_after_switch`: response select cập nhật min_generation trước nhận frame tiếp; frame cũ không ghi SharedFrame. Source timestamp quá 5 giây không được làm mới bằng thời gian nhận.
- [ ] **Test đỏ:** `test_migration_waits_for_durable_ack_and_does_not_fetch_saved_password`: save timeout giữ HKCU Run/file cũ; thành công gửi đúng một lần password nguyên bản; chỉnh tên bằng password null; chọn thay password rỗng thật sự xóa secret.
- [ ] Implement client I/O ở worker thread, UI chỉ dùng snapshot/callback Qt; SharedFrame → run_output giữ backend hiện có. Khi resolution đổi, restart publisher có kiểm soát với slot mới; đổi camera cùng resolution không restart publisher.
- [ ] Singleton theo owner/session: QLocalServer đặt UserAccessOption, tên chứa SID/session ID, protocol chỉ `show`; kiểm tra tiến trình đang giữ mutex trước xóa stale endpoint. QSystemTrayIcon có Mở quản lý, Dừng/Bật webcam, Thoát bộ xuất (xác nhận rõ service vẫn chạy). Service lỗi/OBS bận hiển thị trạng thái, không tự chuyển desktop rồi tạo capture thứ hai.
- [ ] Chạy test_session/test_ui và toàn bộ unittest PASS; smoke desktop vẫn đóng sạch, không đăng ký startup. Commit `feat: publish service frames from a hidden user session`.

## Task 6: Bộ cài một EXE và rollback

**Files:** Create `IPCameraBridge-service.spec`, `installer.iss`, `tests/test_service_install.py`; modify `build.ps1`, `requirements-build.txt`, `windows_service.py`, `README.md`.

**Interfaces:**
- `build.ps1 -Service`: test → PyInstaller onedir `dist-service\IPCameraBridge\` → Inno Setup → `dist-service\IPCameraBridge-Setup.exe`. Giữ build onefile desktop hiện có, không ghi đè EXE đang chạy.
- Frozen EXE có `--install-service --owner-pid <pid>` và `--uninstall-service`; installer chạy từ payload dưới Program Files sau copy, không từ thư mục giải nén PyInstaller tạm. Không dùng pythonservice.exe hoặc yêu cầu Python toàn máy.
- Installer lấy PID tiến trình khởi chạy trước nâng quyền và giữ tiến trình này sống đến khi cài xong. Helper nâng quyền mở token của PID đó để lấy owner SID/session; không suy owner từ tài khoản admin hoặc nhận SID do API client tự khai.

- [ ] **Test đỏ:** `test_install_uses_original_owner_and_restricts_paths`: token owner khác token admin vẫn lưu owner; từ chối PID đã mất/session sai, target ngoài Program Files, reparse point trong install/data path; account LocalService và quoted binary path chính xác.
- [ ] **Test đỏ:** `test_install_failure_rolls_back_without_deleting_profiles`: thất bại từng bước SCM/ACL/start/owner startup không để autostart trỏ binary thiếu; giữ camera ciphertext, old desktop Run và OBS; reinstall không tự chuyển owner.
- [ ] Implement Inno bootstrap `PrivilegesRequired=lowest`, `PrivilegesRequiredOverridesAllowed=commandline`, `UsePreviousPrivileges=no`; relaunch cùng installer qua ShellExec runas với /ALLUSERS và owner PID để UAC một lần; tiến trình gốc chờ đến hết. Helper kiểm tra token/session/image của PID gốc và giữ process handle đến hết enrollment. Nếu chạy trực tiếp bằng Run as administrator và không xác định được owner ban đầu, hiện yêu cầu mở lại bình thường, không đoán SID. Đây là cách ghép API tài liệu, cần smoke test bộ cài thật để xác nhận.
- [ ] Helper tạo thư mục protected DACL, đăng ký SCM/owner policy rồi Start. Inno tạo fixed payload dưới Program Files trước khi gọi helper. Task `\\IPCameraBridgePublisher` có owner SID ở principal/trigger, InteractiveToken=3, RunLevel thấp nhất, MultipleInstances=IgnoreNew=2, ExecutionTimeLimit=PT0S, không chặn/dừng khi dùng pin. Không ghi HKCU của admin; migration chỉ xóa HKCU Run cũ trong phiên owner sau ack. Task không lưu mật khẩu Windows, binary/arguments cố định, không cho user khác sửa task.
- [ ] Gỡ vô hiệu hóa task trước, gửi yêu cầu thoát helper đúng owner, dừng task nếu cần, Stop service/đợi worker hết, gỡ đúng task/service rồi Inno xóa payload. Mặc định giữ dữ liệu, xóa dữ liệu chỉ khi người dùng chọn rõ. Uninstall không cần nạp profile/HKCU của owner đã đăng xuất. Rollback gỡ chỉ tài nguyên vừa tạo, giữ cấu hình lần cài trước.
- [ ] Thêm assert vào test_install: task timeout PT0S và hai battery flags false; owner SID ở principal/trigger; gỡ task xảy ra trước xóa EXE. Xác nhận task khởi động lại sau logon mà không hiện cửa sổ.
- [ ] Xác minh PyInstaller gom pywintypes313/pythoncom313 và win32/servicemanager DLLs bằng frozen run; không chạy pywin32_postinstall trong venv. Khi thiếu Inno compiler, build báo bước cài dependency build rõ ràng, không tự tải/chạy script không kiểm tra.
- [ ] Chạy test_install PASS, build service và thử install/start/stop/uninstall trên Windows thử nghiệm. Commit `build: package the camera service and hidden publisher installer`.

## Task 7: Nghiệm thu thực tế và hướng dẫn vận hành

**Files:** Create `tools/service_acceptance.py`; modify `docs/MANUAL_TESTS.md`, `docs/TEST_REPORT.md`, `README.md`.

**Interfaces:** `service_acceptance.py --check {status,frames,access,shutdown} --report <path>` chỉ đọc/truy vấn các phép kiểm tương ứng; báo cáo JSON dùng mã lỗi cố định, không URL/username/password. Các thao tác cài/Start/Stop dùng lệnh Windows ghi riêng, không ngầm thực hiện khi chạy status/frames.

- [ ] Chạy toàn bộ `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`; desktop smoke `.\.venv\Scripts\python.exe run.py --smoke-test artifacts\desktop-regression --smoke-cameras 2`; PASS, không worker mồ côi.
- [ ] SCM thật: `sc.exe qc IPCameraBridgeCapture`, `sc.exe qsidtype IPCameraBridgeCapture`, `sc.exe queryex IPCameraBridgeCapture`; account/start/path/SID đúng thiết kế. Start/Stop/restart và kill host trong phiên thử riêng; recovery đúng, Stop không recovery, tất cả worker được dọn.
- [ ] `Get-ScheduledTask -TaskName IPCameraBridgePublisher`: owner/trigger/action, không giới hạn 72 giờ, không dừng khi dùng pin; `sc.exe qfailure IPCameraBridgeCapture`: đúng recovery. Kiểm tra ACL Program Files, ProgramData/service và owner policy từ cả owner lẫn non-owner.
- [ ] Service DPAPI thật: lưu mật khẩu giả chứa @/%40 qua owner pipe, restart service đọc lại; truy cập pipe/file từ non-owner bị từ chối; packet malformed không crash hoặc ghi secret vào Event Log/log. Không in/decrypt cấu hình camera thật để kiểm chứng.
- [ ] Chạy 720p25 và 1080p25 ít nhất 60 giây với hai Test Pattern: frame mới có sequence tăng, không backlog/giữ RAM tăng theo thời gian, client chậm không chặn capture. Ghi CPU/RAM/độ trễ/fps thực tế và giới hạn đo; không biến chỉ tiêu camera nguồn 15 fps thành yêu cầu có 25 frame nội dung mới.
- [ ] Người dùng thực hiện reboot/logoff phù hợp: xác nhận frame service có trước logon bằng PID/session + thời điểm service/frame đầu, helper chỉ sau logon, UI không bật lên. Ghi bằng chứng qua log an toàn; không đánh dấu đạt nếu chỉ chạy foreground.
- [ ] Camera thật: mạng ngắt/lên muộn hơn tám retry cũ, khôi phục tự động; chọn giữa hai nguồn, đóng UI, dừng webcam, đăng nhập lại. Nếu không truy cập được mạng công ty, giao bản build + hướng dẫn để người dùng gửi log đã che credential.
- [ ] Meet và Zoom: xác nhận riêng từng ứng dụng có video chuyển động khi chọn OBS Virtual Camera; đổi nguồn không chọn lại webcam. Dùng micro riêng; OBS chỉ cần cài, ứng dụng OBS không cần mở.
- [ ] Máy Windows x64 không Python: cài một EXE, UAC đúng owner, chạy sau boot; uninstall giữ dữ liệu mặc định và không gỡ OBS. Ghi rõ Passed/Failed/Chưa kiểm được, không nhận hoàn thành dựa trên unit test.
- [ ] Cập nhật báo cáo, đường dẫn bộ cài/hash, hướng dẫn dừng/gỡ và rollback; review diff toàn nhánh; commit `docs: record service deployment and camera acceptance results` sau những kiểm tra thực sự đã làm.

## Cách triển khai đề nghị

Native: tôi thực hiện lần lượt từng task trong phiên này vì các phần dùng chung schema, UI và lifecycle; sau cùng nhờ một reviewer độc lập kiểm tra toàn bộ thay đổi. Nếu chọn subagent-driven, mỗi task được giao một agent mới và review trước task kế tiếp; không chạy đồng thời các task phụ thuộc interface nhau.

Chỉ bắt đầu sửa mã/cài dependency sau khi người dùng duyệt kế hoạch này và chọn cách triển khai. Reboot/logoff hoặc gián đoạn camera đang dùng vẫn cần phối hợp ở thời điểm nghiệm thu, không được suy ra từ việc duyệt kế hoạch.

## Nguồn xác minh dependency và nền tảng

- [pywin32 312 và wheel CPython 3.13 x64](https://pypi.org/project/pywin32/312/)
- [pywin32 service utility](https://github.com/mhammond/pywin32/blob/main/win32/Lib/win32serviceutil.py)
- [Microsoft: named pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
- [Microsoft: interactive services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)
- [Inno Setup: privilege override](https://jrsoftware.org/ishelp/topic_setup_privilegesrequiredoverridesallowed.htm)
- [Inno Setup: ShellExec và chờ tiến trình](https://jrsoftware.org/ishelp/topic_isxfunc_shellexec.htm)
- [Microsoft: thời hạn mặc định của scheduled task](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-executiontimelimit)
