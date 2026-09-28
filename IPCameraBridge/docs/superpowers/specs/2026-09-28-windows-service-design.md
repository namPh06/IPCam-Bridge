# IP Camera Bridge — Windows Service

Trạng thái: bản thiết kế để người dùng duyệt; chưa triển khai hoặc cài service.

## Mục tiêu đã xác nhận

Người dùng chọn Windows Service thực sự: cấu hình camera một lần, service tự chạy khi
Windows khởi động kể cả chưa đăng nhập; khi vào Meet/Zoom có thể chọn OBS Virtual Camera
mà không cần mở cửa sổ tool hay OBS. Giữ nhiều camera kết nối đồng thời, chọn một camera
để xuất, giữ Test Pattern để thử khi không có mạng công ty. Mật khẩu nhập riêng trong UI,
ký tự đặc biệt được mã hóa URL đúng một lần, không hardcode hoặc ghi vào log.

Giả định phạm vi: một tài khoản Windows sở hữu cấu hình trên một laptop Windows x64.
Không chia sẻ camera cho mọi tài khoản, không hỗ trợ nhiều phiên RDP đồng thời trong lần
thay đổi này. Người dùng vẫn chọn OBS Virtual Camera và cấp quyền trong ứng dụng họp.

## Bằng chứng và lựa chọn kiến trúc

Hiện tại `windows_settings.py` đăng ký HKCU Run, `ui.py` sở hữu CameraGroup, và đóng UI
sẽ đóng mọi decoder/publisher. Đây chưa phải Windows Service. File `cameras.dat` dùng
DPAPI của tài khoản đang đăng nhập; service dưới tài khoản khác không tự giải mã được.

Windows tách service ở Session 0 khỏi ứng dụng tương tác. Backend pyvirtualcam 0.15.0
và bộ đọc OBS dùng tên bộ nhớ `OBSVirtualCamVideo` không có tiền tố Global. Theo quy tắc
namespace của Windows, suy ra đưa publisher hiện tại vào service có thể tạo đầu ra ở
namespace khác với bộ đọc trong Meet/Zoom. Đây là kết luận từ mã nguồn và tài liệu,
chưa phải kết quả thử xuyên phiên trên laptop.

Các hướng đã xem xét:

- Chạy toàn bộ capture và OBS publisher trong service: ít thành phần, nhưng không dùng
  được làm thiết kế giao hàng với backend OBS hiện có vì vấn đề phiên nêu trên.
- Service capture + publisher ẩn trong phiên người dùng: chọn hướng này; giữ decoder
  và OBS backend hiện có, phần nhận camera thực sự chạy trước đăng nhập.
- Viết driver webcam riêng: có thể kiểm soát toàn bộ đường truyền, nhưng thêm native
  code, cài đặt/ký và phạm vi tương thích; không cần cho yêu cầu hiện tại.

## Hành vi người dùng

1. Cài OBS một lần. Cài chế độ service bằng bộ cài của tool với UAC một lần.
2. Mở quản lý, thêm/sửa camera, chọn camera xuất và Lưu cho service. Có thể chuyển danh
   sách đã ghi nhớ từ bản cũ qua kênh nội bộ mà không nhập lại mật khẩu.
3. Khi khởi động máy: Windows Service tự nhận camera, chưa cần người dùng đăng nhập.
4. Sau khi tài khoản sở hữu đăng nhập: publisher tự chạy ẩn, nhận hình từ service và
   phát OBS Virtual Camera; chỉ có biểu tượng khay, không mở cửa sổ quản lý.
5. Mở Meet/Zoom và chọn OBS Virtual Camera. Khả năng ứng dụng họp nhớ lựa chọn này do
   chính ứng dụng họp quyết định, tool không tự đổi cài đặt của họ.
6. Đóng cửa sổ quản lý chỉ ẩn UI, không ngắt nguồn hoặc publisher. Menu khay có Mở quản
   lý và Dừng webcam trong phiên; dừng service toàn bộ là thao tác riêng, rõ ràng.
7. Đăng xuất kết thúc publisher của phiên, capture service tiếp tục. Đăng nhập lại tự
   có publisher mới, tối đa một publisher cho tài khoản/phiên sở hữu.

Chưa đăng nhập thì service nhận/giải mã camera và giữ khung mới nhất. Không tuyên bố
Meet/Zoom nhận hình ở màn hình đăng nhập: ứng dụng họp và publisher cần phiên người dùng.

