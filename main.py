import subprocess
import os
import sys
import logging
import tempfile
import time
import wave
import shutil
import gc
import re
from pathlib import Path

# ================= CONFIG =================
PIPER_BIN = "piper/piper"
MODEL_PATH = "models/ngocngan3701.onnx"
FINAL_WAV = "final_ngocngan.wav"

# Chunk size nhỏ hơn nhiều
MAX_CHUNK_LENGTH = 80  # Giảm xuống 80 ký tự
DELAY_BETWEEN_CHUNKS = 0.5  # Tăng delay lên 0.5s

TEXT = "Tóm tắt môi trường: Nhận xét về chất lượng không khí và môi trường làm việc: Hiện tại, môi trường làm việc của bạn khá ổn định với nhiệt độ 25.3°C, độ ẩm 49.3% và áp suất không khí là 1021.9 hPa. Tuy nhiên, chỉ số CO2 ở mức 448 ppm có thể cho thấy rằng không gian làm việc hơi ẩm ướt, có thể gây khó chịu cho người dùng trong thời gian dài. # Lời khuyên: Để cải thiện chất lượng không khí và tạo cảm giác thoải mái hơn, bạn có thể cân nhắc một số biện pháp như mở cửa sổ để thông thoáng không khí, hoặc sử dụng máy lọc không khí nếu không khí làm việc bị ẩm thấp. Ngoài ra, khuyến khích nhân viên thường xuyên đứng dậy đi lại, hít thở không khí bên ngoài để giảm thiểu tình trạng tăng CO2 trong phòng."

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("piper_subprocess_debug.log", mode="w", encoding="utf-8")
    ]
)
log = logging.getLogger("PIPER_DEBUG")


def cleanup_process(proc):
    """Cleanup subprocess một cách an toàn"""
    if not proc:
        return
    
    try:
        if proc.poll() is None:  # Vẫn đang chạy
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        
        # Đóng tất cả streams
        for stream in [proc.stdin, proc.stdout, proc.stderr]:
            if stream:
                try:
                    stream.close()
                except:
                    pass
    except:
        pass


def run_piper_safe(text: str, out_wav: str, idx: int, max_retries=2):
    """
    Chạy Piper với retry logic và cleanup tốt hơn
    """
    for attempt in range(max_retries):
        if attempt > 0:
            log.warning("Retry #%d for chunk #%d", attempt, idx)
            time.sleep(1)  # Đợi lâu hơn trước khi retry
        
        success = _run_piper_isolated(text, out_wav, idx, attempt)
        
        if success:
            return True
        
        # Cleanup nếu fail
        try:
            if os.path.exists(out_wav):
                os.remove(out_wav)
        except:
            pass
        
        gc.collect()
        time.sleep(0.5)
    
    return False


def _run_piper_isolated(text: str, out_wav: str, idx: int, apptempt: int):
    """Chạy Piper qua shell script để isolate hoàn toàn"""
    
    # Tạo temp file cho input text
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name
    
    try:
        # Chạy qua bash với ulimit
        cmd = f'''
        ulimit -v 524288 && \
        ulimit -t 30 && \
        cat "{text_file}" | "{PIPER_BIN}" --model "{MODEL_PATH}" --output_file "{out_wav}"
        '''
        
        result = subprocess.run(
            ['bash', '-c', cmd],
            capture_output=True,
            text=True,
            timeout=40
        )
        
        return result.returncode == 0 and os.path.exists(out_wav)
        
    finally:
        try:
            os.remove(text_file)
        except:
            pass

            
