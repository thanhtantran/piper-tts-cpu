# Piper TTS CPU (Python Script)

README này hướng dẫn sử dụng `main.py` để chuyển văn bản tiếng Việt thành file âm thanh WAV bằng Piper.

## 1) Tổng quan

Script `main.py` thực hiện pipeline:

1. Nhận text từ command line.
2. Chuẩn hóa text tiếng Việt (khoảng trắng, dấu `#`, số thập phân, `%`, `°C`).
3. Chia câu dài thành nhiều chunk nhỏ (`--chunk-size`, mặc định 150 ký tự).
4. Gọi binary `piper` cho từng chunk.
5. Nối các file WAV chunk thành 1 file WAV cuối.
6. Ghi log ra console và file `piper_subprocess.log`.

## 2) Cấu trúc quan trọng

- Script chính: `main.py`
- Binary Piper mặc định: `piper/piper`
- Model mặc định: `models/ngocngan3701.onnx`
- Log file: `piper_subprocess.log`

## 3) Yêu cầu

- Python 3.8+
- Có sẵn binary `piper` tương thích hệ điều hành/kiến trúc máy
- Có model `.onnx` và file cấu hình `.onnx.json`

> Lưu ý: repo này đang đặt sẵn model và binary ở đường dẫn mặc định, nhưng bạn vẫn có thể override bằng tham số dòng lệnh.

## 4) Cách chạy nhanh

Từ thư mục gốc project:

```bash
python3 main.py "Xin chào, đây là bản demo chuyển văn bản tiếng Việt thành giọng nói."
```

Nếu không truyền `-o`, script tự sinh tên file theo timestamp dạng `tts_YYYYMMDD_HHMMSS.wav`.

## 5) Cú pháp đầy đủ

```bash
python3 main.py "<noi_dung_van_ban>" \
  -o output.wav \
  -m models/ngocngan3701.onnx \
  -b piper/piper \
  --chunk-size 150
```

### Tham số

- `text` (bắt buộc): nội dung cần đọc.
- `-o, --output`: tên file WAV đầu ra.
- `-m, --model`: đường dẫn model `.onnx`.
- `-b, --binary`: đường dẫn binary `piper`.
- `--chunk-size`: độ dài tối đa mỗi chunk trước khi gọi Piper.

## 6) Ví dụ thực tế

### Ví dụ 1: Xuất file theo tên chỉ định

```bash
python3 main.py "Nhiệt độ hiện tại là 25.8°C và độ ẩm 51.6%." -o weather.wav
```

Script sẽ chuẩn hóa text thành cách đọc thân thiện hơn (ví dụ `25,8 độ C`, `51,6 phần trăm`) trước khi tổng hợp giọng nói.

### Ví dụ 2: Đổi model khác

```bash
python3 main.py "Xin chào bạn" \
  -m models/deepman3909.onnx \
  -o deepman.wav
```

### Ví dụ 3: Chia chunk ngắn hơn

```bash
python3 main.py "<đoạn văn dài>" --chunk-size 100 -o long_text.wav
```

## 7) Cách script xử lý text tiếng Việt

Trong hàm `normalize_vietnamese_text`, script hiện xử lý:

- Gộp nhiều khoảng trắng liên tiếp thành 1 khoảng trắng.
- Đổi `#` thành `.` để ngắt câu tự nhiên.
- Đổi số thập phân `%`: `51.6%` -> `51,6 phần trăm`.
- Đổi số thập phân `°C`: `25.8°C` -> `25,8 độ C`.
- Đổi số thập phân khác từ `.` sang `,` để đọc đúng kiểu tiếng Việt.

## 8) Cách script chia chunk

Hàm `split_text_smart` chia văn bản theo thứ tự ưu tiên:

1. Dấu câu chính: `. ! ? : ;`
2. Nếu vẫn quá dài, chia tiếp theo `, ;`
3. Nếu vẫn quá dài, tách theo từ

Mục tiêu là giảm lỗi khi đưa một câu quá dài vào Piper.

## 9) Log và xử lý lỗi

- Console hiển thị tiến trình từng chunk.
- File `piper_subprocess.log` lưu toàn bộ log.
- Các lỗi phổ biến:
  - Sai đường dẫn `--binary` hoặc `--model`
  - Binary không có quyền chạy (`chmod +x`)
  - Binary không đúng kiến trúc (vd: ARM binary chạy trên x86_64 sẽ báo `Exec format error`)

## 10) Troubleshooting nhanh

### Lỗi `Piper binary not found`

Kiểm tra đúng path và quyền truy cập:

```bash
ls -l piper/piper
```

### Lỗi `Exec format error`

Binary không tương thích kiến trúc CPU hiện tại. Hãy dùng bản Piper đúng kiến trúc máy của bạn.

### Không có file WAV đầu ra

Mở `piper_subprocess.log` để xem chunk nào bị fail và stderr chi tiết từ Piper.

## 11) Gợi ý cải tiến (nếu bạn muốn mở rộng)

- Thêm chế độ đọc text từ file (`--input-file`).
- Thêm đầu ra MP3 (qua ffmpeg).
- Chạy song song chunk để tăng tốc (cần kiểm soát RAM/CPU).
- Thêm bộ quy tắc chuẩn hóa cho ngày tháng, tiền tệ, URL, viết tắt.
