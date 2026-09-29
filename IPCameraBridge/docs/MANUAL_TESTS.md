# Checklist nghiệm thu thủ công

## Windows Service

- [ ] Cài `IPCameraBridge-Setup.exe` từ tài khoản owner thường; UAC dùng cùng hoặc khác tài khoản quản trị vẫn giữ đúng owner.
- [ ] Lưu hai Test Pattern, bật tự kết nối; chọn nguồn xuất, đóng cửa sổ vẫn có hình.
- [ ] Chạy các lệnh chẩn đoán dưới đây từ PowerShell của owner; JSON không chứa thông tin đăng nhập. `status` không thay đổi cấu hình, `frames` chỉ đọc hình, `access` xác nhận owner không đọc trực tiếp file bí mật.

```powershell
& "$env:ProgramFiles\IPCameraBridge\IPCameraBridge.exe" --check-service "$env:TEMP\ipcb-status.json"
& "$env:ProgramFiles\IPCameraBridge\IPCameraBridge.exe" --check-service "$env:TEMP\ipcb-frames.json" --check-mode frames --duration 60
& "$env:ProgramFiles\IPCameraBridge\IPCameraBridge.exe" --check-service "$env:TEMP\ipcb-access.json" --check-mode access
```

- [ ] Thử 720p và 1080p riêng, mỗi lần 60 giây; xem fps, stale và kích thước trong báo cáo. Ngắt tất cả nguồn trước đổi độ phân giải.
- [ ] Kiểm tra `sc.exe qc IPCameraBridgeCapture`, `sc.exe qsidtype IPCameraBridgeCapture`, `sc.exe qfailure IPCameraBridgeCapture`: LocalService, Automatic, service SID, recovery 5/15/30 giây.
- [ ] Trong phiên thử riêng, Stop/Start service, xác nhận publisher tự kết nối lại; Stop thủ công webcam không bị tự bật lại. Host chết phải dọn worker bằng Job Object và được SCM khôi phục.
- [ ] Khởi động lại Windows vào thời điểm phù hợp: service chạy trước logon, helper ẩn chỉ chạy sau logon. Không đánh dấu đạt chỉ từ test foreground.
- [ ] RTSP thật: nhập mật khẩu nguyên bản có `@`, mất mạng lâu rồi phục hồi, chọn camera khác, đóng UI; kiểm tra riêng Meet và Zoom có video chuyển động.
- [ ] Gỡ bằng Settings → Apps: task/helper/service dừng sạch, giữ dữ liệu mặc định, không gỡ OBS. Cài lại cùng owner đọc lại camera; owner khác bị từ chối.
- [ ] Máy Windows x64 không có Python vẫn cài/chạy được bộ cài.

Tài liệu này mô tả **việc cần kiểm tra**, không phải kết quả đã chạy. Các ô đều để trống cho người nghiệm thu. Test tự động/mock không chứng minh thiết bị OBS, camera thật, Google Meet hoặc Zalo hoạt động trên máy đích.

Ghi ngày, Windows/build, CPU, Python x64 hoặc bản `.exe`, phiên bản OBS/Chrome/Zalo, nguồn thử và kết quả. Không ghi URL RTSP thật, tài khoản, mật khẩu hoặc ảnh chụp chứa bí mật. Có thể định danh nguồn bằng nhãn như “camera thử A”.

## 1. Cài đặt và preview

- [ ] Chạy từ `.venv` Python 3.13 x64 theo README; cửa sổ mở, kéo/đổi kích thước được.
- [ ] Trên máy chưa có OBS, Test Pattern vẫn có preview. Bật webcam phải báo lỗi dễ hiểu, không làm mất preview hoặc thoát ứng dụng.
- [ ] Test Pattern ở 720p có số khung và thời gian tăng; thanh tối di chuyển.
- [ ] Kiểm tra thứ tự vạch từ trái sang phải: trắng, vàng, cyan, xanh lá, magenta, đỏ, xanh dương. Đỏ/xanh dương không bị đảo trong preview và ứng dụng nhận.
- [ ] Dừng nguồn và webcam, đổi sang 1080p rồi chạy lại; hình không méo, giao diện vẫn đáp ứng.
- [ ] Ảnh 4:3 và video dọc được giữ tỷ lệ, có viền đen thích hợp ở đầu ra 16:9.

## 2. OBS Virtual Camera và vòng đời