def split_text_smart(text, max_len=80):
    """
    Chia text thông minh theo câu ngắn
    """
    # Chuẩn hóa text
    text = re.sub(r'\s+', ' ', text.strip())
    text = text.replace('#', '.')  # Thay # thành dấu câu
    
    # Chia theo các dấu câu
    parts = re.split(r'([.!?:,;])', text)
    
    chunks = []
    current = ""
    
    i = 0
    while i < len(parts):
        segment = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        
        # Nếu segment quá dài, chia nhỏ hơn nữa
        if len(segment) > max_len:
            # Chia theo từ
            words = segment.split()
            temp = ""
            for word in words:
                if len(temp) + len(word) + 1 <= max_len:
                    temp += word + " "
                else:
                    if temp:
                        chunks.append(temp.strip())
                    temp = word + " "
            if temp:
                current = temp
        else:
            test = current + segment + punct
            if len(test) <= max_len:
                current = test + " "
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = segment + punct + " "
        
        i += 2
    
    if current.strip():
        chunks.append(current.strip())
    
    # Lọc chunks rỗng và quá ngắn
    chunks = [c.strip() for c in chunks if len(c.strip()) > 3]
    
    return chunks


def concat_wavs_efficient(wav_files, output_wav):
    """Nối WAV files hiệu quả với streaming"""
    log.info("Concatenating %d files", len(wav_files))
    
    if not wav_files:
        return False
    
    try:
        # Đọc params
        with wave.open(wav_files[0], "rb") as wf:
            params = wf.getparams()
        
        # Tạo output
        with wave.open(output_wav, "wb") as out_wf:
            out_wf.setparams(params)
            
            # Nối từng file
            for i, wav_path in enumerate(wav_files):
                try:
                    with wave.open(wav_path, "rb") as in_wf:
                        # Đọc và ghi theo chunks để tránh load hết vào RAM
                        chunk_size = 1024
                        while True:
                            frames = in_wf.readframes(chunk_size)
                            if not frames:
                                break
                            out_wf.writeframes(frames)
                    
                    log.debug("Appended #%d", i)
                    
                    # Xóa file ngay
                    os.remove(wav_path)
                    
                except Exception as e:
                    log.error("Error appending file #%d: %s", i, str(e))
                    return False
        
        size = os.path.getsize(output_wav)
        log.info("FINAL WAV: %s (%d KB)", output_wav, size // 1024)
        return True
        
    except Exception as e:
        log.error("Concat error: %s", str(e))
        return False
    finally:
        gc.collect()


def main():
    log.info("=== START PIPER TTS ===")
    
    # Validate
    if not os.path.isfile(PIPER_BIN):
        log.error("Piper not found: %s", PIPER_BIN)
        return 1
    
    if not os.path.isfile(MODEL_PATH):
        log.error("Model not found: %s", MODEL_PATH)
        return 1
    
    # Split text
    chunks = split_text_smart(TEXT, max_len=MAX_CHUNK_LENGTH)
    log.info("Split into %d chunks", len(chunks))
    
    for i, chunk in enumerate(chunks):
        log.debug("Chunk #%d (%d chars): %s", i, len(chunk), chunk[:60])
    
    # Create temp dir
    tmpdir = tempfile.mkdtemp(prefix="piper_")
    log.info("Temp: %s", tmpdir)
    
    wavs = []
    success = True
    
    try:
        # Process chunks
        for i, chunk_text in enumerate(chunks):
            wav_path = os.path.join(tmpdir, f"chunk_{i:03d}.wav")
            
            log.info("Processing chunk %d/%d", i + 1, len(chunks))
            
            ok = run_piper_safe(chunk_text, wav_path, i)
            
            if not ok:
                log.error("FAILED at chunk #%d", i)
                success = False
                break
            
            wavs.append(wav_path)
            
            # Delay giữa các chunks
            if i < len(chunks) - 1:
                time.sleep(DELAY_BETWEEN_CHUNKS)
        
        # Concat
        if success and wavs:
            log.info("Concatenating...")
            success = concat_wavs_efficient(wavs, FINAL_WAV)
        
    except KeyboardInterrupt:
        log.warning("Interrupted")
        success = False
    except Exception as e:
        log.error("Error: %s", str(e), exc_info=True)
        success = False
    finally:
        # Cleanup
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
            log.info("Cleaned temp")
        except:
            pass
        
        gc.collect()
    
    if success:
        log.info("=== SUCCESS ===")
        return 0
    else:
        log.error("=== FAILED ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())