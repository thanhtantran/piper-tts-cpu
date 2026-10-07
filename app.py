import subprocess
import os
import sys
import logging
import time
import wave
import re
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from threading import Thread, Lock
import queue

# ================= CONFIG =================
PIPER_BIN = "piper/piper"
OUTPUT_DIR = "output"
LOG_FILE = "piper_subprocess.log"

# ================= SETUP =================
os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    ]
)
log = logging.getLogger("PIPER_WEB")

app = Flask(__name__)

# Queue state
task_queue = queue.Queue()
current_task = None
task_lock = Lock()

def get_models():
    """Scan models/ directory for .onnx files"""
    models_dir = Path("models")
    if not models_dir.exists():
        return []
    return [str(f.relative_to(models_dir)) for f in models_dir.glob("*.onnx")]

def normalize_vietnamese_text(text):
    """Normalize Vietnamese text for TTS"""
    text = re.sub(r'\s+', ' ', text.strip())
    text = text.replace('#', '.')
    text = re.sub(r'(\d+)\.(\d+)%', r'\1,\2 phần trăm', text)
    text = re.sub(r'(\d+)\.(\d+)°C', r'\1,\2 độ C', text)
    text = re.sub(r'(\d+)\.(\d+)(?!\s*(phần trăm|độ C))', r'\1,\2', text)
    return text

def split_text_smart(text, max_len=150):
    """Split text into chunks by sentences"""
    text = normalize_vietnamese_text(text)
    sentences = re.split(r'([.!?:;])', text)
    
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
            sub_parts = re.split(r'([,;])', segment)
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
    
    return [c.strip() for c in chunks if len(c.strip()) > 3]

def run_piper(text: str, out_wav: str, model_path: str):
    """Run piper binary to synthesize one chunk"""
    cmd = [PIPER_BIN, "--model", model_path, "--output_file", out_wav]
    
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = proc.communicate(text, timeout=120)
        
        if proc.returncode != 0:
            log.error(f"Piper failed: {stderr}")
            return False
        
        if not os.path.exists(out_wav) or os.path.getsize(out_wav) == 0:
            log.error(f"WAV file not created or empty")
            return False
        
        return True
    except Exception as e:
        log.error(f"Error running piper: {e}")
        return False

def concat_wavs(wav_files, output_wav):
    """Concatenate WAV files"""
    if not wav_files:
        return False
    
    try:
        with wave.open(wav_files[0], "rb") as wf:
            params = wf.getparams()
        
        with wave.open(output_wav, "wb") as out_wf:
            out_wf.setparams(params)
            for wav_path in wav_files:
                with wave.open(wav_path, "rb") as in_wf:
                    frames = in_wf.readframes(in_wf.getnframes())
                    out_wf.writeframes(frames)
        
        return True
    except Exception as e:
        log.error(f"Concatenation error: {e}")
        return False

def process_task(task_id, text, model_path):
    """Process TTS task sequentially"""
    global current_task
    
    with task_lock:
        current_task = {
            "id": task_id,
            "status": "processing",
            "progress": 0,
            "total": 0,
            "error": None,
            "output": None
        }
    
    log.info(f"Task {task_id} started")
    
    chunks = split_text_smart(text)
    total = len(chunks)
    
    with task_lock:
        current_task["total"] = total
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"tts_{timestamp}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    temp_dir = os.path.join(OUTPUT_DIR, f"temp_{timestamp}")
    os.makedirs(temp_dir, exist_ok=True)
    
    wav_files = []
    
    try:
        for i, chunk_text in enumerate(chunks):
            log.info(f"Task {task_id} - chunk {i+1}/{total}")
            
            wav_path = os.path.join(temp_dir, f"chunk_{i:03d}.wav")
            success = run_piper(chunk_text, wav_path, model_path)
            
            if not success:
                with task_lock:
                    current_task["status"] = "error"
                    current_task["error"] = f"Failed at chunk {i+1}/{total}"
                log.error(f"Task {task_id} failed at chunk {i+1}")
                return
            
            wav_files.append(wav_path)
            
            with task_lock:
                current_task["progress"] = i + 1
            
            if i < total - 1:
                time.sleep(0.2)
        
        # Concat
        success = concat_wavs(wav_files, output_path)
        if not success:
            with task_lock:
                current_task["status"] = "error"
                current_task["error"] = "Failed to concatenate WAV files"
            return
        
        with task_lock:
            current_task["status"] = "completed"
            current_task["output"] = output_filename
        
        log.info(f"Task {task_id} completed: {output_filename}")
        
    except Exception as e:
        log.error(f"Task {task_id} error: {e}")
        with task_lock:
            current_task["status"] = "error"
            current_task["error"] = str(e)
    finally:
        # Cleanup temp
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

def worker():
    """Background worker for task queue"""
    while True:
        task_id, text, model_path = task_queue.get()
        process_task(task_id, text, model_path)
        task_queue.task_done()

# Start worker thread
Thread(target=worker, daemon=True).start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/models")
def api_models():
    return jsonify(get_models())

@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.json
    text = data.get("text", "").strip()
    model = data.get("model", "")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    if not model:
        return jsonify({"error": "No model selected"}), 400
    
    model_path = os.path.join("models", model)
    if not os.path.exists(model_path):
        return jsonify({"error": "Model not found"}), 400
    
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    task_queue.put((task_id, text, model_path))
    
    return jsonify({"task_id": task_id})

@app.route("/api/status")
def api_status():
    with task_lock:
        if current_task is None:
            return jsonify({"status": "idle"})
        return jsonify(current_task)

@app.route("/api/log")
def api_log():
    """Return last 100 lines of log file"""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return jsonify({"lines": lines[-100:]})
    except:
        return jsonify({"lines": []})

@app.route("/output/<filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
