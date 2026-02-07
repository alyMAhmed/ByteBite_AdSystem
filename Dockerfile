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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (see .dockerignore for exclusions)
COPY . .

# YuNet model face_detection_yunet_2022mar.onnx is included via COPY . .

# Directories the app uses (created at runtime if missing, but ensure they exist)
RUN mkdir -p media identified_faces Reports

# images/ and media/ included via COPY . .

# Environment: headless-friendly (set DISPLAY if using GUI)
ENV PYTHONUNBUFFERED=1

# Default command
CMD ["python", "main.py"]
