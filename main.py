import cv2
from deepface import DeepFace
import face_recognition
import numpy as np
import sqlite3
import threading
import queue
import time
import pickle
from datetime import datetime
import uuid
import os
import socket
import mysql.connector
import schedule
import boto3
from generate_report import generate_report
import random
from dotenv import load_dotenv

load_dotenv()

################################
# New imports for launching/stopping ad_display.py
################################
import subprocess
import atexit

################################
# Absolute path setup
################################
script_dir = os.path.dirname(os.path.abspath(__file__))

def get_device_id():
    return socket.gethostname()

device_id = get_device_id()

db_filename = os.path.join(script_dir, f'face_data_{device_id}.db')
media_dir = os.path.join(script_dir, 'media')
identified_faces_dir = os.path.join(script_dir, "identified_faces")
reports_dir = os.path.join(script_dir, "Reports")

os.makedirs(media_dir, exist_ok=True)
os.makedirs(identified_faces_dir, exist_ok=True)
os.makedirs(reports_dir, exist_ok=True)

################################
# YuNet face detector
################################
print("Loading YuNet face detector for face detection...")
model_path = os.path.join(script_dir, "face_detection_yunet_2022mar.onnx")
if not os.path.exists(model_path):
    raise IOError(f"Could not find YuNet ONNX model at: {model_path}")

detector = cv2.FaceDetectorYN_create(
    model_path,
    "",
    (0, 0),        # We'll set input size dynamically each frame
    0.9,           # Score threshold
    0.3,           # NMS threshold
    5000           # top_k
)
print("YuNet face detector loaded successfully.")

print("Initializing video capture...")
cap = cv2.VideoCapture(0)

frame_skip = 5
frame_resize_factor = 0.3
cached_analysis = None
cache_duration = 1
last_analysis_time = 0
frame_count = 0

frame_queue = queue.Queue(maxsize=10)
analysis_queue = queue.Queue(maxsize=5)

print(f"Connecting to SQLite database: {db_filename}...")
sqlite_conn = sqlite3.connect(db_filename, check_same_thread=False)
c = sqlite_conn.cursor()

# Create tables (if not exist)
c.execute('''
    CREATE TABLE IF NOT EXISTS faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unique_id TEXT,
        encoding BLOB,
        age_range TEXT,
        gender TEXT,
        device_id TEXT
    )
''')

c.execute('''
    CREATE TABLE IF NOT EXISTS identifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unique_id TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        device_id TEXT
    )
''')

c.execute('''
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT,
        age_range TEXT,
        gender TEXT,
        file_path TEXT,
        media_type TEXT,
        profit_score INTEGER,
        device_id TEXT
    )
''')

# Add optional columns if not present
try:
    c.execute("ALTER TABLE media ADD COLUMN mood_target TEXT")
except sqlite3.OperationalError:
    pass

try:
    c.execute("ALTER TABLE media ADD COLUMN time_of_day_range TEXT")
except sqlite3.OperationalError:
    pass

c.execute('''
    CREATE TABLE IF NOT EXISTS ad_displays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_id INTEGER,
        customer_id TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        device_id TEXT
    )
''')

c.execute('''
    CREATE TABLE IF NOT EXISTS saved_frames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unique_id TEXT,
        file_path TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        device_id TEXT
    )
''')

c.execute('''
    CREATE TABLE IF NOT EXISTS sync_info (
        device_id TEXT,
        table_name TEXT,
        last_synced_row INTEGER,
        PRIMARY KEY (device_id, table_name)
    )
''')

sqlite_conn.commit()

# AWS Credentials and S3 Bucket (from .env)
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
bucket_name = os.getenv('bucket_name')

