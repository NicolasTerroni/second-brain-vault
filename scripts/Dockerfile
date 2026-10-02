# Runtime for the vault scripts (telegram_bot.py, lint.py).
# The image holds only Python + dependencies; the code runs from the mounted vault at /vault/scripts,
# so editing a script needs a restart, not a rebuild. Rebuild to update yt-dlp.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/cache \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt \
    && mkdir -p /cache && chmod 777 /cache

WORKDIR /vault/scripts
CMD ["python", "telegram_bot.py"]
