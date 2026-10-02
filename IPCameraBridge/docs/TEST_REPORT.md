# Báo cáo kiểm thử IP Camera Bridge

## Biểu tượng Windows — 02/10/2026 (0.3.10)

- 90/90 unittest đạt trong 12,939 giây. Test gọi API Windows thật và đọc lại AppUserModelID, xác nhận trùng định danh trong hai shortcut của bộ cài.
- App đặt định danh trước cả hai luồng giao diện; service/install/check-service vẫn trả về trước bước này. Shortcut chỉ rõ file ICO thay vì phụ thuộc biểu tượng EXE đã lưu đệm.
- Tài nguyên icon trong EXE 0.3.10 được trích xuất và so sánh đủ sáu kích thước, khớp byte với `app.ico`. Icon trong bộ cài 0.3.9 cũng khớp; biểu tượng cũ trong ảnh Explorer có thể là bộ nhớ đệm đường dẫn đã dùng lại.
- Build xuất tên bộ cài có phiên bản để dùng đường dẫn mới; vẫn giữ bản sao tên cũ cho tương thích.
- Build PyInstaller/Inno thành công: `IPCameraBridge-Setup-0.3.10.exe`, 248.881.012 byte, SHA256 `DCA49ECA5E2D9703E9E6D1EC35F573399DF687115AE3F57616DC935AE5557E28`.
- Smoke EXE 0.3.10: hai Test Pattern kết nối, RGB 1280×720, 25,26 fps, Qt phản hồi, exit 0. Chưa cài lại hoặc xác nhận bằng mắt taskbar/pinned shortcut của máy đang chạy; cần thoát bộ xuất cũ và mở app mới.

## Giao diện và bộ cài — 02/10/2026 (0.3.9)

- 89/89 unittest đạt trong 11,789 giây. Test kiểm tra dialog quét chỉ có IP nhập thủ công, từ chối dải đảo ngược và truyền đúng giới hạn tới worker.
- Test xác nhận mật khẩu đã lưu hiển thị dấu chấm mà giá trị nhập vẫn rỗng; cấu hình gửi giữ `password=None`, không gửi dấu chấm hoặc trường `has_password`. Xóa mật khẩu bỏ dấu chấm. Địa chỉ đã che vẫn được giữ và cho phép thay mới.
- Đã bỏ Tùy chọn nâng cao; toàn bộ chữ tùy chỉnh trong bộ cài chuyển sang tiếng Anh. Logo người dùng cung cấp được chuyển thành icon nhiều kích thước, nhúng trong ứng dụng và bộ cài.
- Đã render và kiểm tra giao diện Qt với font Segoe UI. Smoke test EXE: hai Test Pattern kết nối, ảnh RGB 1280×720, 24,17 fps, Qt phản hồi, exit 0.
- Build PyInstaller/Inno thành công; đã xác nhận icon trong tài nguyên EXE/app và bộ cài, cùng asset logo trong payload. SHA256 bộ cài: `E25FF1FA9259514F81429E58C6060798F0B76046A6DCFCA6D46185062A447C51`.
- Chưa chạy cài đặt trên máy khác hoặc xác nhận camera thật/Meet/Zoom sau khởi động Windows ở bản này. Smoke test không bật webcam ảo.

## Camera chưa nhập URL — 02/10/2026 (0.3.8)

- 89/89 unittest đạt, 11,778 giây. Test mới xác nhận camera RTSP trống không chặn cấu hình/kết nối camera khác; chọn camera trống vẫn bị từ chối trước khi đổi đầu ra.
- Test runtime xác nhận lưu và tải lại mục trống, bỏ qua khi tự kết nối/kết nối tất cả, không tạo decoder hoặc giữ wanted/retry cho mục trống. URL sai không trống vẫn bị service từ chối.
- Lần build đầu bị chặn do test publisher dùng khóa webcam đang được app thật sử dụng. Test đã dùng tên khóa riêng; khóa publisher mặc định trong sản phẩm giữ nguyên.
- Build PyInstaller/Inno thành công. Bộ cài 248.551.892 byte; SHA256 `31134564576E53FB1B07063A99B782187F745422A960DEA38E4815AB84BB2CC8`.
- Smoke EXE hai Test Pattern connected, RGB 1280×720, 25,28 fps, Qt phản hồi, thoát mã 0. Không bật webcam hoặc thay đổi service đang cài.
- Chưa cài lại 0.3.8 hoặc nghiệm thu RTSP/Meet/Zoom/khởi động Windows trên máy đích; cần cập nhật bộ cài để service dùng bản sửa.

