import json
with open('colab_run.ipynb', 'r') as f:
    nb = json.load(f)

# Modify install cell
nb['cells'][4]['source'] = [
    '!apt-get install -y ffmpeg\n',
    '!pip install -r requirements.txt\n',
    '!pip install git+https://github.com/huggingface/parler-tts.git\n',
    '!pip install faster-whisper edge-tts pycloudflared\n'
]

# Modify step 3 markdown
nb['cells'][5]['source'] = [
    '### Step 3: Login to Hugging Face\n',
    'The Bodhan AI ASR model requires you to accept the terms on Hugging Face (https://huggingface.co/bodhan-ai/indic-transcribe-flex). Generate a token at huggingface.co/settings/tokens and paste it here.'
]

# Modify step 3 code
nb['cells'][6]['source'] = [
    'from huggingface_hub import notebook_login\n',
    'notebook_login()'
]

# Modify step 4 markdown
nb['cells'][7]['source'] = [
    '### Step 4: Configure `config/config.yaml` for Indic Voice Models'
]

# Modify step 4 code
nb['cells'][8]['source'] = [
    'import yaml\n',
    '\n',
    'with open("config/config.yaml", "r") as f:\n',
    '    cfg = yaml.safe_load(f)\n',
    '\n',
    '# Set ASR to Bodhan AI Indic-Transcribe-Flex\n',
    'cfg["asr"]["provider"] = "indic_transcribe"\n',
    'cfg["asr"]["model_authoritative"] = "bodhan-ai/indic-transcribe-flex"\n',
    'cfg["asr"]["device"] = "cuda"\n',
    '\n',
    '# Set TTS to AI4Bharat Indic-Parler-TTS\n',
    'cfg["tts"]["provider"] = "indic_parler"\n',
    'cfg["tts"]["model"] = "ai4bharat/indic-parler-tts"\n',
    '\n',
    'with open("config/config.yaml", "w") as f:\n',
    '    yaml.dump(cfg, f, default_flow_style=False)\n',
    '\n',
    'print("✅ config.yaml updated for Indic Voice Pipeline!")'
]

with open('colab_run.ipynb', 'w') as f:
    json.dump(nb, f, indent=1)
