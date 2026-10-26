# Piper TTS CPU

Dự án này chuyển văn bản tiếng Việt thành file âm thanh WAV bằng Piper trên các thiết bị ARM như Rockchip, Orange Pi, Raspberry Pi, v.v.

Có 2 cách chạy:
- CLI: `main.py`
- Flask web app: `app.py`

Các tên riêng bằng tiếng Anh như Rockchip, Orange Pi, Raspberry Pi sẽ được giữ nguyên để Piper đọc đúng theo cách đọc tiếng Anh, không bị biến thành văn bản Việt Nam.

## 1) Yêu cầu

- Python 3.8+
- Binary Piper tương thích với kiến trúc máy của bạn (ARM64, x86_64, ...)
- Có ít nhất một file model `.onnx` trong thư mục `models/`

Cài đặt thư viện Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Chạy CLI

Từ thư mục gốc project:

```bash
python3 main.py "Xin chào, đây là demo chuyển văn bản tiếng Việt thành giọng nói trên Orange Pi."
```

Ví dụ với tên riêng tiếng Anh:

```bash
python3 main.py "Rockchip và Orange Pi là các nền tảng ARM phổ biến. Raspberry Pi rất phổ biến cho học tập và prototype." -o demo.wav
```

Tùy chọn:

```bash
python3 main.py "Raspberry Pi và Rockchip là các board ARM phổ biến." \
  -o output.wav \
  -m models/ngocngan3701.onnx \
  -b piper/piper \
  --chunk-size 150
```

## 3) Chạy Flask web app

Khởi động Flask app:

```bash
python3 app.py --host 0.0.0.0 --port 5000
```

Mở trình duyệt:

```text
http://<IP-cua-thiet-bi>:5000
```

Các endpoint:

- `GET /api/models` — liệt kê model có sẵn
- `POST /api/submit` — gửi văn bản để tạo âm thanh
- `GET /api/status` — xem tiến độ công việc hiện tại
- `GET /api/log` — xem log gần nhất
- `GET /output/<filename>` — tải file WAV đã tạo

Ví dụ gọi API:

```bash
curl -X POST http://127.0.0.1:5000/api/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "Orange Pi và Raspberry Pi là các board phát triển phổ biến.", "model": "ngocngan3701.onnx"}'
```

## 4) Cài đặt service để app chạy khi khởi động

Tạo service:

```bash
sudo nano /etc/systemd/system/piper-tts-web.service
```

Nội dung:

```ini
[Unit]
Description=Piper TTS Flask Web App
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/pi/piper-tts-cpu
ExecStart=/usr/bin/python3 /home/pi/piper-tts-cpu/app.py --host 0.0.0.0 --port 5000
Restart=always
RestartSec=3
User=pi
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Nếu project của bạn nằm ở đường dẫn khác, thay `/home/pi/piper-tts-cpu` bằng đúng đường dẫn thực tế.

Khởi động service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable piper-tts-web.service
sudo systemctl start piper-tts-web.service
sudo systemctl status piper-tts-web.service
```

Xem log service:

```bash
journalctl -u piper-tts-web.service -f
```

## 5) Xử lý lỗi nhanh

### Piper binary not found

```bash
ls -l piper/piper
chmod +x piper/piper
```

### Exec format error

Binary Piper không đúng kiến trúc CPU. Dùng phiên bản tương thích với ARM64 hoặc x86_64 của máy bạn.

### Không có file WAV đầu ra

Kiểm tra log:

```bash
tail -f piper_subprocess.log
```

## 6) Ghi chú

- `main.py` và `app.py` đều đã được cập nhật để giữ nguyên các tên thương hiệu tiếng Anh.
- Nếu muốn thêm tên riêng khác, hãy bổ sung vào danh sách `ENGLISH_PROPER_NOUNS` ở cả 2 file.