- [ ] Cài OBS bằng bộ cài Windows, **Stop Virtual Camera** trong OBS, rồi bật đầu ra từ bridge. Ứng dụng nhận thấy đúng tên **OBS Virtual Camera**.
- [ ] Nhấn Start/Stop lặp 10 lần; không có nhiều tiến trình xuất chồng nhau, có thể bật lại sau khi dừng.
- [ ] Đang xuất ở bridge A, mở bridge B và thử xuất. B báo không chiếm được đầu ra, A tiếp tục hoạt động. Dừng A rồi thử lại ở B.
- [ ] Bật Virtual Camera trong OBS trước, sau đó thử bật ở bridge: báo không mở được/đang được dùng; không làm ứng dụng treo. Dừng output OBS và thử lại.
- [ ] Dừng webcam trong bridge, kiểm tra trạng thái dừng, tiếp tục sử dụng preview.
- [ ] Ngắt nguồn khi đang xuất: giao diện phản hồi, cả nguồn và webcam dừng; không còn retry hoặc worker con.
- [ ] Đóng cửa sổ khi nguồn đang chạy; không còn tiến trình con do bridge tạo sau khi đóng hoàn tất.
- [ ] Chạy liên tục 15 phút ở 720p và 1080p, theo dõi CPU/RAM và chuyển động; ghi máy thử và mức sử dụng, không giả định mọi máy đạt 25 fps.

## 3. File video

- [ ] File có đồng hồ/đếm thời gian: phát một đoạn ít nhất 30 giây, so sánh với đồng hồ thực; không chạy nhanh theo tốc độ giải mã.
- [ ] File 12/15 fps và file 50/60 fps giữ tốc độ nội dung gốc dù đầu ra đặt 25 fps.
- [ ] Đến cuối file tự phát lại; không mất preview, không tăng hàng đợi hoặc treo sau nhiều vòng.
- [ ] File có âm thanh vẫn chỉ xuất hình; Meet dùng micro được chọn riêng.
- [ ] File không tồn tại, file hỏng, file chỉ âm thanh: báo không đọc được, vẫn đổi nguồn hoặc đóng ứng dụng được.
- [ ] Chọn lại file khác sau khi ngắt nguồn; file trước không tiếp tục chạy ngầm.

## 4. RTSP và lỗi mạng

Dùng camera hoặc máy chủ RTSP được phép kiểm tra. Nhập URL của thiết bị vào UI; chỉ ví dụ tài liệu là `rtsp://camera.example:554/stream` với tài khoản ở hai ô riêng.

- [ ] URL hợp lệ và tài khoản đúng: có hình, màu đúng; UI vẫn đáp ứng trong lúc mở nguồn.
- [ ] URL sai scheme, thiếu host hoặc cổng không hợp lệ: thông báo đầu vào không hợp lệ, không ghi lại nội dung URL.
- [ ] Sai path/tài khoản hoặc máy chủ không trả dữ liệu: không treo cửa sổ; cấu hình timeout mở 5 giây, đọc 3 giây. Thời gian toàn chu kỳ còn gồm startup và backoff; không coi đây là cam kết tổng thời gian chính xác.
- [ ] Mất mạng khi đang chạy: ảnh thông báo thay thế khung cũ; watchdog khung cũ có ngưỡng 5 giây, lỗi nguồn có thể cập nhật sớm hơn.
- [ ] Khôi phục mạng trước khi hết lượt retry: tự có lại hình, không phải khởi động lại ứng dụng.
- [ ] Quan sát retry: lần đầu cộng tối đa 8 lượt reconnect; khoảng chờ 1, 2, 4, 8, 15, 15, 15, 15 giây, chưa tính thời gian mở/đọc thất bại.
- [ ] Nguồn hoạt động ổn định ít nhất 10 giây rồi mất lại: ngân sách retry được đặt lại.
- [ ] Khi đang chờ retry, nhấn **Ngắt kết nối**: hủy chờ, không tự kết nối lại sau đó.
- [ ] Hết lượt retry: có trạng thái lỗi; người dùng kết nối lại được sau khi sửa nguồn.
- [ ] Đóng ứng dụng trong lúc mở nguồn hoặc chờ dữ liệu: UI không treo vô hạn và tiến trình con được dọn.

- [ ] Camera thật chạy liên tục **30 phút**: ghi thời điểm bắt đầu/kết thúc, số lỗi/reconnect, CPU trung bình/đỉnh, RAM ban đầu/cuối/đỉnh và độ trễ quan sát. Đo riêng 720p và 1080p nếu cần.

## 5. Cấu hình và bí mật

- [ ] URL, username và password đều hiển thị ký tự che.
- [ ] Khi tắt Ghi nhớ camera, dùng thông tin giả rồi đóng/mở lại: ba ô RTSP trống; loại nguồn, đường dẫn file và độ phân giải được khôi phục.
- [ ] `%LOCALAPPDATA%\IPCameraBridge\config.json` chỉ chứa `source_kind`, `file_path`, `resolution`; không có URL RTSP hoặc tài khoản.
- [ ] Dùng token/tài khoản giả trong URL thử lỗi; kiểm tra UI và file log được tạo không lộ token/tài khoản. Không dùng bí mật thật để thử rò rỉ.
- [ ] Cấu hình JSON hỏng: ứng dụng vẫn mở với mặc định hợp lệ.

