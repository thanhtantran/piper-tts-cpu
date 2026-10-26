import argparse
import gc
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime

# ================= CONFIG =================
PIPER_BIN = "piper/piper"
MODEL_PATH = "models/ngocngan3701.onnx"

# Keep English brand names as-is so they are read in English instead of being
# incorrectly normalized as Vietnamese text.
ENGLISH_PROPER_NOUNS = [
    "Rockchip",
    "Orange Pi",
    "Raspberry Pi",
    "Raspberry",
    "Orange Pi OS",
    "Ubuntu",
    "Debian",
    "Fedora",
    "ARM",
    "x86_64",
    "Intel",
    "AMD",
    "NVIDIA",
]

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("piper_subprocess.log", mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("PIPER_TTS")


def preserve_english_proper_nouns(text):
    """Keep well-known English brand names untouched during normalization."""
    replacements = {}
    for index, name in enumerate(ENGLISH_PROPER_NOUNS):
        token = f"__ENG_NAME_{index}__"
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
        text = pattern.sub(token, text)
        replacements[token] = name
    return text, replacements


def normalize_vietnamese_text(text):
    """Normalize Vietnamese text for TTS while preserving English brand names."""
    log.info("Normalizing Vietnamese text (%d chars)", len(text))

    text, replacements = preserve_english_proper_nouns(text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text.strip())

    # Replace # with a natural sentence terminator
    text = text.replace("#", ".")

    # Decimal values: 51.6% -> 51,6 phần trăm
    text = re.sub(r"(\d+)\.(\d+)%", r"\1,\2 phần trăm", text)

    # Temperature: 25.8°C -> 25,8 độ C
    text = re.sub(r"(\d+)\.(\d+)°C", r"\1,\2 độ C", text)

    # Other decimal numbers: 3.14 -> 3,14
    text = re.sub(r"(\d+)\.(\d+)(?!\s*(phần trăm|độ C))", r"\1,\2", text)

    # Restore English brand names after all Vietnamese-specific normalization.
    for token, name in replacements.items():
        text = text.replace(token, name)

    log.debug("Normalized text: %s", text[:100] + "..." if len(text) > 100 else text)
    return text


def split_text_smart(text, max_len=150):
    """Split text smartly into chunks based on sentence punctuation and length."""
    log.info("Splitting text into chunks (max_len=%d)", max_len)

    text = normalize_vietnamese_text(text)
    sentences = re.split(r"([.!?:;])", text)

    chunks = []
    current = ""

    i = 0
    while i < len(sentences):
        segment = sentences[i].strip()
        punct = sentences[i + 1].strip() if i + 1 < len(sentences) else ""

        if not segment:
            i += 2
            continue

        if len(segment) > max_len:
            sub_parts = re.split(r"([,;])", segment)
            for j in range(0, len(sub_parts), 2):
                sub_segment = sub_parts[j].strip()
                sub_punct = sub_parts[j + 1].strip() if j + 1 < len(sub_parts) else ""

                if len(sub_segment) > max_len:
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

    chunks = [c.strip() for c in chunks if len(c.strip()) > 3]

    log.info("Split into %d chunks", len(chunks))
    for i, chunk in enumerate(chunks):
        log.debug("Chunk #%d (%d chars): %s...", i, len(chunk), chunk[:50])

    return chunks


def run_piper(text: str, out_wav: str, idx: int):
    """Run the Piper binary for a single chunk."""
    log.info("Processing chunk #%d (%d chars)", idx, len(text))

    cmd = [PIPER_BIN, "--model", MODEL_PATH, "--output_file", out_wav]
    start = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(text, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        log.error("TIMEOUT at chunk #%d", idx)
        return False
    except Exception as exc:
        log.error("Error running Piper at chunk #%d: %s", idx, str(exc))
        return False

    elapsed = time.time() - start
    log.info("Chunk #%d completed | returncode=%s | time=%.2fs", idx, proc.returncode, elapsed)

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
    """Concatenate multiple WAV files into one final WAV."""
    log.info("Concatenating %d WAV files", len(wav_files))

    if not wav_files:
        log.error("No WAV files to concatenate")
        return False

    try:
        with wave.open(wav_files[0], "rb") as wf:
            params = wf.getparams()
            log.debug(
                "WAV params: channels=%d, width=%d, rate=%d",
                params.nchannels,
                params.sampwidth,
                params.framerate,
            )

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
                except Exception as exc:
                    log.error("Error appending WAV #%d: %s", i, str(exc))
                    return False

        size = os.path.getsize(output_wav)
        duration = total_frames / params.framerate

        log.info("Final WAV created: %s (%d KB, %.2f seconds)", output_wav, size // 1024, duration)
        return True

    except Exception as exc:
        log.error("Concatenation error: %s", str(exc))
        return False


def generate_output_filename():
    """Create a default output filename based on the current timestamp."""
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

    if not args.output:
        args.output = generate_output_filename()

    log.info("=== PIPER TTS VIETNAMESE ===")
    log.info("Text: %s", args.text[:100] + "..." if len(args.text) > 100 else args.text)
    log.info("Output: %s", args.output)
    log.info("Model: %s", args.model)
    log.info("Binary: %s", args.binary)

    if not os.path.isfile(args.binary):
        log.error("Piper binary not found: %s", args.binary)
        return 1

    if not os.path.isfile(args.model):
        log.error("Model file not found: %s", args.model)
        return 1

    chunks = split_text_smart(args.text, args.chunk_size)

    if not chunks:
        log.error("No valid chunks created from text")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="piper_chunks_")
    wav_files = []

    try:
        for i, chunk_text in enumerate(chunks):
            wav_path = os.path.join(tmpdir, f"chunk_{i:03d}.wav")

            success = run_piper(chunk_text, wav_path, i)
            if not success:
                log.error("ABORT: Failed at chunk #%d", i)
                return 1

            wav_files.append(wav_path)

            if i < len(chunks) - 1:
                time.sleep(0.2)

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
    except Exception as exc:
        log.error("Fatal error: %s", str(exc), exc_info=True)
        return 1
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
            log.info("Cleaned temporary directory")
        except Exception:
            pass
        gc.collect()


if __name__ == "__main__":
    sys.exit(main())
