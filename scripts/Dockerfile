# Runtime for the vault scripts (telegram_bot.py with the trainer thread, lint.py, source_index.py).
# The image holds only Python + dependencies; the code runs from the mounted vault at /vault/scripts,
# so editing a script needs a restart, not a rebuild.
# Reproducible: the base image is pinned by digest and Python packages by requirements.lock (hashed).
# Only yt-dlp floats: every rebuild installs the latest, because Instagram and TikTok break old versions.
FROM python:3.12.15-slim-trixie@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/cache \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface

# Tesseract OCR (English + Spanish) for the sensitive-data check on photos.
# Node.js + Claude Code (pinned) for the personal trainer's voice (trainer.py). It signs in with CLAUDE_CODE_OAUTH_TOKEN
# from scripts/.env (your Claude subscription) and is only used when that token is set.
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_VERSION=2.1.293
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa nodejs npm \
    && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes -r /tmp/requirements.lock \
    && pip install --upgrade yt-dlp \
    && mkdir -p /cache && chmod 777 /cache

WORKDIR /vault/scripts
CMD ["python", "telegram_bot.py"]
