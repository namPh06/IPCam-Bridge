# IP Camera Bridge

## Bản Windows Service

Bộ cài: `dist-service\IPCameraBridge-Setup.exe`. Mở bình thường bằng tài khoản sẽ dùng camera; bộ cài tự yêu cầu quyền quản trị và cho phép cài kèm OBS Studio nếu máy chưa có. Xem [hướng dẫn bộ cài](docs/SERVICE_PACKAGING.md) và [kết quả kiểm thử](docs/TEST_REPORT.md).

- Service nhận camera ngay khi Windows khởi động. Bộ xuất webcam chạy ẩn sau khi tài khoản sở hữu đăng nhập; không tự mở cửa sổ quản lý.
- Mở **IP Camera Bridge** từ Start, thêm camera, chọn **Tự kết nối và bật webcam** rồi lưu. Có thể nhập cấu hình desktop cũ bằng nút chuyển cấu hình; chỉ tắt startup desktop sau khi service xác nhận lưu thành công.
- Nút **Quét LAN** dò camera hỗ trợ ONVIF WS-Discovery. Chọn camera cần thêm, hoàn thiện đường dẫn RTSP do hãng cung cấp, nhập tài khoản/mật khẩu rồi lưu.
- Đóng cửa sổ chỉ ẩn giao diện. Menu khay cho phép bật/dừng webcam hoặc thoát bộ xuất; service vẫn nhận nguồn. Chọn **OBS Virtual Camera** trong Meet/Zoom và dùng micro riêng.
- Camera service lưu tại `%ProgramData%\IPCameraBridge\service\cameras.dat`, mã hóa bằng Windows DPAPI của tài khoản service và giới hạn quyền truy cập. Không sao chép file này sang máy khác để dùng lại mật khẩu.
- Tối đa 16 nguồn, một camera xuất mỗi lúc. Service thử lại khi mất mạng cho đến khi người dùng ngắt nguồn; không bị giới hạn tám lần như bản desktop.

Các mục dưới đây mô tả bản desktop cũ. Khi đã cài service, mở chương trình sẽ vào giao diện quản lý service.

Ứng dụng Windows đưa **Test Pattern**, **Video File** hoặc **RTSP** vào preview và **OBS Virtual Camera** để chọn trong Google Meet. Đầu ra mặc định **1280 × 720, 25 fps**; có tùy chọn **1920 × 1080, 25 fps**. Ảnh giữ đúng tỷ lệ, thêm viền đen khi cần.

Chỉ xử lý hình ảnh. Âm thanh trong file/RTSP được bỏ qua; chọn micro riêng trong Meet. Không gồm ghi hình, PTZ, AI hoặc driver webcam riêng.

## Chuẩn bị

