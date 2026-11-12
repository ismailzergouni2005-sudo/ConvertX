FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-dejavu-core \
        fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