## Thông báo và danh sách camera — 02/10/2026 (0.3.7)

- 87/87 unittest đạt, 9,508 giây. Test xác nhận danh sách không có checkbox, chọn dòng độc lập camera đầu ra, nút kết nối đổi nguồn và nút dừng kết thúc phát; thông báo lỗi đỏ/thành công xanh.
- Đã xem ảnh Qt offscreen: camera đang phát xanh, camera lỗi đỏ, dòng được chọn giữ màu chữ đọc được. Thông báo hiện tại nằm trong thẻ đầu ra; bỏ lịch sử và footer cũ.
- Build service/Inno Setup mã 0. Bộ cài 248.559.046 byte, SHA256 `428E50E118ADCB9F9E560C28B56CCDF4FFB3D2E0DD750144C9D525F336E2C3F7`.
- Smoke EXE hai Test Pattern connected, RGB 1280×720, 25,71 fps, Qt phản hồi, thoát mã 0. Không bật webcam hoặc thay đổi service đang cài.
- Chưa cài bản 0.3.7 trên máy đích hoặc nghiệm thu RTSP thật/Meet/Zoom/khởi động Windows; xem checklist thủ công.

## Dashboard — 02/10/2026 (0.3.6)

- **87/87 unittest đạt**, 11,750 giây; build service và Inno Setup hoàn tất mã 0.
- Test hồi quy xác nhận tìm kiếm, hiện/ẩn mật khẩu, chọn dòng độc lập nguồn phát, kiểm tra RTSP giữ camera đầu ra, và lỗi camera đang sửa không làm sai trạng thái camera đang phát. Sửa địa chỉ mới bỏ lựa chọn giữ URL bí mật cũ.
- Đã xem ảnh Qt offscreen ở 1240×830 và 960×640: bố cục sidebar, form hai cột, thẻ lỗi và trạng thái đầu ra; nội dung dài cuộn được. Đây không thay thế kiểm tra DPI/Windows theme trên máy đích.
- Smoke EXE đóng gói: hai Test Pattern connected, RGB 1280×720, 23,97 fps, Qt phản hồi, thoát mã 0. Smoke không bật OBS, không cài lại hoặc thay đổi service đang cài.
- Bộ cài `dist-service/IPCameraBridge-Setup.exe`: 248.558.176 byte. SHA256: `C04B1D7FE1C23B8D442D2312603FA66559874E1BFA9CBCACAEDE095AFE6ED149`.
- Chưa nghiệm thu RTSP mạng công ty, Meet/Zoom, khởi động Windows hoặc cài bộ cài 0.3.6 trên máy đích; xem checklist thủ công.

## Giao diện và quét LAN có giới hạn — 01/10/2026 (0.3.5)

- **86/86 unittest đạt**, 9,601 giây; `pip check` không phát hiện dependency xung đột.
- Test quét xác nhận chỉ kết nối IP trong hai giới hạn (bao gồm hai đầu), nhận camera trả RTSP 401, từ chối dải đảo ngược/IPv6/quá lớn trước khi mở socket. Dialog kiểm tra dải sai không chạy worker; dải hợp lệ truyền đúng giới hạn xuống scanner. Socket được mock, không phải nghiệm thu LAN công ty.
- Qt offscreen kiểm tra chọn dòng không chuyển nguồn; Space/tick checkbox chuyển camera, chỉ một ô tick; bỏ tick dừng output. Lỗi 401 được thay bằng hướng dẫn kiểm tra, không đưa mã chẩn đoán vào trạng thái; refresh không nhân đôi lịch sử.
- Mất kết nối dịch vụ xóa trạng thái thành công cũ. Log service có ID camera; log session ghi lỗi dịch vụ/bộ phát theo thông báo cố định. Bộ test bảo vệ mật khẩu/DPAPI/ghi nguyên tử vẫn đạt.
- Đã xem ảnh giao diện quản lý và biểu mẫu cấu hình offscreen. Còn cần kiểm tra DPI thực, khởi động Windows, quét mạng công ty và chuyển hình trong Meet/Zoom trên máy đích.
- PyInstaller và Inno Setup biên dịch thành công bộ cài `dist-service/IPCameraBridge-Setup.exe` (248.500.238 byte). SHA256: `CE5C96D6CE889FFB7DA84C379DD1886A951CB052E0B88A0280FB3143E3FEBC1A`.
- Smoke chính EXE đóng gói với hai Test Pattern: cả hai `connected`, ảnh RGB 1280×720, 24,61 fps, Qt phản hồi, thoát mã 0. Phép thử không bật OBS và không thay đổi service đang cài; chưa cài lại bộ cài 0.3.5 trên máy đích.

