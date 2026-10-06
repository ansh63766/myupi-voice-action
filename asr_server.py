import os
import io
import shutil
import tempfile
import subprocess
import uvicorn
from fastapi import FastAPI, UploadFile, Form
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
    from nemo.collections.asr.models import EncDecMultiTaskModel
    
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
            model_dir = cp
            break
            
    if not model_dir:
        model_dir = candidate_paths[-1] # fallback to huggingface hub
        
    logger.info(f"Using model path: {model_dir}")
    try:
        if os.path.isdir(model_dir):
            nemo_path = os.path.join(model_dir, "nemo", "indic_transcribe_flex.nemo")
            if os.path.exists(nemo_path):
                asr_model = EncDecMultiTaskModel.restore_from(nemo_path)
            else:
                asr_model = EncDecMultiTaskModel.from_pretrained(model_name="bodhan-ai/indic-transcribe-flex")
        else:
            asr_model = EncDecMultiTaskModel.from_pretrained(model_name=model_dir)
            
        logger.info("ASR model loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load ASR model: {e}")

@app.post("/transcribe")
async def transcribe(audio_file: UploadFile, language: str = Form("en")):
    if not asr_model:
        return JSONResponse({"error": "Model not loaded"}, status_code=500)
        
    try:
        audio_bytes = await audio_file.read()
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
            try:
                os.remove(p)
            except Exception:
                pass

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
