import os

video_path = "/Users/alyahmed/Documents/Facial Recognition/media/Tomato5.jpeg"
print("Checking file path:", os.path.abspath(video_path))

if os.path.exists(video_path):
    print("File found, proceeding...")
else:
    print("Error: File not found")