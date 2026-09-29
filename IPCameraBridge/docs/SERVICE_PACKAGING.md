# Đóng gói bản Windows Service

Máy build cần Windows x64, Python 3.13 x64 cùng các dependency đã khóa, và [Inno Setup 6.7.3 trở lên](https://jrsoftware.org/isdl.php). Máy nhận không cần Python. OBS Virtual Camera vẫn là thành phần cài riêng.

```powershell
.\run.ps1 -Setup
.\build.ps1 -Service
```

Nếu compiler hoặc venv nằm ở vị trí khác:

```powershell
.\build.ps1 -Service -PythonPath 'C:\path\venv\Scripts\python.exe' -ISCCPath 'C:\path\Inno Setup 6\ISCC.exe'
```

Build chạy toàn bộ unittest trước khi đóng gói. Payload nằm trong `dist-service\IPCameraBridge\`; bản gửi sang máy khác là **`dist-service\IPCameraBridge-Setup.exe`** và file `.sha256` để đối chiếu. Không chạy EXE trong thư mục `build-service`, không tách EXE khỏi `_internal` của payload.

Nếu thiếu Inno, payload vẫn được tạo nhưng lệnh build dừng với hướng dẫn cài compiler; chưa có bộ cài EXE. Build không tự tải hoặc chạy chương trình cài dependency. Lệnh `build.ps1` không có `-Service` vẫn tạo bản desktop một file như trước.

## Cài đặt và gỡ

Mở bộ cài bình thường trong tài khoản Windows sẽ dùng camera; **không chọn Run as administrator**. Bộ cài tự yêu cầu UAC. Có thể nhập tài khoản quản trị khác; tài khoản sở hữu camera vẫn lấy từ tiến trình mở bộ cài ban đầu. Không truyền mật khẩu Windows hoặc camera trên dòng lệnh.

Payload cài cố định tại `%ProgramFiles%\IPCameraBridge`. Helper đăng ký service `IPCameraBridgeCapture` và tác vụ đăng nhập `IPCameraBridgePublisher`; cấu hình service nằm tại `%ProgramData%\IPCameraBridge\service\cameras.dat`. Mở IP Camera Bridge từ Start để quản lý camera. OBS chỉ cần cài sẵn, không cần mở ứng dụng OBS.

Nếu đã có thư mục cài đặt, bộ cài yêu cầu gỡ bản cũ và giữ dữ liệu rồi cài lại; không ghi đè ứng dụng đang chạy. Thư mục dữ liệu có owner không tin cậy hoặc directory link bị từ chối để tránh sửa quyền nhầm thư mục của người khác.

Bootstrap không nâng quyền dùng thư mục tạm và không chép payload. Bản nâng quyền dùng thư mục Program Files cố định. Cần giữ `CreateAppDir=yes`: [Inno quy định `CreateAppDir=no` chuyển `{app}` thành thư mục Windows](https://jrsoftware.org/ishelp/topic_setup_createappdir.htm), nên không dùng nó để tắt ghi file ở bootstrap.

Gỡ bằng **Settings → Apps → Installed apps → IP Camera Bridge → Uninstall**. Mặc định giữ cấu hình camera. Chỉ chọn xóa dữ liệu khi muốn xóa các camera và mật khẩu đã lưu. Bộ gỡ dừng task, publisher và service trước khi xóa chương trình; không gỡ OBS.

Hiện chưa hỗ trợ ghi đè bản service đang cài. Bộ cài từ chối trước khi thay file: gỡ bản cũ, chọn giữ dữ liệu, rồi cài bản mới bằng cùng tài khoản. Giữ lại bộ cài cũ để quay lại phiên bản trước. Không sao chép ciphertext sang máy Windows khác.

## Luồng UAC và giới hạn kiểm chứng

Bootstrap dùng `PrivilegesRequired=lowest` và `/ALLUSERS`; tiến trình ban đầu giữ nguyên token và chờ bộ cài nâng quyền hoàn thành. Lệnh relaunch ở `ssInstall` vì [Inno không cho ShellExec chạy lại chính Setup trước khi bắt đầu cài](https://jrsoftware.org/ishelp/topic_isxfunc_shellexec.htm). Nhánh bootstrap không copy payload hoặc tạo uninstaller. Helper phải xác thực PID/token/session và ảnh thực thi của tiến trình ban đầu trước khi ghi owner policy.

Compiler thành công và payload có DLL không chứng minh được service chạy dưới LocalService. Phải kiểm tra riêng cài/gỡ với UAC tài khoản khác, boot trước đăng nhập, đăng nhập lại, quyền file/pipe, và Meet/Zoom trên máy đích. Không tự reboot, logoff hoặc dừng camera đang sử dụng để kiểm thử bộ cài.