## 6. Google Meet trong Chrome

- [ ] Cho phép `meet.google.com` dùng camera. Chọn **OBS Virtual Camera** ở bộ chọn camera hoặc **Cài đặt → Video**.
- [ ] Test Pattern xuất hiện, số khung tăng và đỏ/xanh đúng. So sánh với preview bridge; self-view của ứng dụng họp có thể phản chiếu nên không dùng riêng chiều trái/phải để kết luận lỗi màu.
- [ ] Thử một cuộc gọi với người nhận/thiết bị thứ hai được phép; người nhận thấy chuyển động, màu và tỷ lệ đúng. Ghi kết quả riêng cho 720p và 1080p; Meet có thể tự giảm độ phân giải truyền.
- [ ] Đổi Test Pattern sang file rồi RTSP sau khi dừng/khởi động nguồn thích hợp; Meet nhận hình mới.
- [ ] Ngắt mạng RTSP rồi khôi phục; người nhận thấy trạng thái thay thế và hình trở lại.
- [ ] Chọn và thử micro riêng; không mong chờ âm thanh từ bridge.
- [ ] Thu hồi quyền camera của Meet rồi cấp lại: thực hiện được quy trình khôi phục trong README.

Nguồn thao tác: [Google Meet](https://support.google.com/meet/answer/10409699?hl=vi), [quyền camera Chrome](https://support.google.com/chrome/answer/2693767?hl=vi).

## 7. Zalo PC/Zavi và bản đóng gói

- [ ] **Zalo PC: chưa nghiệm thu.** Trên phiên bản Zalo thực tế, kiểm tra có chọn được OBS Virtual Camera hay không; nếu có, thử cuộc gọi được phép với người nhận và ghi phiên bản/kết quả. Không suy ra từ kết quả Meet.
- [ ] **Zavi: chưa nghiệm thu.** Xác nhận đúng ứng dụng/phiên bản được dùng, kiểm tra chọn OBS Virtual Camera và cuộc gọi với thiết bị thứ hai; ghi kết quả riêng, không suy ra từ Meet hoặc Zalo PC.
- [ ] Build bằng `build.ps1`, sao chép **một file** `dist\IPCameraBridge.exe` sang máy Windows x64 khác hoặc VM không có môi trường Python dự án.
- [ ] Chạy `IPCameraBridge.exe`: Test Pattern và file hoạt động; đóng/mở lại không mất DLL, không bật thêm cửa sổ con ngoài ý muốn.
- [ ] Máy nhận chưa có OBS: lỗi webcam rõ ràng. Sau khi cài OBS riêng: kiểm tra lại output, quyền camera và cuộc gọi.
- [ ] Ghi rõ các mục chưa thực hiện; chỉ đánh dấu đạt khi có quan sát thực tế.

## 8. Nhiều camera và mở cùng Windows

- [ ] Thêm hai camera thật, Kết nối tất cả, chọn lần lượt Camera xuất; Meet đổi hình mà không cần chọn lại thiết bị webcam.
- [ ] Ngắt mạng một camera: camera khác tiếp tục chạy; chọn camera mất mạng thấy ảnh trạng thái, không giữ ảnh camera trước đó.
- [ ] Ngắt/Kết nối lại từng nguồn, Ngắt tất cả và đóng cửa sổ: không còn worker con.
- [ ] Bật Ghi nhớ camera với tài khoản giả có `@` và `%40`; lưu, đóng và mở lại: khôi phục chính xác. `cameras.dat` không chứa bí mật dạng rõ, JSON/log không chứa tài khoản.
- [ ] Đặt EXE ở vị trí cố định, bật cả ba tùy chọn và lưu. Đăng xuất/đăng nhập Windows: ứng dụng tự mở, kết nối các nguồn, tự bật webcam từ camera đã chọn.
- [ ] Đăng nhập khi mạng chưa sẵn sàng, khôi phục mạng trong thời gian retry; kiểm tra hình trở lại. Nếu hết lượt, Kết nối tất cả hoạt động.
- [ ] Bỏ Mở cùng Windows và lưu: lần đăng nhập tiếp theo không tự mở. Bỏ Ghi nhớ camera và lưu: file camera mã hóa bị xóa.
- [ ] Đóng khi không ghi được cài đặt: cho phép hủy đóng hoặc đóng không lưu; không kẹt ứng dụng.

## Biên bản

| Ngày / máy / phiên bản | Mục thử | Đạt / lỗi / chưa chạy | Bằng chứng không chứa bí mật |
|---|---|---|---|
| Chưa ghi nhận | Camera thật / OBS / Meet / Zalo | Chưa chạy theo checklist này | — |