## Thành phần và tái sử dụng

### Capture service

- Tên service `IPCameraBridgeCapture`, startup Automatic, chạy bằng
  `NT AUTHORITY\LocalService` với service SID riêng và thư mục dữ liệu giới hạn quyền.
  Không yêu cầu mật khẩu Windows của người dùng, không chạy decoder bằng LocalSystem.
- Sử dụng lại SourceSpec, worker PyAV, SharedFrame, controller và phần chọn camera.
  Tách vòng điều phối khỏi Qt; service không mở GUI hoặc pyvirtualcam.
- SCM nhận trạng thái Start/Stop đúng hạn. Khởi động service thành công nghĩa là vòng
  điều phối sẵn sàng, không đồng nghĩa camera đã xác thực hoặc webcam đang phát.
- Worker nằm trong Windows Job Object để service chết/dừng không để decoder mồ côi.
  SCM recovery khởi động lại service sau lỗi; Stop có chủ đích không tự bật lại.
- Camera RTSP mất mạng tiếp tục thử có backoff tối đa 30 giây, không bỏ cuộc sau tám
  lượt như chế độ desktop. Lỗi xác thực/địa chỉ hiện rõ, giới hạn tần suất thử và log;
  Stop hoặc xóa camera hủy ngay retry. Nguồn khác tiếp tục hoạt động.
- File video trong service phải là file cục bộ service đọc được; không tự mở rộng
  quyền toàn bộ thư mục người dùng hoặc dùng ổ mạng mapped của phiên đăng nhập.

### Publisher và quản lý trong phiên người dùng

- Chế độ chạy ẩn của executable khởi động tại logon của đúng SID sở hữu; sử dụng backend
  `output.py` ở phiên đó. Dùng cơ chế chống chạy trùng trước khi tạo publisher.
- Giao diện quản lý hiện tại được nối đến service thay vì tạo decoder thứ hai. Đổi
  camera cập nhật khung được chọn, không mở lại OBS Virtual Camera.
- Chỉ cửa sổ quản lý hiện khi người dùng yêu cầu. Dùng QSystemTrayIcon có sẵn trong Qt.
- Service/publisher chưa sẵn sàng: tự thử lại có giới hạn tốc độ; webcam dùng ảnh trạng
  thái khi không có khung mới sau 5 giây. Không giữ ảnh của camera trước khi đổi sang
  nguồn mất mạng. Nếu thiết bị OBS bận, báo ở khay/UI và thử lại có backoff.
- Dừng webcam thủ công hủy tự thử trong phiên đó; lần đăng nhập tiếp theo thực hiện
  theo cấu hình đã lưu. Đóng UI không được hiểu là dừng webcam.

### Kênh nội bộ và cấu hình

- Named pipe chỉ trên máy, từ chối remote client, ACL cho đúng SID sở hữu, service SID,
  SYSTEM và Administrators; client kiểm tra PID/token server thuộc service đã đăng ký.
  Không dùng pipe với ACL mặc định, không mở HTTP/TCP listener, không dùng pickle.
- Thông điệp quản lý dạng JSON có schema/giới hạn kích thước: trạng thái, cập nhật cấu
  hình, chọn nguồn, kết nối/ngắt nguồn. Không có lệnh thực thi shell hoặc ghi file tùy ý.
- Kênh frame riêng truyền RGB của camera được chọn, chỉ khung mới nhất, có header
  version/kích thước/sequence/timestamp, trần 1920×1080×3 bytes. I/O có timeout và hủy
  được; client chậm không khóa vòng capture, không tạo hàng đợi tăng vô hạn. Xác minh
  throughput 1080p25 trước giao hàng.
- Service mã hóa bằng DPAPI dưới danh tính của service và ghi nguyên tử vào
  `%ProgramData%\IPCameraBridge\service\cameras.dat`. ACL file/thư mục chỉ service SID,
  SYSTEM và Administrators; không đổi sang DPAPI machine-scope chỉ để dễ chia sẻ.
- Owner SID được thiết lập ở bước cài đặt có quyền quản trị, không cho client tự nhận
  quyền sở hữu qua yêu cầu API. Owner là tài khoản sử dụng tool trước khi nâng quyền,
  không tự đổi sang tài khoản quản trị nhập trong hộp UAC. Danh sách trả về UI không
  kèm mật khẩu đã lưu; sửa camera
  có lựa chọn giữ mật khẩu cũ hoặc thay mới. Chỉ truyền mật khẩu khi người dùng nhập/lưu.
