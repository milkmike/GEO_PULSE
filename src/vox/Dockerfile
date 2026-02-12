FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY src/vox/requirements.txt ./vox-requirements.txt
RUN pip install --no-cache-dir -r vox-requirements.txt

# Copy source
COPY src/ ./src/

CMD ["python", "-m", "src.vox.telegram_collector"]
