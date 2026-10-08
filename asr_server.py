import os
import io
import shutil
import tempfile
import subprocess
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASR Server")

asr_model = None

@app.on_event("startup")
async def startup_event():
    global asr_model
    logger.info("Starting ASR standalone server...")
    logger.info("Loading ASR model into GPU...")
    import sys
    
    # Check paths
    candidate_paths = [
        os.environ.get("ASR_MODEL_DIR"),
        "/content/asr_model",
        os.path.join(os.getcwd(), "models", "indic-transcribe-flex"),
        "bodhan-ai/indic-transcribe-flex"
    ]
    
    model_dir = None
    for cp in candidate_paths:
        if cp and os.path.isdir(cp):
            nemo_file = os.path.join(cp, "nemo", "indic_transcribe_flex.nemo")
            loader_file = os.path.join(cp, "nemo", "load_nemo.py")
            if os.path.exists(nemo_file) and os.path.exists(loader_file):
                model_dir = cp
                break
            
    if not model_dir:
        logger.error("Could not find ASR model directory containing load_nemo.py")
        return
        
    logger.info(f"Using model path: {model_dir}")
    try:
        # Use the model's own loader shipped inside the repo
        nemo_loader_path = os.path.join(model_dir, "nemo")
        if nemo_loader_path not in sys.path:
            sys.path.insert(0, nemo_loader_path)

        from load_nemo import load_nemo_model
        asr_model = load_nemo_model(model_dir)
        logger.info("ASR model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load ASR model: {e}")

@app.post("/transcribe")
async def transcribe(request: Request, language: str = "en"):
    if not asr_model:
        return JSONResponse({"error": "Model not loaded"}, status_code=500)
        
    try:
        raw_path = None
        wav_path = None
        audio_bytes = await request.body()
        if not audio_bytes:
            return JSONResponse({"error": "Empty body"}, status_code=400)
            
        with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as tf:
            tf.write(audio_bytes)
            raw_path = tf.name
            
        wav_path = raw_path + ".wav"
        
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path,
             "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        
        results = asr_model.transcribe(
            [wav_path],
            source_lang=language,
            target_lang=language,
            pnc="yes",
        )
        
        if isinstance(results, list) and results:
            r = results[0]
            text = r.text if hasattr(r, "text") else str(r)
        else:
            text = str(results)
            
        return {"text": text.strip()}
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        for p in (raw_path, wav_path):
            if p:
                try:
                    os.remove(p)
                except Exception:
                    pass

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