## Windows Service — 29/09/2026

- Toàn bộ **76/76 unittest đạt**, 10,255 giây. Bao gồm Windows DPAPI với tài khoản chạy test, named pipe thật, frame RGB có giới hạn, từ chối JSON/frame lỗi, reconnect, revision/save nguyên tử, lifecycle worker và hồi phục khóa bị bỏ lại. Test native Job Object xác nhận host thoát dọn tiến trình con.
- Review độc lập phát hiện ba lỗi: owner thư mục dữ liệu có sẵn, semaphore của publisher bị chết, routing sau uninstall giữ dữ liệu. Đã sửa, thêm test hồi quy và review lại không còn lỗi critical/important trong ba phần này.
- Payload service PyInstaller onedir build thành công; đã xác nhận `python313.dll`, `pythoncom313.dll`, `pywintypes313.dll`, `servicemanager.pyd` có trong payload.
- Chính EXE chạy smoke hai Test Pattern: cả hai connected, RGB 1280×720, 24,82 fps, Qt responsive, exit 0 sau khi dọn worker. Không chiếm thiết bị OBS trong phép thử này.
- CLI chẩn đoán frozen trả `not_installed` đúng khi chưa có service; không mở cửa sổ và không đưa credential vào JSON.
- Bộ cài Inno Setup đã cài thật qua UAC: service Automatic, LocalService Session 0, service SID unrestricted; recovery cấu hình 5/15/30 giây. Publisher tạo trong phiên owner, không có cửa sổ. Bản cài thử lỗi ban đầu đã gỡ sạch, các lỗi native API được sửa và thêm test.
- Pipe giữa owner và LocalService đã xác thực hai chiều và truyền hình thật. Hai Test Pattern cùng kết nối: 720p nhận 1.159 khung/60 giây (**19,32 fps**); 1080p nhận 976 khung/60,03 giây (**16,26 fps**). Cả hai `stale=0`, chọn nguồn và disconnect thành công. Đây là tốc độ đo đường nhận IPC, chưa đạt 25 khung mới/giây; output có thể lặp khung ở nhịp 25 fps. Chưa đo CPU/độ trễ end-to-end hoặc chạy dài hạn.
- Lưu cấu hình DPAPI dưới LocalService thành công; kiểm tra owner đọc trực tiếp `cameras.dat` bị Access Denied. Probe service → OBS Virtual Camera 12 giây báo đang phát, process hoạt động, kết nối service thành công và dọn sạch khi dừng. Chưa xác nhận hình trong Meet/Zoom.
- Stop thủ công giữ trạng thái Stopped sau 6 giây; Start lại đổi PID, pipe xác thực lại được và đọc nguyên cấu hình revision 5 từ DPAPI. Cả hai nguồn vẫn dừng đúng vì cấu hình thử nghiệm không bật auto-connect.
- Còn cần nghiệm thu: boot trước logon, UAC bằng tài khoản quản trị khác, kill-host/SCM recovery thật, RTSP công ty mất mạng/kết nối lại, Meet/Zoom, máy không cài Python. Không xem unit/build hoặc cấu hình SCM là bằng chứng những mục này đã đạt.

Hướng dẫn chạy/gỡ trong [SERVICE_PACKAGING.md](SERVICE_PACKAGING.md); các bước máy đích còn lại trong [MANUAL_TESTS.md](MANUAL_TESTS.md).

## Cập nhật nhiều camera và tự khởi động — 28/09/2026

