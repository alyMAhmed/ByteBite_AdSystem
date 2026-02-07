# Facial Recognition application - Dockerfile
# Uses camera (--device /dev/video0) and optional display for GUI

FROM python:3.11-slim

# System dependencies for OpenCV (GUI, video), dlib/face_recognition, and pygame
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    cmake \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (see .dockerignore for exclusions)
COPY . .

# YuNet face detection model: app expects face_detection_yunet_2022mar.onnx
# Use model from COPY if present; otherwise try to download 2023mar (same ONNX API)
RUN if [ ! -f face_detection_yunet_2022mar.onnx ] || [ $$(stat -c%s face_detection_yunet_2022mar.onnx 2>/dev/null || echo 0) -lt 1000 ]; then \
    curl -fsSL -o /tmp/yunet.onnx \
      "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
    && [ -s /tmp/yunet.onnx ] && [ $$(stat -c%s /tmp/yunet.onnx) -gt 1000 ] \
    && cp /tmp/yunet.onnx face_detection_yunet_2022mar.onnx; \
    fi && rm -f /tmp/yunet.onnx

# Directories the app uses (created at runtime if missing, but ensure they exist)
RUN mkdir -p media identified_faces Reports

# images/ and media/ included via COPY . .

# Environment: headless-friendly (set DISPLAY if using GUI)
ENV PYTHONUNBUFFERED=1

# Default command
CMD ["python", "main.py"]
