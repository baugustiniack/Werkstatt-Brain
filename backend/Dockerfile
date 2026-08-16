FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglu1-mesa \
    libgomp1 \
    libxrender1 \
    libxi6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY agents ./agents
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# --reload nur wenn UVICORN_RELOAD=1 (siehe entrypoint.sh / docker-compose).
# Ohne Reload weniger Datei-Watcher-Last auf Windows-Hosts.
CMD ["/entrypoint.sh"]
