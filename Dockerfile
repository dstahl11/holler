FROM python:3.12-slim

WORKDIR /holler

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg: audio conversion for /admin re-rendering (recorded voice, piper output).
# piper-tts + voice model: lets the admin page render TTS inside the container.
# Drop these two layers if you only ever use pre-rendered / recorded audio.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir piper-tts \
    && mkdir -p /holler/voices \
    && python -m piper.download_voices --download-dir /holler/voices en_US-lessac-medium
ENV HOLLER_PIPER_MODEL=/holler/voices/en_US-lessac-medium.onnx

COPY app ./app
COPY static ./static
# Baked-in defaults; docker-compose bind-mounts your real presets.yaml and
# audio/ over these at runtime.
COPY presets.example.yaml ./presets.yaml
RUN mkdir -p ./audio

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
