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

TEXT = "Tóm tắt môi trường: Nhận xét: Hiện tại, chất lượng không khí trong văn phòng khá ổn định với độ ẩm phù hợp (51.6%) và chỉ số CO2 ở mức chấp nhận được (487 ppm). Nhiệt độ cũng nằm trong khoảng thuận lợi cho sức khỏe (25.8°C). #Lời khuyên: Tuy nhiên, để duy trì không khí trong lành và nâng cao hiệu suất làm việc, bạn có thể tăng cường thông gió tự nhiên bằng cách mở cửa sổ vào những lúc không quá nóng hoặc lạnh. Ngoài ra, định kỳ kiểm tra và vệ sinh hệ thống lọc không khí cũng rất quan trọng. Hy vọng lời khuyên này sẽ hữu ích cho môi trường làm việc của bạn!"

# ================= LOG =================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("piper_subprocess_debug.log", mode="w")
    ]
)
log = logging.getLogger("PIPER_DEBUG")


def run_piper(text: str, out_wav: str, idx: int):
    log.info("START subprocess #%d", idx)

    cmd = [
        PIPER_BIN,
        "--model", MODEL_PATH,
        "--output_file", out_wav
    ]

    start = time.time()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        stdout, stderr = proc.communicate(text, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        log.error("TIMEOUT subprocess #%d", idx)
        return False

    elapsed = time.time() - start

    log.info(
        "END subprocess #%d | returncode=%s | time=%.2fs",
        idx, proc.returncode, elapsed
    )

    if stdout:
        log.debug("STDOUT #%d:\n%s", idx, stdout)

    if stderr:
        log.warning("STDERR #%d:\n%s", idx, stderr)

    if proc.returncode != 0:
        log.error("Piper failed at chunk #%d", idx)
        return False

    if not os.path.exists(out_wav):
        log.error("WAV not created at chunk #%d", idx)
        return False

    log.info(
        "Chunk #%d OK (%d KB)",
        idx, os.path.getsize(out_wav) // 1024
    )
    return True


def concat_wavs(wav_files, output_wav):
    log.info("Concatenating %d wav files", len(wav_files))

    with wave.open(wav_files[0], "rb") as wf:
        params = wf.getparams()

    with wave.open(output_wav, "wb") as out:
        out.setparams(params)

        for i, wf_path in enumerate(wav_files):
            with wave.open(wf_path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                out.writeframes(frames)

            log.debug("Appended wav #%d", i)

    log.info("FINAL WAV created: %s", output_wav)


def split_text(text, max_len=180):
    
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

def main():
    log.info("=== START PIPER MULTI SUBPROCESS TEST ===")

    if not os.path.isfile(PIPER_BIN):
        log.error("Piper binary not found")
        return

    chunks = split_text(TEXT)
    log.info("Split text into %d chunks", len(chunks))

    tmpdir = tempfile.mkdtemp(prefix="piper_chunks_")
    wavs = []

    try:
        for i, t in enumerate(chunks):
            wav_path = os.path.join(tmpdir, f"chunk_{i}.wav")
            ok = run_piper(t, wav_path, i)
            if not ok:
                log.error("ABORT at chunk #%d", i)
                return
            wavs.append(wav_path)

        concat_wavs(wavs, FINAL_WAV)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        log.info("Cleaned temp dir")

    log.info("=== DONE ===")


if __name__ == "__main__":
    main()