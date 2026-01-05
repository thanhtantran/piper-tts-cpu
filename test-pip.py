import wave
import logging
import sys
import os
import gc
import re
import time
import io
from pathlib import Path
from piper import PiperVoice

# ================= CONFIG =================
MODEL_PATH = "models/ngocngan3701.onnx"
FINAL_WAV = "final_ngocngan.wav"

TEXT = "Tóm tắt môi trường: Nhận xét về chất lượng không khí và môi trường làm việc: Hiện tại, môi trường làm việc của bạn khá ổn định với nhiệt độ 25.3°C, độ ẩm 49.3% và áp suất không khí là 1021.9 hPa. Tuy nhiên, chỉ số CO2 ở mức 448 ppm có thể cho thấy rằng không gian làm việc hơi ẩm ướt, có thể gây khó chịu cho người dùng trong thời gian dài. # Lời khuyên: Để cải thiện chất lượng không khí và tạo cảm giác thoải mái hơn, bạn có thể cân nhắc một số biện pháp như mở cửa sổ để thông thoáng không khí, hoặc sử dụng máy lọc không khí nếu không khí làm việc bị ẩm thấp. Ngoài ra, khuyến khích nhân viên thường xuyên đứng dậy đi lại, hít thở không khí bên ngoài để giảm thiểu tình trạng tăng CO2 trong phòng."

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("piper_tts.log", mode="w", encoding="utf-8")
    ]
)
log = logging.getLogger("PIPER_TTS")


def normalize_text(text):
    """Chuẩn hóa text trước khi xử lý"""
    text = re.sub(r'\s+', ' ', text.strip())
    text = text.replace('#', '.')
    text = text.replace('°C', ' độ C')
    return text


def synthesize_to_wav(voice, text, output_wav):
    """Synthesize text thành WAV file - dùng BytesIO buffer"""
    log.info("Synthesizing text (%d chars)", len(text))
    
    try:
        start = time.time()
        
        # Tạo buffer trong memory
        buffer = io.BytesIO()
        
        # Tạo WAV file trong buffer
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            
            # Synthesize vào buffer
            voice.synthesize(text, wav_file)
        
        # Ghi buffer ra file
        buffer.seek(0)
        with open(output_wav, "wb") as f:
            f.write(buffer.read())
        
        buffer.close()
        
        elapsed = time.time() - start
        size = os.path.getsize(output_wav)
        
        log.info("Synthesis completed in %.2fs (%d KB)", elapsed, size // 1024)
        return True
        
    except Exception as e:
        log.error("Synthesis error: %s", str(e), exc_info=True)
        return False
    finally:
        gc.collect()


def split_text_smart(text, max_len=200):
    """Chia text theo câu"""
    text = re.sub(r'\s+', ' ', text.strip())
    text = text.replace('#', '.')
    
    sentences = re.split(r'([.!?:,;])', text)
    
    chunks = []
    current = ""
    
    i = 0
    while i < len(sentences):
        segment = sentences[i]
        punct = sentences[i + 1] if i + 1 < len(sentences) else ""
        
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
    
    return [c.strip() for c in chunks if len(c.strip()) > 3]


def concat_wavs(wav_files, output_wav):
    """Nối các WAV files thành một file cuối cùng"""
    log.info("Concatenating %d WAV files", len(wav_files))
    
    if not wav_files:
        log.error("No WAV files to concatenate")
        return False
    
    try:
        # Đọc params từ file đầu tiên
        with wave.open(wav_files[0], "rb") as wf:
            params = wf.getparams()
            log.debug("WAV params: channels=%d, width=%d, rate=%d", 
                     params.nchannels, params.sampwidth, params.framerate)
        
        # Tạo output file
        with wave.open(output_wav, "wb") as out_wf:
            out_wf.setparams(params)
            
            # Nối từng file
            for i, wav_path in enumerate(wav_files):
                try:
                    with wave.open(wav_path, "rb") as in_wf:
                        frames = in_wf.readframes(in_wf.getnframes())
                        out_wf.writeframes(frames)
                    
                    log.debug("Appended WAV #%d (%d bytes)", i, len(frames))
                    
                    # Xóa file tạm
                    os.remove(wav_path)
                    
                except Exception as e:
                    log.error("Error appending WAV #%d: %s", i, str(e))
                    return False
        
        size = os.path.getsize(output_wav)
        log.info("Final WAV created: %s (%d KB)", output_wav, size // 1024)
        
        # Verify final WAV
        try:
            with wave.open(output_wav, "rb") as wf:
                log.info("Final WAV verified: %d frames, %.2f seconds", 
                        wf.getnframes(), 
                        wf.getnframes() / wf.getframerate())
        except Exception as e:
            log.error("Final WAV verification failed: %s", str(e))
            return False
        
        return True
        
    except Exception as e:
        log.error("Concatenation error: %s", str(e))
        return False
    finally:
        gc.collect()


def synthesize_long_text_chunked(voice, text, output_wav):
    """Synthesize với chunking"""
    log.info("Synthesizing with chunking (%d chars)", len(text))
    
    chunks = split_text_smart(text, max_len=200)
    log.info("Split into %d chunks", len(chunks))
    
    for i, chunk in enumerate(chunks):
        log.debug("Chunk #%d: %s...", i, chunk[:60])
    
    temp_wavs = []
    
    try:
        for i, chunk in enumerate(chunks):
            temp_wav = f"temp_chunk_{i:03d}.wav"
            
            log.info("Processing chunk %d/%d", i + 1, len(chunks))
            
            success = synthesize_to_wav(voice, chunk, temp_wav)
            
            if not success:
                log.error("Failed at chunk #%d", i)
                return False
            
            # Verify chunk WAV
            try:
                with wave.open(temp_wav, "rb") as wf:
                    log.debug("Chunk #%d verified: %d frames", i, wf.getnframes())
            except Exception as e:
                log.error("Chunk #%d WAV invalid: %s", i, str(e))
                return False
            
            temp_wavs.append(temp_wav)
            
            if i < len(chunks) - 1:
                time.sleep(0.1)
        
        success = concat_wavs(temp_wavs, output_wav)
        return success
        
    except Exception as e:
        log.error("Chunked synthesis error: %s", str(e), exc_info=True)
        return False
    finally:
        for temp_wav in temp_wavs:
            try:
                if os.path.exists(temp_wav):
                    os.remove(temp_wav)
            except:
                pass
        gc.collect()


def main():
    log.info("=== START PIPER TTS ===")
    
    if not os.path.isfile(MODEL_PATH):
        log.error("Model not found: %s", MODEL_PATH)
        return 1
    
    try:
        log.info("Loading voice model: %s", MODEL_PATH)
        voice = PiperVoice.load(MODEL_PATH)
        log.info("Voice model loaded successfully")
        log.info("Sample rate: %d Hz", voice.config.sample_rate)
        
        text = normalize_text(TEXT)
        log.info("Text normalized (%d chars)", len(text))
        
        success = synthesize_long_text_chunked(voice, text, FINAL_WAV)
        
        if success:
            log.info("=== SUCCESS ===")
            log.info("Output file: %s", FINAL_WAV)
            return 0
        else:
            log.error("=== FAILED ===")
            return 1
            
    except Exception as e:
        log.error("Fatal error: %s", str(e), exc_info=True)
        return 1
    finally:
        gc.collect()


if __name__ == "__main__":
    sys.exit(main())
