# Phụ thuộc và kiểm tra tương thích

Ngày đối chiếu nguồn chính thức: **2026-09-28**. Mục tiêu: **Windows 10 1809+ / Windows 11 x64, CPython 3.13 thông thường**. Bảng dưới xác nhận phiên bản, wheel và API công bố; không thay thế việc cài, chạy test, build và nghiệm thu trên máy đích.

## Phiên bản khóa

| Gói | Phiên bản | Wheel Windows x64 phù hợp Python 3.13 | Vai trò / nguồn chính thức |
|---|---|---|---|
| PySide6 | `6.11.2` | `cp310-abi3-win_amd64` | [GUI Qt](https://pypi.org/project/PySide6/6.11.2/) |
| av | `18.1.0` | `cp311-abi3-win_amd64` | [PyAV / giải mã FFmpeg](https://pypi.org/project/av/18.1.0/) |
| numpy | `2.5.3` | `cp313-cp313-win_amd64` | [Mảng RGB uint8](https://pypi.org/project/numpy/2.5.3/) |
| opencv-python-headless | `4.13.0.92` | `cp37-abi3-win_amd64` | [Resize, vẽ pattern; không có GUI OpenCV](https://pypi.org/project/opencv-python-headless/4.13.0.92/) |
| pyvirtualcam | `0.15.0` | `cp313-cp313-win_amd64` | [Xuất vào webcam đã cài](https://pypi.org/project/pyvirtualcam/0.15.0/) |
| PyInstaller | `6.22.3` | `py3-none-win_amd64` | [Chỉ cần khi build](https://pypi.org/project/pyinstaller/6.22.3/) |

Wheel `abi3` có thể dùng trên CPython mới hơn mốc trong tên wheel. Không dùng các wheel `cp313t` dành cho bản free-threaded. Pin trực tiếp ở `requirements.txt` và `requirements-build.txt`; đây không phải lockfile toàn bộ phụ thuộc bắc cầu.

OpenCV 4.13.0.92 công bố `numpy>=2` với Python từ 3.9, không có trần NumPy trong metadata. Chọn nhánh 4.13 cho MVP dù OpenCV 5.0.0.93 đã có; không cần đổi major để resize/vẽ ảnh. Chỉ cài một biến thể OpenCV vì các biến thể dùng chung namespace `cv2`. [Metadata OpenCV](https://pypi.org/pypi/opencv-python-headless/4.13.0.92/json)

Qt 6.11 liệt kê Windows 10 1809+ và Windows 11 x64 trong nền tảng hỗ trợ. [Qt supported platforms](https://doc.qt.io/qt-6/supported-platforms.html)

## PyAV và FFmpeg

Wheel PyAV đã kèm các thư viện FFmpeg; đường giải mã này không gọi hoặc yêu cầu cài riêng `ffmpeg.exe`. Không khóa một bản FFmpeg hệ thống khác với bộ thư viện trong wheel. [PyAV installation](https://pyav.basswood.io/docs/stable/overview/installation.html)

`av.open(..., timeout=(5.0, 3.0), options={"rtsp_transport": "tcp"})` sử dụng tuple timeout mở/đọc theo **giây**. Tham số FFmpeg RTSP `timeout`, nếu dùng trực tiếp trong options, có đơn vị **microsecond**; không nhầm với `listen_timeout`, vốn dành cho chế độ lắng nghe. [PyAV API](https://pyav.basswood.io/docs/stable/api/_globals.html), [FFmpeg RTSP](https://ffmpeg.org/ffmpeg-protocols.html#rtsp)

Giữ chế độ decode SLICE mặc định; FRAME/AUTO có thể tăng khoảng trễ từ packet đến frame. File cần pacing theo timestamp; RTSP cần đọc liên tục và chỉ giữ khung mới nhất, không ngủ theo fps đầu ra trong vòng đọc mạng. Đây là quyết định triển khai để tránh tồn đọng hình, không phải bảo đảm camera/mạng luôn đạt độ trễ cố định. [PyAV threading](https://pyav.basswood.io/docs/stable/cookbook/basics.html#threading)

## Backend OBS

Ứng dụng dùng backend `obs`, thiết bị **OBS Virtual Camera**, khung RGB. Backend Windows chuyển về NV12. OBS phải được cài riêng; khuyến nghị bộ cài Windows hiện hành, OBS 30 trở lên. Mức 30 là khuyến nghị triển khai của dự án; pyvirtualcam ghi nhận OBS Windows có camera tích hợp từ 26.0. [OBS download](https://obsproject.com/download), [pyvirtualcam README](https://github.com/letmaik/pyvirtualcam#supported-virtual-cameras), [API](https://letmaik.github.io/pyvirtualcam/)

Lớp `VirtualCameraOutput` trong `ip_camera_bridge/output.py` quản lý vòng đời camera và nhịp xuất; chạy trong tiến trình riêng. Named mutex Windows ngăn hai phiên bridge cùng chiếm quyền phát. Kiểm tra bận bên trong backend vẫn cần thiết vì OBS là một chương trình khác.

OBS chỉ có một đầu ra camera chung. Không thể đồng thời để bridge phát vào OBS Virtual Camera và để OBS xuất cảnh trộn vào cùng thiết bị. Dừng **Virtual Camera** trong OBS trước khi bật ở bridge. Mỗi phiên xuất của bridge cũng phải giành quyền độc quyền; phiên thứ hai báo không thể mở đầu ra.

Backend kiểm tra đăng ký thiết bị và báo lỗi khi thiếu OBS. Khi vùng nhớ `OBSVirtualCamVideo` đã tồn tại, khởi tạo thất bại; thông báo khởi tạo chung cũng có thể xuất phát từ lỗi tài nguyên khác, nên không quy mọi lỗi thành “đang bận”. [Backend Windows](https://github.com/letmaik/pyvirtualcam/blob/main/pyvirtualcam/native_windows_obs/virtual_output.h), [queue Windows](https://github.com/letmaik/pyvirtualcam/blob/main/pyvirtualcam/native_windows_obs/queue/shared-memory-queue.c)

Nếu bộ cài OBS không đăng ký thiết bị đúng, OBS hướng dẫn chạy `virtualcam-install.bat` trong `data\obs-plugins\win-dshow` với quyền quản trị. Bridge không tự chạy bước này. [OBS troubleshooting](https://obsproject.com/kb/virtual-camera-troubleshooting)

## Đóng gói

PyInstaller phải chạy trên Windows x64 với đúng interpreter mục tiêu. Dùng **onefile**, kiểm tra executable trước khi phân phối; Python được đưa vào bundle, driver OBS thì không. [PyInstaller operating mode](https://pyinstaller.org/en/stable/operating-mode.html)

Hook PyAV của `pyinstaller-hooks-contrib` đã thu thập module PyAV và thư mục `av.libs` trên Windows. Dùng hook có sẵn trước, chỉ thêm hidden import/DLL khi log build hoặc smoke test chứng minh thiếu. [Hook PyAV](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/_pyinstaller_hooks_contrib/stdhooks/hook-av.py)

Gói cài được và import được chưa đủ để xác nhận bản đóng gói. Cần chạy unittest, build, smoke `.exe`, rồi làm các mục OBS/Meet/camera thật trong [MANUAL_TESTS.md](MANUAL_TESTS.md). Kết quả thực thi phải được ghi riêng; tài liệu này không tuyên bố những bước đó đã đạt.