- `python -m unittest discover -s tests -q`: **50/50 đạt**, 14,091 giây.
- Hai worker nguồn thật tạo ảnh đỏ/xanh: đổi nguồn đưa vào shared memory của publisher đúng màu, PID publisher giữ nguyên; ngắt/kết nối lại một nguồn không dừng nguồn còn lại; đóng dọn toàn bộ worker. Publisher trong phép thử này là giả, không chiếm OBS đang được người dùng sử dụng.
- Windows DPAPI thật: lưu/đọc lại chính xác tài khoản giả có `@` và `%40`; file mã hóa không chứa URL/tài khoản dạng rõ; lỗi ghi nguyên tử giữ nguyên dữ liệu cũ; dữ liệu hỏng được từ chối.
- GUI lưu hai nguồn, mở lại và tự kết nối/bật publisher giả; nhớ camera được chọn; bỏ Ghi nhớ xóa file mã hóa. Registry được mock để kiểm tra đúng HKCU/value và quoting đường dẫn EXE; không thay đổi cấu hình khởi động thật của người dùng trong test.
- Smoke mã nguồn hai Test Pattern đồng thời: cả hai `connected`, preview RGB 1280×720, nguồn được chọn khoảng 25,41 fps, exit 0; đã xem ảnh giao diện.
- Build onefile mới: `dist-multicam/IPCameraBridge.exe` (bản cũ ở `dist` đang chạy nên không ghi đè). Smoke chính EXE với `--smoke-test .local\smoke-multi-exe --smoke-cameras 2`: hai nguồn đều `connected`, 1280×720 RGB, khoảng 25,2 fps, exit 0 và dọn xong worker. Không bật OBS trong smoke này.
- Theo ảnh người dùng cung cấp trước thay đổi này, một camera RTSP thật đã có preview khoảng 14,5 fps. Chưa nghiệm thu nhiều camera RTSP thật, đổi hình trong Meet, hoặc đăng xuất/đăng nhập Windows với cấu hình mới.
- Không thêm dependency. Dữ liệu camera chỉ lưu khi người dùng bật Ghi nhớ; startup chỉ chứa đường dẫn chạy ứng dụng.

## Báo cáo giai đoạn đầu (lịch sử trước bản nhiều camera)

Ngày kiểm tra: 28/09/2026. Máy thực thi: Windows 11 x64, build 26200,
CPython 3.13.5 x64. Chưa có OBS Virtual Camera và chưa có camera IP thật.

## Kết quả đã xác minh

- `.venv\Scripts\python.exe -m unittest discover -s tests -v`: **38/38 bài đạt**, lần chạy ghi nhận 6,282 giây.
- `.venv\Scripts\python.exe -m pip check`: **No broken requirements found**.
- Các phiên bản thực cài được lưu trong [requirements-lock.txt](../requirements-lock.txt).
  Căn cứ tương thích và tài liệu chính thức: [DEPENDENCIES.md](DEPENDENCIES.md).
- GUI chạy bằng Qt offscreen trên Windows; timer đáp ứng, nguồn chạy trong process,
  preview có hình, Start/Stop và đóng cửa sổ dọn worker. Đây không phải nghiệm thu thao tác
  thủ công trên desktop hoặc máy Windows thứ hai.

| Phạm vi | Bằng chứng / kết quả |
|---|---|
| Test Pattern 720p | Có chuyển động, số khung, thời gian; 1280×720 RGB; khoảng 24–25 fps trong smoke ngắn. |
| File thật 1080p | File MJPG 640×480 ở 15 fps giải mã bằng PyAV, lặp sau EOF; output 1920×1080, giữ tỷ lệ 4:3 và viền đen; khoảng 14,7 fps nguồn. |
| RGB/BGR, tỷ lệ | Test pixel đỏ/xanh, kích thước và padding; publisher nhận RGB. Ảnh preview 720p/1080p đã được mở kiểm tra. |
| Latest frame | Shared-memory chỉ giữ một ảnh; đọc trả bản copy; lock contention có timeout. Không dùng queue chứa ảnh. |
| RTSP mock | TCP, timeout mở/đọc 5/3 giây, EOF/mất nguồn, backoff có giới hạn, 8 reconnect ngoài lần đầu, hủy chờ khi Stop, reset sau 10 giây ổn định. |
| Nhịp RTSP | Test xác nhận nguồn 25/30/50/60 fps đều cập nhật khoảng 100 khung trong 4 giây ở đích 25 fps. |
| Dừng / đóng | Spawn worker thật cố tình treo được terminate/reap; ba chu kỳ nguồn và ba chu kỳ output giả không để lại worker; Start trùng bị chặn. |
| Mất nguồn | Slate thay ảnh cũ, watchdog phục hồi khi ảnh mới trở lại, không tự báo connected sau Ngắt. |
| OBS chưa cài | Đã thử pyvirtualcam thật trên máy này: trả lỗi thiếu OBS; ứng dụng hiển thị hướng dẫn tiếng Việt và preview tiếp tục. |
| OBS đang bận / vòng đời | Backend giả và named mutex Windows thật kiểm tra lỗi, tránh hai publisher, đóng camera và giải phóng khóa. Chưa thử với OBS thật đang phát. |
| Bí mật | URL userinfo/query nhạy cảm được che trong logging; lỗi decoder không được chuyển nguyên văn; SourceSpec repr không lộ bí mật; URL/username/password không lưu. |
| Cấu hình | Whitelist ba trường; JSON lỗi về mặc định; đọc UTF-8 có BOM của Windows; ghi nguyên tử không mất cấu hình cũ khi lỗi. |