def store_media(customer_id, age_range, gender, file_path, media_type, profit_score):
    abs_media_dir = media_dir
    abs_file_path = os.path.abspath(file_path)
    if not abs_file_path.startswith(abs_media_dir):
        print(f"Error: File path '{abs_file_path}' is not inside the media directory '{abs_media_dir}'.")
        return

    rel_file_path = os.path.relpath(abs_file_path, abs_media_dir)
    c.execute("""
        INSERT INTO media (
            customer_id,
            age_range,
            gender,
            file_path,
            media_type,
            profit_score,
            device_id
        ) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (customer_id, age_range.lower(), gender.lower(), rel_file_path, media_type, profit_score, device_id))
    sqlite_conn.commit()

def preprocess_image(frame):
    img_y_cr_cb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = cv2.split(img_y_cr_cb)
    y_eq = cv2.equalizeHist(y)
    img_y_cr_cb_eq = cv2.merge([y_eq, cr, cb])
    img_rgb_eq = cv2.cvtColor(img_y_cr_cb_eq, cv2.COLOR_YCrCb2BGR)
    return img_rgb_eq

def get_age_range(age):
    if age < 18:
        return "0-17"
    elif age < 30:
        return "18-29"
    elif age < 40:
        return "30-39"
    elif age < 50:
        return "40-49"
    elif age < 60:
        return "50-59"
    else:
        return "60+"

def analyze_faces():
    global cached_analysis, last_analysis_time, sqlite_conn, c
    print("Started analyze_faces thread...")
    while True:
        if not analysis_queue.empty():
            face_region, frame_with_faces = analysis_queue.get()
            current_time = time.time()
            if current_time - last_analysis_time > cache_duration:
                try:
                    analysis_result = DeepFace.analyze(
                        face_region,
                        actions=['age', 'gender', 'emotion'], 
                        enforce_detection=True,
                        detector_backend='retinaface'
                    )
                    last_analysis_time = current_time

                    if isinstance(analysis_result, list) and len(analysis_result) > 0:
                        analysis_result = analysis_result[0]
                    if not isinstance(analysis_result, dict):
                        continue

                    age = analysis_result.get('age', 0)
                    age_range = get_age_range(age)
                    analysis_result['age_range'] = age_range

                    gender_dict = analysis_result.get('gender', {})
                    if isinstance(gender_dict, dict) and gender_dict:
                        dominant_gender = max(gender_dict, key=gender_dict.get)
                    else:
                        dominant_gender = 'Unknown'
                    analysis_result['gender'] = dominant_gender

                    dominant_emotion = analysis_result.get('dominant_emotion', 'neutral')
                    analysis_result['mood'] = dominant_emotion

                    # Face recognition
                    rgb_face = cv2.cvtColor(face_region, cv2.COLOR_BGR2RGB)
                    encodings = face_recognition.face_encodings(rgb_face)
                    
                    if encodings:
                        encoding = encodings[0]
                        encoding_blob = sqlite3.Binary(pickle.dumps(encoding))
                        
                        c.execute("SELECT unique_id, encoding FROM faces")
                        faces = c.fetchall()
                        match_found = False
                        for face in faces:
                            unique_id, db_encoding = face
                            db_encoding = pickle.loads(db_encoding)
                            match = face_recognition.compare_faces([db_encoding], encoding, tolerance=0.6)
                            if match[0]:
                                match_found = True
                                analysis_result['unique_id'] = unique_id
                                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                c.execute(
                                    "INSERT INTO identifications (unique_id, timestamp, device_id) VALUES (?, ?, ?)",
                                    (unique_id, timestamp, device_id)
                                )
                                sqlite_conn.commit()
                                save_frame(frame_with_faces, unique_id)
                                break
                        
                        if not match_found:
                            unique_id = str(uuid.uuid4())
                            c.execute("""
                                INSERT INTO faces (unique_id, encoding, age_range, gender, device_id)
                                VALUES (?, ?, ?, ?, ?)
                            """, (unique_id, encoding_blob, age_range, dominant_gender, device_id))
                            sqlite_conn.commit()
                            analysis_result['unique_id'] = unique_id

                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            c.execute("""
                                INSERT INTO identifications (unique_id, timestamp, device_id)
                                VALUES (?, ?, ?)
                            """, (unique_id, timestamp, device_id))
                            sqlite_conn.commit()
                            save_frame(frame_with_faces, unique_id)

                    cached_analysis = analysis_result

                except Exception as e:
                    print(f"Error analyzing face: {e}")

def save_frame(frame, unique_id):
    try:
        c.execute("""
            SELECT timestamp 
            FROM saved_frames 
            WHERE unique_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 1
        """, (unique_id,))
        row = c.fetchone()
        if row:
            last_captured_str = row[0]
            last_captured_datetime = datetime.strptime(last_captured_str, '%Y-%m-%d %H:%M:%S')
            time_diff = datetime.now() - last_captured_datetime
            # Only save a new frame if more than 10 minutes have passed
            if time_diff.total_seconds() < 10 * 60:
                return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{unique_id}_{timestamp}.jpg"
        file_path = os.path.join(identified_faces_dir, file_name)
        
        cv2.imwrite(file_path, frame)
        rel_file_path = os.path.relpath(file_path, script_dir)

        c.execute("""
            INSERT INTO saved_frames (unique_id, file_path, timestamp, device_id) 
            VALUES (?, ?, ?, ?)
        """, (unique_id, rel_file_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), device_id))
        sqlite_conn.commit()

    except sqlite3.Error as e:
        print(f"Error inserting into saved_frames: {e}")

def log_ad_display(media_id, customer_id):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        c.execute("""
            INSERT INTO ad_displays (media_id, customer_id, timestamp, device_id)
            VALUES (?, ?, ?, ?)
        """, (media_id, customer_id, timestamp, device_id))
        sqlite_conn.commit()
    except Exception as e:
        print(f"Error logging ad display: {e}")

def get_time_of_day_range():
    current_hour = datetime.now().hour
    if 5 <= current_hour < 11:
        return 'morning'
    elif 11 <= current_hour < 17:
        return 'afternoon'
    elif 17 <= current_hour < 21:
        return 'evening'
    else:
        return 'night'

def get_last_display_time(media_id):
    c.execute("""
        SELECT MAX(timestamp)
        FROM ad_displays
        WHERE media_id = ?
    """, (media_id,))
    row = c.fetchone()
    if row and row[0]:
        return datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
    return None

def pick_weighted_media(age_range, gender, mood):
    # Lowercase to match DB
    age_range = age_range.lower()
    gender = gender.lower()

    c.execute('''
        SELECT
            id,
            customer_id,
            file_path,
            media_type,
            profit_score,
            mood_target,
            time_of_day_range
        FROM media
        WHERE age_range = ?
          AND gender = ?
          AND device_id = ?
    ''', (age_range, gender, device_id))
    candidate_ads = c.fetchall()
    if not candidate_ads:
        return None

    weighted_list = []
    current_tod = get_time_of_day_range()

    for (media_id, customer_id, rel_file_path, media_type,
         profit_score, mood_target, tod_range) in candidate_ads:
        
        score = float(profit_score)

        # time-of-day bonus
        if tod_range and tod_range.lower() == current_tod:
            score += 10

        # mood bonus
        if mood_target and mood.lower() == mood_target.lower():
            score += 1500

        last_display = get_last_display_time(media_id)
        if last_display:
            minutes_since_last = (datetime.now() - last_display).total_seconds() / 60.0
            if minutes_since_last < 5:
                # Heavily penalize if it was shown <5 min ago
                score -= 5
            else:
                # Slight bonus the longer it's been since last shown
                bonus = min((minutes_since_last - 5) * 0.1, 30)
                score += bonus

        final_score = max(score, 1.0)
        weighted_list.append({
            'media_id': media_id,
            'customer_id': customer_id,
            'file_path': rel_file_path,
            'media_type': media_type,
            'score': final_score
        })

    total = sum(item['score'] for item in weighted_list)
    r = random.uniform(0, total)
    upto = 0
    for item in weighted_list:
        if upto + item['score'] >= r:
            return (item['media_id'],
                    item['customer_id'],
                    item['file_path'],
                    item['media_type'])
        upto += item['score']
    return None

def send_ad_command(command):
    """
    Opens a short-lived TCP socket to the ad_display.py server and sends commands.
    Supports both images and videos.

    Example commands:
        "SHOW_IMAGE:/path/to/image.jpg"
        "SHOW_VIDEO:/path/to/video.mp4"
    """
    HOST = "127.0.0.1"
    PORT = 5005
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((HOST, PORT))
            s.sendall(command.encode('utf-8'))
    except Exception as e:
        print(f"Failed to send command to ad_display server: {e}")

def play_media(age_range, gender, mood):
    """
    Picks media based on criteria and sends the appropriate command to display it.
    Supports both images and videos.
    """
    ad_info = pick_weighted_media(age_range, gender, mood)
    if not ad_info:
        print(f"No media available for {age_range}, {gender}, mood={mood}")
        return

    media_id, customer_id, rel_file_path, media_type = ad_info
    abs_file_path = os.path.abspath(os.path.join(media_dir, rel_file_path))

    if not os.path.exists(abs_file_path):
        print(f"File path '{abs_file_path}' does not exist.")
        return

    # Determine if it's an image or video and send the right command
    if media_type.lower() in ('image/jpg', 'image/jpeg', 'image/png'):
        command_str = f"SHOW_IMAGE:{abs_file_path}"
    elif media_type.lower() in ('video/mp4', 'video/avi', 'video/mov'):
        command_str = f"SHOW_VIDEO:{abs_file_path}"
    else:
        print(f"Unsupported media type: {media_type}")
        return

    print(f"Displaying ad (ID={media_id}, type={media_type}): {abs_file_path}")
    send_ad_command(command_str)
    log_ad_display(media_id, customer_id)

def calculate_profit(age_range, gender):
    c.execute("""
        SELECT AVG(profit_score)
        FROM media
        WHERE age_range = ?
          AND gender = ?
          AND device_id = ?
    """, (age_range.lower(), gender.lower(), device_id))
    result = c.fetchone()
    if result and result[0] is not None:
        return result[0]
    else:
        return 0

HOST = os.getenv('HOST')
USER = os.getenv('ROOT')
PASSWORD = os.getenv('PASSWORD')

def migrate_data(sqlite_conn):
    cursor_sqlite = sqlite_conn.cursor()
    try:
        mysql_conn = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database='face_data',
            port=3306
        )
        cursor_mysql = mysql_conn.cursor()

        tables_to_migrate = ['faces', 'identifications', 'media', 'ad_displays', 'saved_frames']

        for table_name in tables_to_migrate:
            print(f"Migrating table: {table_name}")

            cursor_sqlite.execute(
                'SELECT last_synced_row FROM sync_info WHERE device_id = ? AND table_name = ?',
                (device_id, table_name)
            )
            result = cursor_sqlite.fetchone()
            last_synced_row = result[0] if result else 0

            cursor_sqlite.execute(
                f'SELECT * FROM {table_name} WHERE id > ? AND device_id = ?',
                (last_synced_row, device_id)
            )
            rows = cursor_sqlite.fetchall()

            cursor_sqlite.execute(f'PRAGMA table_info({table_name})')
            columns_info = cursor_sqlite.fetchall()
            columns = [col[1] for col in columns_info]

            placeholders = ", ".join(["%s"] * len(columns))
            insert_query = f'INSERT INTO {table_name} ({", ".join(columns)}) VALUES ({placeholders})'

            def convert_timestamp(timestamp_str):
                try:
                    dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        return dt.strftime('%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        return timestamp_str

            processed_rows = []
            for row in rows:
                processed_row = []
                for idx, col_name in enumerate(columns):
                    if col_name == "timestamp":
                        processed_row.append(convert_timestamp(row[idx]))
                    else:
                        processed_row.append(row[idx])
                processed_rows.append(tuple(processed_row))

            # Bulk-insert
            if processed_rows:
                cursor_mysql.executemany(insert_query, processed_rows)
                mysql_conn.commit()

                new_last_synced_row = max(row[0] for row in rows)
                cursor_sqlite.execute(
                    "REPLACE INTO sync_info (device_id, table_name, last_synced_row) VALUES (?, ?, ?)",
                    (device_id, table_name, new_last_synced_row)
                )
                sqlite_conn.commit()

            print(f"Data migrated successfully for table {table_name}.")

        mysql_conn.close()
    except mysql.connector.Error as err:
        print(f"Error: {err}")

def sync_databases():
    try:
        migrate_data(sqlite_conn)
    except Exception as e:
        print(f"An error occurred during database sync: {e}")

s3_folder = "identified_faces"
def sync_to_s3():
    s3 = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    if not os.path.exists(identified_faces_dir):
        print(f"Local directory '{identified_faces_dir}' does not exist.")
        return

    existing_files = set()
    try:
        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=f"{s3_folder}/")
        for page in page_iterator:
            if 'Contents' in page:
                for item in page['Contents']:
                    existing_files.add(item['Key'])
    except Exception as e:
        print(f"Error retrieving existing files from S3: {e}")
        return

    for root, dirs, files in os.walk(identified_faces_dir):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, identified_faces_dir)
            s3_path = os.path.join(s3_folder, rel_path).replace("\\", "/")

            if s3_path not in existing_files:
                try:
                    s3.upload_file(file_path, bucket_name, s3_path)
                    print(f"Uploaded {file_path} to s3://{bucket_name}/{s3_path}")
                except Exception as e:
                    print(f"Error uploading {file_path} to S3: {e}")
            else:
                print(f"File {s3_path} already exists in S3, skipping upload.")

# Generate initial report
generate_report(db_filename, reports_dir, device_id)

print("Starting analysis thread...")
try:
    analysis_thread = threading.Thread(target=analyze_faces, daemon=True)
    analysis_thread.start()
except Exception as e:
    print(f"Failed to start analysis thread: {e}")

################################
# NEW: Functions to start/stop ad_display.py
################################
def start_ad_display_script():
    """
    Launch ad_display.py as a child process. 
    This will run concurrently with main.py.
    """
    return subprocess.Popen(["python", "ad_display.py"])

def stop_ad_display_script(proc):
    """
    Stop ad_display.py if it's still running.
    """
    if proc and proc.poll() is None:  # check if process is alive
        proc.terminate()             # send terminate signal
        proc.wait()                  # wait for it to exit

if __name__ == "__main__":
    # Start the ad_display process
    ad_display_process = start_ad_display_script()
    
    # Ensure that if main.py ends for any reason, we stop ad_display.py
    atexit.register(stop_ad_display_script, ad_display_process)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # If we have an analysis result cached, display the ad
            if cached_analysis:
                best_analysis = cached_analysis
                age_range = best_analysis.get('age_range', 'Unknown')
                gender = best_analysis.get('gender', 'Unknown')
                mood = best_analysis.get('mood', 'neutral')
                unique_id = best_analysis.get('unique_id', 'Unknown')

                print("About to play media (ad) on second script...")
                play_media(age_range, gender, mood)
                cached_analysis = None

            # Face detection with YuNet, adding a safe check for `faces`
            small_frame = cv2.resize(frame, None, fx=frame_resize_factor, fy=frame_resize_factor)
            small_frame = preprocess_image(small_frame)

            h_small, w_small, _ = small_frame.shape
            detector.setInputSize((w_small, h_small))

            retval, faces = detector.detect(small_frame)

            # Ensure faces is not None before iterating
            if faces is not None and len(faces) > 0:
                for f in faces:
                    x, y, w_box, h_box, score = f[:5]

                    orig_x = int(x / frame_resize_factor)
                    orig_y = int(y / frame_resize_factor)
                    orig_w = int(w_box / frame_resize_factor)
                    orig_h = int(h_box / frame_resize_factor)

                    cv2.rectangle(frame, (orig_x, orig_y),
                                  (orig_x + orig_w, orig_y + orig_h),
                                  (0, 255, 0), 2)

                    face_region = frame[orig_y:orig_y + orig_h, orig_x:orig_x + orig_w]

                    if frame_count % frame_skip == 0:
                        if analysis_queue.full():
                            analysis_queue.get()  # discard oldest if full
                        analysis_queue.put((face_region, frame.copy()))

            cv2.imshow("Face Detection", frame)
            frame_count += 1

            if cv2.waitKey(1) & 0xFF == ord('q'):
                send_ad_command("QUIT")
                break

        cap.release()
        cv2.destroyAllWindows()
        sqlite_conn.close()
        print("Closed SQLite database connection.")

    except KeyboardInterrupt:
        pass

    finally:
        stop_ad_display_script(ad_display_process)
        print("Main script ending, ad_display.py terminated.")