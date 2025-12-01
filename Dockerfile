FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps for common scientific Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better cache reuse
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure expected data directories exist (volumes can override)
RUN mkdir -p data/input data/output data/results data/storage

# Default entrypoint runs the full pipeline on the sample dataset
ENTRYPOINT ["python", "scripts/run_pipeline.py"]
CMD ["--dataset", "data/input/npidata_sample_100.csv", "--output-file", "data/output/npidata_sample_100_fixed.csv"]