- Chuyển bản cũ: UI giải mã bằng tài khoản hiện tại, gửi qua pipe đã kiểm tra danh tính;
  service xác nhận đã lưu bền vững trước khi tắt autostart desktop cũ. Không ghi file
  chuyển tiếp plaintext, không đưa secret vào command line/log. Giữ bản cũ cho rollback
  cho đến khi người dùng chọn xóa dữ liệu cũ.
- Log tách service/publisher, có rotation, chỉ trạng thái và mã lỗi đã lọc. Có thể gửi
  log để hỗ trợ mà không kèm tài khoản hoặc URL có bí mật.

## Cài đặt và gỡ

- Một bộ cài EXE để gửi sang máy khác; payload service/publisher cài vào thư mục cố
  định dưới Program Files, user thường không được ghi đè. Không chạy service từ EXE
  onefile giải nén tạm hoặc thư mục Downloads có thể bị thay thế.
- Dùng pywin32 cho SCM, pipe, token/ACL và Job Object thay vì tự tạo hàng loạt binding
  ctypes. Chọn/pin bản hỗ trợ CPython 3.13 x64 và kiểm tra đóng gói khi triển khai.
- Cài/gỡ service và sửa Program Files cần quản trị/UAC. Cấu hình camera hằng ngày không
  cần quyền quản trị. OBS cài riêng; bộ cài tool không chiếm webcam từ chương trình khác.
- Khi cài service thành công, thay mục autostart desktop bằng helper chạy ẩn của owner;
  lưu thông tin rollback không chứa mật khẩu. Gỡ dừng service/helper, gỡ đúng service,
  autostart và binary của tool. Xóa dữ liệu camera là lựa chọn riêng, không xóa ngầm.
- Bản desktop độc lập vẫn dùng được khi chưa cài service. Không chạy hai chế độ xuất
  cùng lúc; UI hiển thị rõ chế độ đang sử dụng.

## Tiêu chí kiểm thử và hoàn thành

1. Các kiểm thử hiện có vẫn đạt, gồm @/%40, không log bí mật, nhiều nguồn và Stop.
2. Test schema/ACL/peer identity, dữ liệu pipe lỗi/quá cỡ, client chậm hoặc chết, không
   cho tài khoản không sở hữu đọc hình/cấu hình; không thể thực thi code từ thông điệp.
3. Test DPAPI dưới service identity, khôi phục cấu hình sau restart, migration thất bại
   không mất cấu hình cũ; tài khoản thường không đọc được file service.
4. SCM thật: Start/Stop/restart, báo trạng thái không chờ RTSP, kill worker/service,
   Job Object dọn tiến trình, recovery không tạo publisher hoặc decoder trùng.
5. Khởi động lại máy: bằng chứng service đã có frame Test Pattern trước logon; sau logon
   helper tự chạy ẩn, nhận frame xuyên Session 0 và xuất qua OBS thật.
6. Camera thật: mạng chưa sẵn sàng rồi có lại sau hơn ngân sách tám retry cũ; tự phục hồi;
   đổi ít nhất hai nguồn, đóng UI, đăng xuất/đăng nhập và kiểm tra Stop.
7. Meet và Zoom nhận hình chuyển động từ OBS Virtual Camera; camera được chọn đúng và
   đổi nguồn không cần chọn lại thiết bị. Kết quả từng ứng dụng ghi riêng.
8. Cài và gỡ trên máy Windows x64 không có Python dự án; kiểm tra 720p25 và 1080p25,
   CPU/RAM và độ trễ với cấu hình camera thực tế.

Unit test hoặc phiên chạy offscreen không thay thế kiểm thử boot/logon/SCM/Meet/Zoom.
Không tự reboot, logoff hoặc dừng phiên camera người dùng đang dùng để nghiệm thu.

## Nguồn kỹ thuật

- [Microsoft: service và phiên người dùng](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)
- [Microsoft: kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)
- [Backend pyvirtualcam 0.15.0](https://github.com/letmaik/pyvirtualcam/blob/v0.15.0/pyvirtualcam/native_windows_obs/queue/shared-memory-queue.c)
- [Bộ đọc OBS](https://github.com/obsproject/obs-studio/blob/master/shared/obs-shared-memory-queue/shared-memory-queue.c)
- [Microsoft: LocalService](https://learn.microsoft.com/en-us/windows/win32/services/localservice-account)
- [Microsoft: DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- [Microsoft: named pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
