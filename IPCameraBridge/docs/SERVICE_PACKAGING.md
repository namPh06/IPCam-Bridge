# Đóng gói bản Windows Service

## Bản 0.3.10

- App đặt định danh taskbar `IPCameraBridge.Desktop` trước khi tạo giao diện, dùng chung với shortcut Start Menu/Desktop. Shortcut trỏ trực tiếp tới icon đã đóng gói.
- Bộ cài phát hành là `dist-service/IPCameraBridge-Setup-0.3.10.exe`; tên có phiên bản giúp tránh dùng lại biểu tượng Explorer đã lưu cho file cũ. Build vẫn tạo bản sao tên `IPCameraBridge-Setup.exe` để giữ các đường dẫn tải hiện có.
- Sau khi cài lại, thoát bộ xuất đang chạy qua menu khay hệ thống và mở app mới. Nếu đã ghim shortcut cũ trên taskbar, bỏ ghim và ghim lại shortcut mới.

## Bản 0.3.9

- Bộ cài dùng tiếng Anh, giữ một cửa sổ tiến trình và tùy chọn OBS hiện có.
- Logo nằm tại `ip_camera_bridge/assets/app.ico`, được nhúng vào EXE, cửa sổ ứng dụng, khay hệ thống và bộ cài; `wizard-logo.bmp` hiển thị trong wizard.
- Quét LAN chỉ dùng dải IP nhập thủ công. Mật khẩu đã lưu hiện dấu chấm và không được đọc lại về giao diện.
- Chạy `build.ps1 -Service` để tạo `dist-service/IPCameraBridge-Setup.exe` và file SHA256 bên cạnh.

## Bản 0.3.8

- Camera RTSP chưa nhập URL được lưu như mục chưa hoàn thiện, không chặn kết nối/phát camera đã cấu hình.
- Service bỏ qua camera trống ở cùng đường xử lý kết nối thủ công và tự kết nối sau khởi động; không tạo decoder hoặc lặp retry cho camera trống.
- Camera đang chọn để phát phải có URL hợp lệ; URL sai báo đúng camera và mở cấu hình của camera đó. URL không trống vẫn được kiểm tra ở ranh giới service.

## Bản 0.3.7

- Danh sách camera dùng biểu tượng và trạng thái màu; chọn dòng rồi bấm Kết nối camera để phát.
- Khung thông báo hiện tại thay lịch sử và footer: đỏ khi lỗi, xanh khi thành công, trung tính khi đang xử lý. Log kỹ thuật vẫn được giữ trong file.
- Bỏ chú thích thanh tiến trình cài đặt và đoạn hướng dẫn dài trong dialog quét LAN; không đổi giới hạn quét hoặc tiến trình cài đặt.

## Bản 0.3.6

- Dashboard mới: danh sách camera và tìm kiếm bên trái; cấu hình hai cột, lỗi và hướng khắc phục bên phải; trạng thái đầu ra nằm riêng phía dưới.
- Chọn dòng để sửa; checkbox chọn nguồn phát. Kiểm tra RTSP kết nối camera đang sửa mà không đổi camera đầu ra. Tùy chọn lưu vẫn áp dụng cho cấu hình kiểm tra.
- Nút hiện/ẩn chỉ hiển thị mật khẩu vừa nhập; mật khẩu đã lưu không được trả về giao diện. Tùy chọn nâng cao và tác vụ phụ được thu gọn.
- Dùng widget Qt và QtSvg có sẵn; không thêm dependency. Kích thước tối thiểu 960×640, vùng nội dung cuộn khi thiếu chiều cao.

## Bản 0.3.5

- Quét LAN bằng hai giới hạn IPv4 bắt đầu/kết thúc, bao gồm hai đầu và tối đa 1024 địa chỉ. Chỉ kiểm tra RTSP cổng 554 trong dải nhập; không gửi ONVIF multicast. Chọn card mạng chỉ gợi ý dải, Windows vẫn định tuyến kết nối TCP.
- Danh sách camera có checkbox: tick camera để kết nối/phát, tick camera khác để chuyển nguồn, bỏ tick để dừng webcam. Chọn dòng chỉ mở thông tin để sửa; không đổi nguồn.
- Trạng thái phân biệt nguồn đã kết nối với webcam đang phát, không giữ trạng thái thành công cũ khi mất dịch vụ. Giao diện sáng dùng Fusion và font Segoe UI thống nhất.
- Lịch sử sự cố nằm trong phần trạng thái, nêu nguyên nhân và hướng kiểm tra. Mã chẩn đoán giữ trong `service.log` (kèm ID camera) và `session.log`; không ghi thông tin đăng nhập.

## Bản 0.3.4

- Chỉ cửa sổ cài chính hiển thị. Tiến trình quản trị chạy với `/VERYSILENT`; lựa chọn cài OBS được chuyển từ cửa sổ chính. Hộp thoại UAC vẫn hiện khi cần quyền quản trị.
- Checkbox **Lưu cấu hình** mặc định bật: bấm **Kết nối và sử dụng** sẽ lưu cấu hình bằng DPAPI. Bỏ chọn chỉ áp dụng tạm đến khi service khởi động lại, không xóa cấu hình đã lưu trước đó. Tự chạy Windows yêu cầu bật lưu.
- Nhập IP hoặc IP:cổng sẽ tạo URL theo **Mẫu đường dẫn**, mặc định i-PRO `/Src/MediaInput/stream_1`. URL RTSP đầy đủ được giữ nguyên. Camera khác có thể cần mẫu khác; đây không phải tự nhận diện model hay bảo đảm đường dẫn đúng.
- Quét ONVIF trên mọi card mạng đang hoạt động hoặc card được chọn; quét RTSP tùy chọn theo dải IPv4 tối đa 1024 địa chỉ, cổng 554, có tiến độ và nút dừng. Thiết bị phải phản hồi giao thức RTSP mới được đưa vào kết quả. Khác VLAN vẫn cần routing/firewall cho phép; ứng dụng không thay đổi thiết lập mạng.
- Màu chữ được đặt rõ ràng để giao diện sáng không mất chữ khi Windows dùng palette tối.

