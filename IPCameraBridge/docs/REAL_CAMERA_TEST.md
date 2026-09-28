# Kiểm thử camera i-PRO WV-U2130LA

Thông số do người dùng cung cấp: H.264, 1920×1080, 15 fps.
Kiểm tra TCP cổng 554 đã thành công; chưa xác nhận xác thực RTSP hoặc hình ảnh camera thật.

## 1. Preview

Chọn RTSP, nhập riêng:
- URL: `rtsp://192.168.100.77:554/Src/MediaInput/stream_1`
- Username: `admin`
- Password: nhập trực tiếp mật khẩu nguyên bản trong giao diện. Giữ nguyên `@`; không tự đổi thành `%40`.

Nhấn Kết nối. Xác nhận hình đang chuyển động và trạng thái đã kết nối. Nguồn 15 fps không tạo ra 25 hình khác nhau mỗi giây: webcam xuất 25 fps bằng cách lặp khung mới nhất. Ba ô RTSP không được lưu khi đóng nếu tắt Ghi nhớ camera; bật tùy chọn này sẽ lưu bằng Windows DPAPI.

## 2. Webcam ảo

Sau khi preview chạy, nhấn bật webcam. Máy cần có OBS Virtual Camera được cài bằng bộ cài OBS Windows. Nếu OBS đang xuất Virtual Camera, dừng nó trước để bridge sử dụng thiết bị. Xác nhận trạng thái webcam đang chạy; nếu lỗi, ghi lại thông báo trên giao diện.

## 3. Google Meet

Trong màn hình trước cuộc họp, mở danh sách camera dưới preview và chọn OBS Virtual Camera; hoặc vào Tuỳ chọn khác → Cài đặt → Video. Cho phép Meet truy cập camera khi trình duyệt hỏi. Xác nhận hình chuyển động khớp với bridge. Chọn micro riêng vì bridge chỉ xuất hình.

Hướng dẫn Google: https://support.google.com/meet/answer/10409699?hl=vi

## 4. Ngắt, kết nối lại, đóng

- Nhấn Ngắt kết nối: cả nguồn và webcam phải dừng. Kết nối lại rồi bật webcam lại.
- Khi có thể gián đoạn kết nối của laptop một cách an toàn, ngắt mạng của laptop rồi khôi phục. Không cần tắt camera dùng chung. Mong đợi ảnh báo mất kết nối và tự thử lại; nếu hết lượt thử, nhấn Kết nối lại.
- Đóng cửa sổ trong lúc đang chạy hoặc đang thử lại: ứng dụng và worker con phải thoát.
- Ngoài mạng công ty, dùng Test Pattern để kiểm tra preview và webcam độc lập.

## Log và kết quả

Log: `%LOCALAPPDATA%\IPCameraBridge\bridge.log` (và `.1`, `.2` nếu có).
Ứng dụng chỉ ghi trạng thái, không ghi URL hay thông tin đăng nhập; bộ định dạng còn che userinfo trong URL và bỏ traceback. Không thêm mật khẩu vào báo cáo hoặc ảnh chụp. Kiểm tra nội dung trước khi gửi log.

Gửi kết quả từng bước, thời điểm xảy ra lỗi và log đã che thông tin đăng nhập. Chưa đánh dấu camera, webcam hay Meet đạt khi chưa có quan sát thực tế.

Kiểm thử tự động ngày 2026-09-28: 41/41 đạt, gồm mã hóa @ đúng một lần, bảo toàn chuỗi %40 nguyên bản, truyền thông tin đăng nhập qua UI không mã hóa trước, không lưu thông tin đăng nhập, retry và dọn worker. Kiểm thử mock không thay thế kiểm thử camera thật.
