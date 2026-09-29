# IP Camera Bridge

> Biến camera IP/RTSP thành **OBS Virtual Camera** để sử dụng trong Google Meet, Zoom và các ứng dụng họp trên Windows.

![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows11&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.3.0-2E8B57)
![Tests](https://img.shields.io/badge/tests-77%20passed-brightgreen)

IP Camera Bridge nhận hình ảnh từ RTSP, video hoặc Test Pattern, hiển thị preview và xuất một camera đã chọn qua thiết bị **OBS Virtual Camera**. Ứng dụng hỗ trợ tối đa 16 nguồn, tự kết nối lại khi mất mạng và có thể hoạt động nền cùng Windows.

## Mục lục

- [Tính năng](#tính-năng)
- [Cách hoạt động](#cách-hoạt-động)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Thiết lập lần đầu](#thiết-lập-lần-đầu)
- [Quét camera trong LAN](#quét-camera-trong-lan)
- [Thêm camera RTSP thủ công](#thêm-camera-rtsp-thủ-công)
- [Sử dụng nhiều camera](#sử-dụng-nhiều-camera)
- [Sử dụng trong Google Meet và Zoom](#sử-dụng-trong-google-meet-và-zoom)
- [Chạy cùng Windows](#chạy-cùng-windows)
- [Bảo mật và dữ liệu](#bảo-mật-và-dữ-liệu)
- [Xử lý lỗi](#xử-lý-lỗi)
- [Chạy từ mã nguồn](#chạy-từ-mã-nguồn)
- [Đóng gói](#đóng-gói)

## Tính năng

| Chức năng | Mô tả |
|---|---|
| Nguồn RTSP | Nhận H.264/H.265 và các luồng video được FFmpeg/PyAV hỗ trợ qua RTSP TCP. |
| Quét LAN | Tìm camera hỗ trợ ONVIF WS-Discovery trong cùng mạng LAN. |
| Nhiều camera | Lưu tối đa 16 nguồn và chọn một camera để xuất tại mỗi thời điểm. |
| Webcam ảo | Xuất hình qua **OBS Virtual Camera** cho Meet, Zoom và ứng dụng tương thích webcam Windows. |
| Windows Service | Tự nhận camera khi Windows khởi động và tự bật webcam sau khi người dùng đăng nhập. |
| Tự phục hồi | Liên tục thử kết nối lại khi camera hoặc mạng bị gián đoạn. |
| Bảo vệ mật khẩu | Mã hóa cấu hình camera bằng Windows DPAPI; không ghi mật khẩu vào log. |
| Test Pattern | Kiểm tra preview và webcam ảo khi không truy cập được mạng camera. |
| Video File | Dùng file video làm nguồn thử nghiệm, phát lặp và không phát âm thanh. |

> [!NOTE]
> Ứng dụng chỉ xử lý **hình ảnh**. Hãy chọn microphone riêng trong phần cài đặt âm thanh của ứng dụng họp.

## Cách hoạt động

```mermaid
flowchart LR
    A[Camera IP / RTSP] --> B[IP Camera Bridge Service]
    T[Test Pattern / Video] --> B
    B --> C[Preview]
    B --> D[OBS Virtual Camera]
    D --> E[Google Meet]
    D --> F[Zoom]
    D --> G[Ứng dụng họp khác]
```

OBS cung cấp driver webcam ảo. IP Camera Bridge gửi trực tiếp hình ảnh vào driver đó, vì vậy **không cần mở OBS Studio** và không cần bấm **Start Virtual Camera** trong OBS.

## Yêu cầu hệ thống

- Windows 10 phiên bản 1809 trở lên hoặc Windows 11, x64.
- Máy tính và camera nằm trong cùng mạng hoặc có tuyến mạng truy cập được camera.
- Cổng RTSP của camera, thường là TCP `554`, không bị firewall chặn.
- OBS Studio để cung cấp thiết bị **OBS Virtual Camera**.
- Quyền quản trị khi cài đặt hoặc gỡ Windows Service.

Máy sử dụng bộ cài không cần cài Python. Bộ cài có tùy chọn cài kèm OBS Studio nếu chưa phát hiện OBS trên máy.

## Cài đặt

### Cài mới

1. Nhận file `IPCameraBridge-Setup.exe` từ người phát hành.
2. Mở file theo cách bình thường, không chọn **Run as administrator**.
3. Chọn **Yes** khi Windows yêu cầu quyền quản trị.
4. Nếu máy chưa có OBS, giữ tùy chọn **Cài OBS Studio**.
5. Sau khi cài xong, mở **Start → IP Camera Bridge**.

> [!WARNING]
> Bộ cài hiện chưa có chữ ký số của nhà phát hành. Windows SmartScreen có thể yêu cầu chọn **More info → Run anyway**. Chỉ chạy file nhận từ nguồn bạn tin tưởng và có SHA-256 khớp với file `.sha256` đi kèm.

### Nâng cấp

Phiên bản hiện tại chưa ghi đè trực tiếp bản đang cài:

1. Mở **Settings → Apps → Installed apps**.
2. Gỡ **IP Camera Bridge**.
3. Khi được hỏi có xóa camera và mật khẩu hay không, chọn **No** để giữ dữ liệu.
4. Chạy bộ cài mới bằng cùng tài khoản Windows.

### Gỡ cài đặt

Gỡ ứng dụng trong **Settings → Apps → Installed apps**. OBS Studio là thành phần độc lập nên không bị gỡ cùng IP Camera Bridge.

## Thiết lập lần đầu

Nên kiểm tra bằng Test Pattern trước khi kết nối camera thật:

1. Mở **IP Camera Bridge** từ Start.
2. Chọn camera `Test Pattern 1`.
3. Bấm **Kết nối camera** và kiểm tra preview có hình chuyển động.
4. Bấm **Xuất camera này**.
5. Bấm **Bật webcam**.
6. Mở Google Meet hoặc Zoom và chọn **OBS Virtual Camera**.

Nếu Test Pattern hoạt động trong cuộc họp, đường xuất webcam đã sẵn sàng. Tiếp theo có thể quét LAN hoặc nhập RTSP thủ công.

## Quét camera trong LAN

Nút **Quét LAN** nằm trên hàng đầu, giữa **Thêm** và **Xóa**.

1. Kết nối máy tính vào cùng mạng LAN/VLAN với camera.
2. Bấm **Quét LAN** và chờ khoảng vài giây.
3. Chọn một hoặc nhiều camera ONVIF trong danh sách.
4. Bấm **OK** để thêm các camera đã chọn.
5. Chọn từng camera và hoàn thiện trường **URL RTSP / file**.
6. Chọn nguồn **RTSP**, nhập tên đăng nhập và mật khẩu.
7. Bấm **Lưu cấu hình**.
8. Bấm **Kết nối camera** để kiểm tra preview.

WS-Discovery thường chỉ xác định được thiết bị và địa chỉ IP. Đường dẫn stream RTSP phụ thuộc hãng camera nên vẫn cần kiểm tra tài liệu của camera. Ví dụ:

```text
rtsp://192.168.1.100:554/Src/MediaInput/stream_1
```

Nếu không tìm thấy camera:

- Kiểm tra camera đã bật ONVIF hoặc WS-Discovery.
- Kiểm tra máy và camera có cùng LAN/VLAN hay không.
- Cho phép ứng dụng qua Windows Firewall trên mạng Private.
- Thêm camera thủ công nếu thiết bị không hỗ trợ ONVIF discovery.

## Thêm camera RTSP thủ công

1. Bấm **Thêm**.
2. Đặt **Tên camera** dễ nhận biết.
3. Trong **Nguồn**, chọn `RTSP`.
4. Nhập URL không chứa thông tin đăng nhập:

   ```text
   rtsp://192.168.1.100:554/duong-dan-stream
   ```

5. Nhập **Tên đăng nhập** và **Mật khẩu** vào hai ô riêng.
6. Chọn `720p` hoặc `1080p` tại **Đầu ra 25 fps**.
7. Bấm **Lưu cấu hình**, sau đó bấm **Kết nối camera**.

Nhập mật khẩu ở dạng nguyên bản. Ví dụ, nếu mật khẩu chứa `@`, hãy nhập ký tự `@`; không đổi thành `%40`. Ứng dụng tự mã hóa ký tự đặc biệt đúng một lần khi tạo URL nội bộ.

> [!CAUTION]
> Không chụp màn hình, gửi log hoặc đăng issue có URL chứa tài khoản/mật khẩu thật.

## Sử dụng nhiều camera

- Bấm **Thêm** hoặc **Quét LAN** để tạo tối đa 16 camera.
- Bấm **Kết nối tất cả** để nhận đồng thời các nguồn đã lưu.
- Chọn camera trong danh sách rồi bấm **Xuất camera này** để chuyển hình đưa vào webcam ảo.
- Google Meet/Zoom vẫn sử dụng cùng một thiết bị **OBS Virtual Camera** khi chuyển camera.
- Bấm **Ngắt camera** để dừng nguồn đang chọn hoặc **Ngắt tất cả** để dừng toàn bộ nguồn.

Ứng dụng xuất một camera tại một thời điểm. Các camera còn lại có thể tiếp tục kết nối để chuyển nguồn nhanh, tùy khả năng CPU và mạng của máy.

## Sử dụng trong Google Meet và Zoom

### Google Meet

1. Bật webcam trong IP Camera Bridge.
2. Mở Google Meet.
3. Chọn **Tùy chọn khác → Cài đặt → Video**.
4. Tại **Máy ảnh**, chọn `OBS Virtual Camera`.
5. Chọn microphone thật trong mục **Âm thanh**.

Nếu vừa cài OBS khi Chrome đang mở, hãy đóng toàn bộ cửa sổ Chrome rồi mở lại.

### Zoom

1. Bật webcam trong IP Camera Bridge.
2. Mở **Zoom → Settings → Video**.
3. Chọn `OBS Virtual Camera` trong danh sách Camera.
4. Chọn microphone riêng trong **Audio**.

Một số ứng dụng chỉ đọc danh sách camera lúc khởi động. Nếu chưa thấy OBS Virtual Camera, hãy đóng và mở lại ứng dụng họp.

## Chạy cùng Windows

Đánh dấu:

```text
Tự kết nối camera khi Windows khởi động; tự bật webcam sau đăng nhập
```

Sau đó bấm **Lưu cấu hình**.

- Service `IPCameraBridgeCapture` tự chạy từ lúc Windows khởi động.
- Camera được kết nối nền theo cấu hình đã lưu.
- Sau khi tài khoản sở hữu đăng nhập, tác vụ `IPCameraBridgePublisher` tự bật OBS Virtual Camera.
- Cửa sổ quản lý không tự bật lên.
- Đóng cửa sổ quản lý không dừng service hoặc webcam.

Biểu tượng ở khay hệ thống cho phép mở quản lý, bật/dừng webcam hoặc thoát bộ xuất. Thoát bộ xuất không dừng service nhận camera.

## Bảo mật và dữ liệu

| Dữ liệu | Vị trí / cách bảo vệ |
|---|---|
| Camera và thông tin đăng nhập | `%ProgramData%\IPCameraBridge\service\cameras.dat` |
| Mã hóa | Windows DPAPI, gắn với tài khoản Windows sở hữu cấu hình |
| Chương trình | `%ProgramFiles%\IPCameraBridge` |
| Log service | Trong thư mục dữ liệu được giới hạn quyền truy cập |

- Mật khẩu không được trả ngược về giao diện sau khi lưu.
- Mật khẩu và userinfo trong URL được che khỏi log.
- Không hardcode thông tin camera trong mã nguồn.
- Không sao chép `cameras.dat` sang máy khác để dùng lại mật khẩu; hãy nhập lại trên máy nhận.
- Gỡ và cài lại bằng cùng tài khoản Windows nếu muốn giữ cấu hình cũ.

## Xử lý lỗi

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Không có nút **Quét LAN** | Đang chạy bản cũ | Gỡ bản cũ nhưng giữ dữ liệu, sau đó cài bản 0.3.0 trở lên. |
| Quét LAN không thấy camera | ONVIF discovery bị tắt, khác VLAN hoặc firewall chặn multicast | Bật ONVIF/WS-Discovery, kiểm tra mạng Private và thử nhập RTSP thủ công. |
| Camera từ chối xác thực `401` | Sai tài khoản, mật khẩu hoặc quyền stream | Kiểm tra lại bằng VLC; nhập URL, username và mật khẩu vào ba ô riêng. |
| Mật khẩu có `@` không kết nối | Mật khẩu đã được mã hóa thủ công | Nhập `@` nguyên bản, không nhập `%40`. |
| VLC chạy nhưng ứng dụng không chạy | Sai URL/path trong ứng dụng hoặc đang dùng bản/cấu hình cũ | Sao chép đúng URL đã thử trong VLC, lưu lại rồi kết nối lại. |
| Preview có hình nhưng không bật webcam | Thiếu OBS Virtual Camera hoặc thiết bị đang bị OBS/phiên bridge khác giữ | Cài OBS, dừng Virtual Camera trong OBS và đóng phiên bridge khác. |
| Meet/Zoom không thấy OBS Virtual Camera | Ứng dụng họp đã mở trước khi driver được cài | Đóng hoàn toàn ứng dụng họp/trình duyệt rồi mở lại. |
| Camera mất mạng | Mạng hoặc nguồn camera gián đoạn | Service tự thử lại; kiểm tra dây/mạng và theo dõi trạng thái trong giao diện. |
| Hình có viền đen | Tỷ lệ camera khác tỷ lệ đầu ra | Đây là hành vi bình thường để giữ đúng tỷ lệ, không kéo méo hình. |

Để tách lỗi camera và lỗi webcam ảo, hãy thử theo thứ tự:

1. Test Pattern có preview.
2. Test Pattern xuất được sang OBS Virtual Camera.
3. RTSP có preview.
4. RTSP xuất được vào ứng dụng họp.

## Chạy từ mã nguồn

Yêu cầu CPython 3.13 x64 đã được thêm vào `PATH`.

```powershell
cd C:\Users\ADMIN\Documents\Tools\IPCameraBridge
.\run.ps1 -Setup   # lần đầu: tạo .venv và cài thư viện
.\run.ps1          # các lần sau
```

Nếu PowerShell chặn script:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Chạy test:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Đóng gói

Máy build cần Python 3.13 x64, Inno Setup 6 và bộ cài OBS Studio đúng phiên bản/hash đã khai báo trong `build.ps1`.

```powershell
.\run.ps1 -Setup
.\build.ps1 -Service -OBSInstallerPath 'C:\path\OBS-Studio-32.2.2-Windows-x64-Installer.exe'
```

Kết quả gửi cho người dùng:

```text
dist-service\IPCameraBridge-Setup.exe
dist-service\IPCameraBridge-Setup.exe.sha256
```

File OBS tải về được giữ ngoài Git. Script build kiểm tra SHA-256 và chữ ký số của OBS trước khi tạo bộ cài.

## Giới hạn hiện tại

- Chỉ xuất một camera tại một thời điểm, không ghép lưới nhiều camera.
- Không truyền âm thanh từ RTSP hoặc video file.
- Không điều khiển PTZ, không ghi hình và không có phân tích AI.
- Quét LAN chỉ phát hiện thiết bị hỗ trợ ONVIF WS-Discovery.
- Bộ cài IP Camera Bridge chưa được ký bằng chứng thư phát hành phần mềm.

## Tài liệu kỹ thuật

- [Đóng gói Windows Service](IPCameraBridge/docs/SERVICE_PACKAGING.md)
- [Checklist kiểm thử thủ công](IPCameraBridge/docs/MANUAL_TESTS.md)
- [Kiểm thử camera thật](IPCameraBridge/docs/REAL_CAMERA_TEST.md)
- [Kết quả kiểm thử](IPCameraBridge/docs/TEST_REPORT.md)
- [Danh sách dependency](IPCameraBridge/docs/DEPENDENCIES.md)

---

Nếu gặp lỗi, hãy gửi trạng thái hiển thị trong ứng dụng, phiên bản Windows/OBS và các bước tái hiện. Luôn che URL, username và password trước khi chia sẻ.
