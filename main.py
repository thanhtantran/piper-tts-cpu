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
import argparse
from datetime import datetime
from pathlib import Path

# ================= CONFIG =================
PIPER_BIN = "piper/piper"
MODEL_PATH = "models/ngocngan3701.onnx"

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("piper_subprocess.log", mode="w", encoding="utf-8")
    ]
)
log = logging.getLogger("PIPER_TTS")


def normalize_vietnamese_text(text):
    """
    Chuẩn hóa text tiếng Việt cho TTS
    """
    log.info("Normalizing Vietnamese text (%d chars)", len(text))
    
    # Chuẩn hóa khoảng trắng
    text = re.sub(r'\s+', ' ', text.strip())
    
    # Thay # thành dấu câu
    text = text.replace('#', '.')
    
    # Xử lý số thập phân cho độ ẩm và nhiệt độ
    # Độ ẩm: 51.6% -> 51,6 phần trăm
    text = re.sub(r'(\d+)\.(\d+)%', r'\1,\2 phần trăm', text)
    
    # Nhiệt độ: 25.8°C -> 25,8 độ C
    text = re.sub(r'(\d+)\.(\d+)°C', r'\1,\2 độ C', text)
    
    # Xử lý các số thập phân khác (không phải % hay °C)
    text = re.sub(r'(\d+)\.(\d+)(?!\s*(phần trăm|độ C))', r'\1,\2', text)
    
    log.debug("Normalized text: %s", text[:100] + "..." if len(text) > 100 else text)
    return text


def split_text_smart(text, max_len=150):
    """
    Chia text thông minh theo câu và độ dài phù hợp
    """
    log.info("Splitting text into chunks (max_len=%d)", max_len)
    
    # Chuẩn hóa trước
    text = normalize_vietnamese_text(text)
    
    # Chia theo các dấu câu chính
    sentences = re.split(r'([.!?:;])', text)
    
    chunks = []
    current = ""
    
    i = 0
    while i < len(sentences):
        segment = sentences[i].strip()
        punct = sentences[i + 1].strip() if i + 1 < len(sentences) else ""
        
        if not segment:  # Skip empty segments
            i += 2
            continue
        
        # Nếu segment quá dài, chia nhỏ hơn
        if len(segment) > max_len:
            # Chia theo dấu phẩy trước
            sub_parts = re.split(r'([,;])', segment)
            for j in range(0, len(sub_parts), 2):
                sub_segment = sub_parts[j].strip()
                sub_punct = sub_parts[j + 1].strip() if j + 1 < len(sub_parts) else ""
                
                if len(sub_segment) > max_len:
                    # Chia theo từ
                    words = sub_segment.split()
                    temp = ""
                    for word in words:
                        if len(temp + " " + word) <= max_len:
                            temp += (" " + word) if temp else word
                        else:
                            if temp:
                                chunks.append(temp.strip())
                            temp = word
                    if temp:
                        current = temp + sub_punct + " "
                else:
                    test = current + sub_segment + sub_punct
                    if len(test) <= max_len:
                        current = test + " "
                    else:
                        if current.strip():
                            chunks.append(current.strip())
                        current = sub_segment + sub_punct + " "
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
    
    log.info("Split into %d chunks", len(chunks))
    for i, chunk in enumerate(chunks):
        log.debug("Chunk #%d (%d chars): %s...", i, len(chunk), chunk[:50])
    
    return chunks