## Các mốc

1. **Pattern + webcam:** đã triển khai pipeline và xác minh lỗi thiếu backend; xuất thành công
   qua driver OBS và Google Meet còn chờ cài OBS/kiểm thử thực tế.
2. **GUI + file:** đã kiểm tra tự động và smoke offscreen với pattern 720p, video 1080p.
3. **RTSP:** đã triển khai và kiểm tra mock. Chưa thực hiện được tích hợp localhost:
   tải MediaMTX trong sandbox thất bại với lỗi Schannel `SEC_E_NO_CREDENTIALS (0x8009030e)`.
   Lượt tải lại ngoài sandbox bị hủy khi chờ phê duyệt; không có server/publisher nào được
   khởi động. Không coi reconnect qua RTSP thật là đã được xác minh.
4. **Đóng gói:** PyInstaller onedir hoàn tất với exit code 0. Bản
   `dist/IPCameraBridge/IPCameraBridge.exe` chạy smoke thành công (exit 0) cho
   pattern 720p và file video 1080p; cả hai thoát sạch, không còn process IPCameraBridge.
   Bản build có 344 file, khoảng 311 MiB; phải giữ nguyên toàn bộ thư mục.

## Bằng chứng bản executable

| Phép thử | Trạng thái nguồn | Kích thước RGB | FPS nguồn quan sát | Exit |
|---|---|---|---|---|
| Pattern + thử backend thật | connected | 1280×720×3 | 24,28 | 0 |
| File MJPG 15 fps, 4:3 → 1080p | connected | 1920×1080×3 | 14,63 | 0 |

Backend trong phép thử đầu trả `Không tìm thấy OBS Virtual Camera`; đó là kết quả
đúng cho máy chưa có driver, **không phải bằng chứng webcam đã xuất thành công**.

Lệnh tái hiện (từ thư mục dự án):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm IPCameraBridge.spec
.\dist\IPCameraBridge\IPCameraBridge.exe --smoke-test .local\smoke-exe --probe-webcam
.\dist\IPCameraBridge\IPCameraBridge.exe --smoke-test .local\smoke-exe-file --source-file .local\sample-4x3.avi
```

Mỗi smoke lưu `result.json`, `preview.png`, `bridge.log` và config không nhạy cảm
trong thư mục chỉ định. File thử MJPG được tạo cục bộ ở `.local/sample-4x3.avi`.

## Sửa lỗi từ rà soát độc lập

- Giữ pha deadline RTSP: cách tính từ thời điểm khung vừa nhận làm nguồn 30 fps tụt còn 15 fps.
  Test tái hiện thất bại trước sửa, đạt sau sửa.
- Xóa cờ watchdog khi ngắt nguồn và giới hạn phục hồi vào trạng thái lost, tránh slate
  do controller tạo làm nguồn đã dừng hiện connected. Test thất bại trước sửa, đạt sau sửa.
- Ngắt nguồn thủ công dừng cả webcam để giải phóng shared-memory trước khi khởi động lại;
  mất RTSP tự động vẫn giữ output ở slate trong lúc retry.
- Bản Qt offscreen không tự liệt kê font Windows; smoke nạp Segoe UI có sẵn để ảnh chụp
  đọc được. Ứng dụng desktop dùng font nền tảng.

## Chưa nghiệm thu

- Tích hợp RTSP localhost qua máy chủ thật (hiện chỉ có test mock).
- OBS Virtual Camera thật hoạt động, có thể mở trong Chrome/Google Meet và người nhận
  cuộc họp thấy chuyển động, màu, tỷ lệ đúng.
- i-PRO WV-U2130LA thật, URL/tài khoản thực tế, H.264/H.265 từ camera, ngắt mạng vật lý rồi phục hồi.
- Chạy camera liên tục 30 phút; đo CPU/RAM và độ trễ quan sát trên máy đích.
- Windows 10 và một máy Windows/VM sạch không có Python dự án.
- Zalo PC và Zavi: **chưa được coi là đã hỗ trợ**.

Thực hiện các bước còn lại theo [MANUAL_TESTS.md](MANUAL_TESTS.md). Không suy ra
khả năng tương thích cuộc gọi/camera thật chỉ từ test tự động hoặc RTSP localhost.