Máy build cần Windows x64, Python 3.13 x64 cùng các dependency đã khóa, [Inno Setup 6.7.3 trở lên](https://jrsoftware.org/isdl.php), và bộ cài OBS Studio 32.2.2 x64 chính thức. Máy nhận không cần Python.

```powershell
.\run.ps1 -Setup
.\build.ps1 -Service -OBSInstallerPath 'C:\path\OBS-Studio-32.2.2-Windows-x64-Installer.exe'
```

Nếu compiler hoặc venv nằm ở vị trí khác:

```powershell
.\build.ps1 -Service -PythonPath 'C:\path\venv\Scripts\python.exe' -ISCCPath 'C:\path\Inno Setup 6\ISCC.exe'
```

Build kiểm tra SHA-256 và chữ ký số OBS Project trước khi chạy toàn bộ unittest. Payload nằm trong `dist-service\IPCameraBridge\`; bản gửi sang máy khác là **`dist-service\IPCameraBridge-Setup.exe`** và file `.sha256` để đối chiếu. Không chạy EXE trong thư mục `build-service`, không tách EXE khỏi `_internal` của payload.

Nếu thiếu Inno, payload vẫn được tạo nhưng lệnh build dừng với hướng dẫn cài compiler; chưa có bộ cài EXE. Build không tự tải hoặc chạy chương trình cài dependency. Lệnh `build.ps1` không có `-Service` vẫn tạo bản desktop một file như trước.

## Cài đặt và gỡ

Mở bộ cài bình thường trong tài khoản Windows sẽ dùng camera; **không chọn Run as administrator**. Bộ cài tự yêu cầu UAC. Có thể nhập tài khoản quản trị khác; tài khoản sở hữu camera vẫn lấy từ tiến trình mở bộ cài ban đầu. Không truyền mật khẩu Windows hoặc camera trên dòng lệnh.

Payload cài cố định tại `%ProgramFiles%\IPCameraBridge`. Helper đăng ký service `IPCameraBridgeCapture` và tác vụ đăng nhập `IPCameraBridgePublisher`; cấu hình service nằm tại `%ProgramData%\IPCameraBridge\service\cameras.dat`. Nếu máy chưa có OBS, trang **Thành phần tùy chọn** cho phép cài bản OBS chính thức đã kèm trong Setup. OBS không cần mở để bridge hoạt động.

Nếu đã có thư mục cài đặt, bộ cài yêu cầu gỡ bản cũ và giữ dữ liệu rồi cài lại; không ghi đè ứng dụng đang chạy. Thư mục dữ liệu có owner không tin cậy hoặc directory link bị từ chối để tránh sửa quyền nhầm thư mục của người khác.

Bootstrap không nâng quyền dùng thư mục tạm và không chép payload. Bản nâng quyền dùng thư mục Program Files cố định. Cần giữ `CreateAppDir=yes`: [Inno quy định `CreateAppDir=no` chuyển `{app}` thành thư mục Windows](https://jrsoftware.org/ishelp/topic_setup_createappdir.htm), nên không dùng nó để tắt ghi file ở bootstrap.

Gỡ bằng **Settings → Apps → Installed apps → IP Camera Bridge → Uninstall**. Mặc định giữ cấu hình camera. Chỉ chọn xóa dữ liệu khi muốn xóa các camera và mật khẩu đã lưu. Bộ gỡ dừng task, publisher và service trước khi xóa chương trình; OBS là thành phần độc lập nên không bị gỡ.

Hiện chưa hỗ trợ ghi đè bản service đang cài. Bộ cài từ chối trước khi thay file: gỡ bản cũ, chọn giữ dữ liệu, rồi cài bản mới bằng cùng tài khoản. Giữ lại bộ cài cũ để quay lại phiên bản trước. Không sao chép ciphertext sang máy Windows khác.

## Luồng UAC và giới hạn kiểm chứng

Bootstrap dùng `PrivilegesRequired=lowest` và `/ALLUSERS`; tiến trình ban đầu giữ nguyên token và chờ bộ cài nâng quyền hoàn thành. Lệnh relaunch ở `ssInstall` vì [Inno không cho ShellExec chạy lại chính Setup trước khi bắt đầu cài](https://jrsoftware.org/ishelp/topic_isxfunc_shellexec.htm). Nhánh bootstrap không copy payload hoặc tạo uninstaller. Helper phải xác thực PID/token/session và ảnh thực thi của tiến trình ban đầu trước khi ghi owner policy.

Compiler thành công và payload có DLL không chứng minh được service chạy dưới LocalService. Phải kiểm tra riêng cài/gỡ với UAC tài khoản khác, boot trước đăng nhập, đăng nhập lại, quyền file/pipe, và Meet/Zoom trên máy đích. Không tự reboot, logoff hoặc dừng camera đang sử dụng để kiểm thử bộ cài.