def run_piper(text: str, out_wav: str, idx: int):
    """
    Chạy piper binary để synthesize một chunk
    """
    log.info("Processing chunk #%d (%d chars)", idx, len(text))

    cmd = [
        PIPER_BIN,
        "--model", MODEL_PATH,
        "--output_file", out_wav
    ]

    start = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = proc.communicate(text, timeout=120)
        
    except subprocess.TimeoutExpired:
        proc.kill()
        log.error("TIMEOUT at chunk #%d", idx)
        return False
    except Exception as e:
        log.error("Error running piper at chunk #%d: %s", idx, str(e))
        return False

    elapsed = time.time() - start

    log.info("Chunk #%d completed | returncode=%s | time=%.2fs", 
             idx, proc.returncode, elapsed)

    if stdout.strip():
        log.debug("STDOUT #%d: %s", idx, stdout.strip())

    if stderr.strip():
        log.warning("STDERR #%d: %s", idx, stderr.strip())

    if proc.returncode != 0:
        log.error("Piper failed at chunk #%d (returncode=%d)", idx, proc.returncode)
        return False

    if not os.path.exists(out_wav):
        log.error("WAV file not created at chunk #%d", idx)
        return False

    size = os.path.getsize(out_wav)
    if size == 0:
        log.error("Empty WAV file at chunk #%d", idx)
        return False

    log.info("Chunk #%d SUCCESS (%d KB)", idx, size // 1024)
    return True


def concat_wavs(wav_files, output_wav):
    """
    Nối các file WAV thành một file cuối cùng
    """
    log.info("Concatenating %d WAV files", len(wav_files))

    if not wav_files:
        log.error("No WAV files to concatenate")
        return False

    try:
        # Đọc parameters từ file đầu tiên
        with wave.open(wav_files[0], "rb") as wf:
            params = wf.getparams()
            log.debug("WAV params: channels=%d, width=%d, rate=%d", 
                     params.nchannels, params.sampwidth, params.framerate)

        # Tạo file output
        with wave.open(output_wav, "wb") as out_wf:
            out_wf.setparams(params)

            total_frames = 0
            for i, wav_path in enumerate(wav_files):
                try:
                    with wave.open(wav_path, "rb") as in_wf:
                        frames = in_wf.readframes(in_wf.getnframes())
                        out_wf.writeframes(frames)
                        total_frames += in_wf.getnframes()
                    
                    log.debug("Appended WAV #%d (%d bytes)", i, len(frames))
                    
                except Exception as e:
                    log.error("Error appending WAV #%d: %s", i, str(e))
                    return False

        size = os.path.getsize(output_wav)
        duration = total_frames / params.framerate
        
        log.info("Final WAV created: %s (%d KB, %.2f seconds)", 
                output_wav, size // 1024, duration)
        return True

    except Exception as e:
        log.error("Concatenation error: %s", str(e))
        return False


def generate_output_filename():
    """
    Tạo tên file output theo timestamp
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"tts_{timestamp}.wav"


def main():
    parser = argparse.ArgumentParser(description="Piper TTS - Vietnamese Text to Speech")
    parser.add_argument("text", help="Text to synthesize")
    parser.add_argument("-o", "--output", help="Output WAV file (default: auto-generated with timestamp)")
    parser.add_argument("-m", "--model", default=MODEL_PATH, help="Piper model path")
    parser.add_argument("-b", "--binary", default=PIPER_BIN, help="Piper binary path")
    parser.add_argument("--chunk-size", type=int, default=150, help="Max chunk size")
    
    args = parser.parse_args()
    
    # Tạo tên file output nếu không được chỉ định
    if not args.output:
        args.output = generate_output_filename()
    
    log.info("=== PIPER TTS VIETNAMESE ===")
    log.info("Text: %s", args.text[:100] + "..." if len(args.text) > 100 else args.text)
    log.info("Output: %s", args.output)
    log.info("Model: %s", args.model)
    log.info("Binary: %s", args.binary)

    # Kiểm tra files
    if not os.path.isfile(args.binary):
        log.error("Piper binary not found: %s", args.binary)
        return 1

    if not os.path.isfile(args.model):
        log.error("Model file not found: %s", args.model)
        return 1

    # Chia text thành chunks
    chunks = split_text_smart(args.text, args.chunk_size)
    
    if not chunks:
        log.error("No valid chunks created from text")
        return 1

    # Tạo thư mục tạm
    tmpdir = tempfile.mkdtemp(prefix="piper_chunks_")
    wav_files = []

    try:
        # Xử lý từng chunk
        for i, chunk_text in enumerate(chunks):
            wav_path = os.path.join(tmpdir, f"chunk_{i:03d}.wav")
            
            success = run_piper(chunk_text, wav_path, i)
            if not success:
                log.error("ABORT: Failed at chunk #%d", i)
                return 1
            
            wav_files.append(wav_path)
            
            # Nghỉ ngắn giữa các chunk
            if i < len(chunks) - 1:
                time.sleep(0.2)

        # Nối các file WAV
        success = concat_wavs(wav_files, args.output)
        if not success:
            log.error("ABORT: Failed to concatenate WAV files")
            return 1

        log.info("=== SUCCESS ===")
        log.info("Final output: %s", args.output)
        return 0

    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        return 1
    except Exception as e:
        log.error("Fatal error: %s", str(e), exc_info=True)
        return 1
    finally:
        # Dọn dẹp
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
            log.info("Cleaned temporary directory")
        except:
            pass
        gc.collect()


if __name__ == "__main__":
    sys.exit(main())
