# Advanced Facial Recognition and Targeted Advertising System

## Overview
This project implements a sophisticated facial recognition system that combines real-time face detection, demographic analysis, and targeted advertising capabilities. The system uses computer vision and machine learning to identify faces, analyze demographic information, and display contextually relevant advertisements based on viewer characteristics.

## Key Features
- Real-time face detection using YuNet ONNX model
- Demographic analysis (age, gender)
- Face recognition and tracking
- Targeted advertising system
- Data synchronization with cloud storage
- Automated reporting system
- Multi-device support
- Customer analytics

## Project Structure
```
├── main.py                 # Core application logic
├── ad_display.py          # Advertisement display system
├── customer_analysis.py   # Customer analytics processing
├── generate_report.py     # Report generation utilities
├── sync_media.py         # Media synchronization
├── face_detection_yunet_2022mar.onnx  # Face detection model
├── media/                # Directory for advertisement media
├── identified_faces/     # Directory for captured face images
├── Reports/             # Generated analysis reports
└── face_data_[device].db # Local SQLite database
```

## Prerequisites
- Python 3.8+
- OpenCV
- DeepFace
- face_recognition
- SQLite3
- AWS SDK (boto3)
- Additional dependencies listed in requirements.txt

## Installation
1. Clone the repository:
```bash
git clone [repository-url]
cd [repository-name]
```

2. Create and activate a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
   - Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   - Edit `.env` and replace the placeholder values with your actual credentials
   - NEVER commit the `.env` file to version control
   - Keep your credentials secure and rotate them regularly

Required environment variables:
```
API_KEY=your_api_key_here
AWS_ACCESS_KEY_ID=your_aws_access_key_id_here
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key_here
bucket_name=your_s3_bucket_name_here
PASSWORD=your_database_password_here
```

## Running with Docker

1. **Build the image:**
   ```bash
   docker build -t facial-recognition .
   ```

2. **Provide the YuNet model** (required): The app expects `face_detection_yunet_2022mar.onnx` in the project root. Either:
   - Download it (e.g. from [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) or Hugging Face) and place it in the project before building, or
   - Mount it at run time: `-v /path/to/face_detection_yunet_2022mar.onnx:/app/face_detection_yunet_2022mar.onnx`

3. **Run the container** (camera and display for GUI):
   ```bash
   docker run --rm -it \
     --device /dev/video0 \
     -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
     -v "$(pwd)/.env:/app/.env" \
     facial-recognition
   ```
   On Linux you may need `xhost +local:docker` once to allow the container to use the display.

4. **Optional:** Mount volumes for persistence (DB, media, reports):
   ```bash
   -v "$(pwd)/media:/app/media" -v "$(pwd)/Reports:/app/Reports"
   ```

## Usage
1. Start the main application:
```bash
python main.py
```

2. The system will automatically:
   - Initialize the face detection model
   - Set up the database
   - Begin capturing and analyzing video feed
   - Display targeted advertisements based on detected demographics

## Database Schema
The system uses SQLite for local storage with the following main tables:
- `faces`: Stores face encodings and demographic data
- `identifications`: Tracks face recognition events
- `media`: Manages advertisement content
- `ad_displays`: Logs advertisement display events
- `saved_frames`: Stores captured frames
- `sync_info`: Tracks data synchronization status

## Features in Detail

### Face Detection and Analysis
- Uses YuNet ONNX model for efficient face detection
- Implements frame preprocessing for improved accuracy
- Performs demographic analysis using DeepFace
- Maintains face tracking across frames

### Advertisement System
- Contextual ad selection based on:
  - Age demographics
  - Gender
  - Time of day
  - Previous display history
- Weighted selection algorithm for ad variety
- Profit scoring system for ad optimization

### Data Management
- Local SQLite database for immediate storage
- AWS S3 integration for cloud backup
- Automated synchronization between devices
- Secure credential management
- EC2 MySQL synchronization:
  - Real-time data sync from local SQLite to EC2 MySQL instance
  - Bi-directional synchronization for multi-device support
  - Automatic conflict resolution
  - Secure connection using SSL/TLS
  - Configurable sync intervals
  - Error handling and retry mechanisms
  - Data integrity verification

### Reporting
- Automated report generation
- Customer analytics
- Performance metrics
- Demographic insights

## Contributing
Contributions are welcome! Please feel free to submit a Pull Request.

## Security Considerations
- All sensitive credentials are stored in environment variables
- Face data is stored locally with encryption
- Regular data synchronization with secure cloud storage
- Proper error handling and logging
- Credentials are never committed to version control
- Use `.env.example` as a template for required environment variables
- Regularly rotate API keys and credentials
- Implement proper access controls for AWS resources
- Use IAM roles with minimum required permissions
- Enable AWS CloudTrail for audit logging
- Encrypt sensitive data at rest and in transit

## Support
For support, please [specify how to contact you or where to open issues]

## Acknowledgments
- YuNet face detection model
- OpenCV community
- DeepFace library
- face_recognition library 