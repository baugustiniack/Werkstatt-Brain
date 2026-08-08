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

EXPOSE 8000

# --reload-dir beschränkt den Datei-Watcher explizit auf Quellcode-Verzeichnisse.
# Ohne dies überwacht uvicorn das gesamte CWD (/app) inkl. exports/,
# sandbox_workspace/ und uploads/ – jeder Sandbox-Export oder Datei-Upload
# würde sonst einen Server-Neustart auslösen und damit den In-Memory-
# LangGraph-Checkpointer (MemorySaver, Kap. 3.5 interrupt()) zurücksetzen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", \
     "--reload-dir", "/app/app", "--reload-dir", "/app/agents"]