- Windows 10 **1809 trở lên** hoặc Windows 11, x64.
- Chạy mã nguồn: **CPython 3.13 x64**, bản thông thường, đã thêm vào PATH (`python --version` phải là 3.13).
- Xuất webcam: cài riêng [OBS Studio cho Windows](https://obsproject.com/download), ưu tiên bộ cài hiện hành, OBS 30 trở lên. Không cần mở OBS để chạy bridge. Trước khi bật webcam trong bridge, **Stop Virtual Camera** trong OBS.
- Test Pattern, file và preview chạy được khi chưa có OBS; chỉ bước xuất webcam cần thiết bị OBS Virtual Camera.

Phiên bản thư viện đã khóa trong [requirements.txt](requirements.txt); nguồn kiểm tra chính thức ở [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Chạy mã nguồn

Mở PowerShell tại thư mục `IPCameraBridge`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\run.ps1
```

Có thể chạy trực tiếp sau khi cài thư viện:

```powershell
.\.venv\Scripts\python.exe -m ip_camera_bridge
```

## Sử dụng

1. Chọn **Test Pattern**, để **720p**, nhấn **Kết nối nguồn**. Preview cần có các vạch màu, thanh chuyển động, thời gian và số khung tăng.
2. Nhấn **Bắt đầu webcam ảo** sau khi nguồn có hình. Thiết bị xuất luôn có tên **OBS Virtual Camera**.
3. Trong Chrome, mở Google Meet, cho phép trang dùng camera. Ở màn hình trước cuộc họp hoặc **Tùy chọn khác → Cài đặt → Video**, chọn **OBS Virtual Camera**. Chọn micro riêng. [Hướng dẫn Meet](https://support.google.com/meet/answer/10409699?hl=vi)
4. Nếu đã chặn quyền camera: Chrome → **Cài đặt → Quyền riêng tư và bảo mật → Cài đặt trang web → Camera**, cho phép `meet.google.com`, rồi tải lại trang. Kiểm tra quyền camera của Windows nếu Chrome yêu cầu. [Hướng dẫn Chrome](https://support.google.com/chrome/answer/2693767?hl=vi)
5. Nhấn **Dừng webcam ảo** khi xong. Nhấn **Ngắt kết nối** để dừng cả nguồn và webcam, đồng thời hủy reconnect. Dừng cả hai trước khi đổi độ phân giải.

**Video File:** chọn nguồn, nhấn **Chọn file…**, chọn video rồi kết nối. File phát lặp theo thời gian gốc; đầu ra vẫn là 25 fps, có thể bỏ hoặc lặp khung để khớp nhịp. Không phát âm thanh.

**RTSP:** nhập URL do camera của bạn cung cấp, ví dụ minh họa `rtsp://camera.example:554/stream`, rồi nhập tài khoản ở hai ô riêng **Tên đăng nhập**, **Mật khẩu**. Ví dụ không phải camera có thể kết nối. Ứng dụng không có địa chỉ camera cố định, dùng RTSP qua TCP.

Cả ba ô URL, tên đăng nhập và mật khẩu đều được che. Mặc định chỉ giữ trong bộ nhớ; nếu muốn khôi phục sau khi mở lại, bật **Ghi nhớ camera (mã hóa Windows)** rồi **Lưu cài đặt**. Không đưa thông tin đăng nhập thật vào ảnh chụp hoặc báo cáo lỗi.

### Nhiều camera và tự mở cùng Windows

1. Bấm **Thêm camera**, đặt tên và nhập URL/tài khoản riêng cho từng camera. Vẫn có thể chọn Test Pattern hoặc Video File cho mỗi mục.
2. Bấm **Kết nối tất cả**. Chọn mục **Camera xuất** để xem trước và chuyển hình đưa vào webcam ảo. Các camera khác vẫn kết nối; đổi camera không khởi động lại thiết bị OBS Virtual Camera trong Meet.
3. Bật **Ghi nhớ camera (mã hóa Windows)**, **Tự kết nối và bật webcam**, **Mở cùng Windows**, rồi **Lưu cài đặt**. Camera đang chọn sẽ là camera xuất khi mở lại.
4. Giữ EXE ở vị trí cố định trước khi bật tự khởi động. Ứng dụng mở **sau khi đăng nhập Windows**, kết nối tất cả và bật webcam khi camera được chọn có hình. Không cần mở OBS. Nếu đổi chỗ EXE, mở từ vị trí mới rồi lưu lại cài đặt.
5. Muốn tắt tự khởi động: bỏ **Mở cùng Windows** và lưu. Muốn xóa camera đã lưu trên đĩa: bỏ **Ghi nhớ camera** và lưu.

Tối đa 16 mục camera; số luồng chạy mượt tùy CPU/mạng. Chỉ xuất **một camera tại một thời điểm**, không ghép lưới. Camera mất mạng không dừng các nguồn khác; chọn camera chưa có hình sẽ xuất ảnh trạng thái. Nếu mạng chưa sẵn sàng khi đăng nhập và hết lượt thử lại, bấm **Kết nối tất cả**. Đổi độ phân giải cần **Ngắt tất cả** trước.

## Mất kết nối và trạng thái

RTSP có timeout mở **5 giây**, đọc **3 giây**. Sau lỗi, ứng dụng thử kết nối lại tối đa **8 lần** ngoài lần đầu, chờ lần lượt **1, 2, 4, 8, 15, 15, 15, 15 giây**. Sau **10 giây** nhận hình ổn định, bộ đếm lỗi được đặt lại. Hết lượt thì kiểm tra nguồn và nhấn kết nối lại. Ngắt kết nối hủy lần chờ thử lại.

Khi không nhận khung mới trong **5 giây**, preview và đầu ra đang hoạt động chuyển sang ảnh thông báo mất kết nối. Trạng thái lỗi đọc nguồn có thể đưa ảnh thông báo lên sớm hơn. Tốc độ xử lý hiển thị là số khung nguồn được cập nhật, có thể khác nhịp xuất 25 fps.

## Cấu hình

Ứng dụng lưu tùy chọn khi đóng tại:

```text
%LOCALAPPDATA%\IPCameraBridge\config.json
```

JSON chỉ có ba trường: `source_kind`, `file_path`, `resolution`. Xem [config.example.json](config.example.json). **Không chứa URL RTSP, username hoặc password**. Khi bật Ghi nhớ camera, danh sách và tài khoản nằm trong `cameras.dat` cùng thư mục, được mã hóa bằng Windows DPAPI cho tài khoản Windows hiện tại. Không sao chép file này sang máy khác để dùng lại mật khẩu; nhập lại trên máy nhận. [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

Tự khởi động dùng mục `IPCameraBridge` trong `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; chỉ chứa lệnh mở ứng dụng, không có tài khoản camera. Đây là mở sau đăng nhập, không phải dịch vụ trước đăng nhập. [Microsoft Run keys](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys)

Log trạng thái ở `%LOCALAPPDATA%\IPCameraBridge\bridge.log`. Khi báo lỗi, gửi trạng thái và bước tái hiện; kiểm tra lại nội dung trước khi chia sẻ log.

## Đóng gói Windows

Bản nhiều camera tạo trong phiên cập nhật này nằm tại **`dist-multicam\IPCameraBridge.exe`**, do bản cũ ở `dist` đang chạy và bị Windows khóa. Đóng bản cũ rồi mở bản này. Lệnh build mặc định bên dưới vẫn xuất vào `dist`.

Từ thư mục dự án, sau bước tạo `.venv`:

```powershell
.\build.ps1
```

Build dùng PyInstaller dạng **onefile**, trên Windows x64 với Python 3.13. Chạy:

```text
dist\IPCameraBridge.exe
```

Phân phối **một file `dist\IPCameraBridge.exe`**. Máy nhận Windows x64 không cần Python hay thư mục `_internal` đi kèm, nhưng vẫn cần cài OBS Virtual Camera riêng. Khi mở, ứng dụng tự giải nén thư viện vào thư mục tạm nên khởi động có thể chậm hơn bản thư mục. Không có bước tự cài hoặc đăng ký driver. Thư mục `dist\IPCameraBridge` còn lại từ lần build cũ không phải bản một file mới.

## Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Preview có hình, không bật được webcam | Cài OBS bằng bộ cài Windows; dừng Virtual Camera trong OBS và dừng phiên bridge khác đang xuất. |
| Không có OBS Virtual Camera trong Meet | Kiểm tra cài OBS, quyền camera của Chrome; đóng rồi mở lại Chrome sau khi cài thiết bị. |
| OBS đã cài nhưng thiết bị chưa đăng ký | Làm theo [OBS troubleshooting](https://obsproject.com/kb/virtual-camera-troubleshooting): trong thư mục OBS `data\obs-plugins\win-dshow`, chạy `virtualcam-install.bat` bằng **Run as Administrator**. |
| Webcam báo không mở được/đang được dùng | Dừng mọi chương trình đang phát vào OBS Virtual Camera. OBS chỉ cung cấp một đầu ra chung; bridge và OBS không thể cùng phát vào đó. Lỗi khởi tạo cũng có thể do backend chưa sẵn sàng. |
| RTSP không có hình | Kiểm tra URL/path/port, tài khoản, mạng và RTSP của camera. Dùng ô tài khoản riêng; không gửi URL có bí mật trong báo cáo lỗi. |
| File báo không đọc được | Chọn file có luồng video giải mã được, kiểm tra file có tồn tại. File chỉ âm thanh không phải nguồn video. |

OBS có thể nhận OBS Virtual Camera của bridge làm nguồn hình; khi đó không bật lại **Start Virtual Camera** trong OBS. Nếu cần trộn hình rồi xuất từ OBS, cần một backend khác ở đầu bridge; luồng đó ngoài phạm vi MVP này. [Giới hạn của pyvirtualcam](https://github.com/letmaik/pyvirtualcam#supported-virtual-cameras)

## Kiểm tra

Kết quả thực chạy và các phần chưa nghiệm thu: [TEST_REPORT.md](docs/TEST_REPORT.md).

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:QT_QPA_PLATFORM
```

Test tự động không thay thế nghiệm thu camera thật, OBS trên máy nhận hoặc cuộc gọi. Dùng [checklist kiểm tra thủ công](docs/MANUAL_TESTS.md) cho Test Pattern, file, RTSP, Google Meet và Zalo. **Chưa coi camera thật, Google Meet hoặc Zalo là đã nghiệm thu nếu chưa có kết quả thực tế được ghi lại.**
