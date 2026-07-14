FROM python:3.12-slim

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1
# Keeps Python from buffering stdout/stderr so crash logs are never lost.
ENV PYTHONUNBUFFERED=1

# libcurl4: required by the bitHuman runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gcc libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv installs straight into the image's interpreter (no venv layer)
ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
ENV UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir uv
RUN uv sync --frozen --no-install-project --no-dev

COPY worker.py .

# Prefetch model weights (silero VAD etc.) at BUILD time. Needs no secrets.
RUN python worker.py download-files

# LiveKit agent workers expose a health/debug port
EXPOSE 8081

# The .imx avatar model is MOUNTED at runtime, not baked into the image:
#   docker run --env-file .env -v ./avatar.imx:/models/avatar.imx \
#     -e BITHUMAN_MODEL_PATH=/models/avatar.imx  <image>
# Runtime secrets: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
# OPENAI_API_KEY, BITHUMAN_API_SECRET.
ENTRYPOINT ["python", "worker.py"]
CMD ["start"]
