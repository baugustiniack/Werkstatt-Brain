# Isolierter build123d-Container (SPEC Kap. 1.6)
FROM python:3.12-slim

WORKDIR /sandbox

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libgomp1 \
    libxrender1 \
    libxi6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir build123d>=0.8.0 cadquery>=2.4.0

COPY run.py .

ENTRYPOINT ["python", "run.py"]
