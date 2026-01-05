import subprocess
import os
import sys
import logging
import tempfile
import time
import wave
import shutil

# ================= CONFIG =================
PIPER_BIN = "piper/piper"
MODEL_PATH = "models/ngocngan3701.onnx"
FINAL_WAV = "final_ngocngan.wav"

TEXT = "Tóm tắt môi trường: Nhận xét về chất lượng không khí và môi trường làm việc: Hiện tại, môi trường làm việc của bạn khá ổn định với nhiệt độ 25.3°C, độ ẩm 49.3% và áp suất không khí là 1021.9 hPa. Tuy nhiên, chỉ số CO2 ở mức 448 ppm có thể cho thấy rằng không gian làm việc hơi ẩm ướt, có thể gây khó chịu cho người dùng trong thời gian dài. # Lời khuyên: Để cải thiện chất lượng không khí và tạo cảm giác thoải mái hơn, bạn có thể cân nhắc một số biện pháp như mở cửa sổ để thông thoáng không khí, hoặc sử dụng máy lọc không khí nếu không khí làm việc bị ẩm thấp. Ngoài ra, khuyến khích nhân viên thường xuyên đứng dậy đi lại, hít thở không khí bên ngoài để giảm thiểu tình trạng tăng CO2 trong phòng. "

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
    sentences = []
    buf = ""

    for part in text.split(","):
        if len(buf) + len(part) < max_len:
            buf += part + ","
        else:
            sentences.append(buf.strip(", "))
            buf = part + ","

    if buf:
        sentences.append(buf.strip(", "))

    return sentences


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