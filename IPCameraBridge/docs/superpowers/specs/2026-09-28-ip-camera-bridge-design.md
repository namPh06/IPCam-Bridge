# IP Camera Bridge — thiết kế MVP

Yêu cầu gốc: ứng dụng Windows 10/11 x64 đưa một nguồn Test Pattern, file video
hoặc RTSP qua preview RGB và OBS Virtual Camera; mặc định 1280×720, 25 fps,
có lựa chọn 1920×1080. Chỉ hình ảnh. Không discovery, ghi hình, AI, PTZ hay driver riêng.

Giao diện PySide6 tiếng Việt. PyAV giải mã file/RTSP TCP; OpenCV headless resize
letterbox và tạo pattern; NumPy giữ dữ liệu RGB uint8. Decoder chạy trong tiến
trình spawn, có timeout và có thể terminate khi native decoder không đáp ứng.
Bộ đệm shared memory chỉ có một khung; UI đọc bằng timer, không xếp hàng ảnh Qt.
Một tiến trình riêng xuất pyvirtualcam ở nhịp 25 fps, lặp khung khi nguồn chậm.
Preview không phụ thuộc backend webcam. Start trùng bị chặn trong bộ điều phối.

Nguồn báo connecting/connected/lost/retrying/error/stopped qua queue có giới hạn.
RTSP reconnect chờ 1, 2, 4, 8, 15 giây (giới hạn 15 giây), tối đa 8 lần lỗi
liên tiếp; reset sau khi nhận ảnh ổn định 10 giây để tránh reconnect dồn dập.
Stop dùng Event để hủy chờ; nếu worker treo thì terminate và join có giới hạn.
Khi không có ảnh mới sau 5 giây, cả preview và webcam chuyển sang bảng mất kết nối.
Ngắt nguồn thủ công dừng cả webcam và giải phóng bộ đệm; mất RTSP tự động vẫn xuất bảng thông báo trong khi reconnect.

Không ghi URL RTSP, username, password vào cấu hình. Chỉ lưu loại nguồn,
đường dẫn file và độ phân giải. Credentials chỉ ở RAM; không truyền qua dòng lệnh.
Không đưa nguyên văn lỗi FFmpeg/pyvirtualcam ra UI/log. FFmpeg logging bị tắt để
tránh C-level diagnostics lộ URL. Các lỗi tiếng Việt được ánh xạ theo ngữ cảnh.

Không tự cài OBS/driver. Hướng dẫn cài OBS riêng; nhắc tắt Virtual Camera trong OBS
trước khi bắt đầu, và dùng named mutex ngăn các phiên app cùng chiếm đầu ra.
Không khẳng định Google Meet/camera thật/Zalo đã hoạt động nếu chưa kiểm tra.

Kiểm chứng: unittest cho RGB/letterbox, pattern động, file pacing/loop, credentials,
reconnect, stop worker treo, output cadence/exclusivity và GUI offscreen. Đóng gói
PyInstaller onedir trên Windows và chạy smoke executable. Kết quả thực đo ghi riêng.
