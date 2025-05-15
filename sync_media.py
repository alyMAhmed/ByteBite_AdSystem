import boto3
import os
import time
import socket
import sqlite3
from dotenv import load_dotenv

load_dotenv()

def get_device_id():
    """Get the unique device ID based on the hostname."""
    return socket.gethostname()

script_directory = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_directory)
device_id = get_device_id()
db_filename = f'face_data_{device_id}.db'

# Configuration
BUCKET_NAME = 'testbucket8th'
S3_FOLDER = 'media_files'  # S3 folder path
LOCAL_FOLDER = os.path.join(script_directory, 'media')  # Absolute path to local folder
SYNC_INTERVAL = 60  # Sync interval in seconds
db_filepath = os.path.join(script_directory, db_filename)  # Path to the local SQLite DB

# Default mood/time_of_day if missing
DEFAULT_MOOD = 'neutral'
DEFAULT_TIME_OF_DAY = 'none'

# AWS Credentials
AWS_ACCESS_KEY_ID= os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

def determine_media_type(file_path):
    """Determine the media type based on the file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ['.mp4', '.avi', '.mov']:
        return f"video/{ext[1:]}"
    elif ext in ['.jpg', '.jpeg', '.png']:
        return f"image/{ext[1:]}"
    return 'unknown'

def ensure_s3_folder_exists(s3_client, bucket_name, folder_name):
    """Ensure the folder exists on S3."""
    response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=folder_name)
    if 'Contents' not in response:
        print(f"Creating folder {folder_name} on S3...")
        s3_client.put_object(Bucket=bucket_name, Key=f"{folder_name}/")
        print(f"Folder {folder_name} created on S3.")

def sanitize_filename(filename):
    """
    Sanitize the filename to remove appended strings like .2e6d3AD3.
    Example: "my_file.jpg.2e6d3AD3" -> "my_file.jpg"
    """
    parts = filename.split('.')
    # Heuristic: if the last two segments are "suspiciously long", remove the final one
    if len(parts) > 2 and all(len(part) > 3 for part in parts[-2:]):
        return '.'.join(parts[:-1])
    return filename

def clear_media_table():
    """Clear the media table in the local database."""
    conn = sqlite3.connect(db_filepath)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM media')
    conn.commit()
    conn.close()

def delete_local_files():
    """Delete all files in the local media folder."""
    for file_name in os.listdir(LOCAL_FOLDER):
        file_path = os.path.join(LOCAL_FOLDER, file_name)
        if os.path.isfile(file_path):
            os.remove(file_path)

def update_database(file_path, customer_id, age_range, gender, media_type, profit_score, mood, time_of_day):
    """
    Update (insert) the local database with media file details, including mood/time_of_day.
    """
    conn = sqlite3.connect(db_filepath)
    cursor = conn.cursor()

    # Ensure the table exists (and add new columns if needed)
    cursor.execute('''
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
    # Try adding columns for mood/time_of_day if they don't already exist
    try:
        cursor.execute("ALTER TABLE media ADD COLUMN mood_target TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE media ADD COLUMN time_of_day_range TEXT")
    except sqlite3.OperationalError:
        pass

    # Insert the record
    cursor.execute('''
        INSERT INTO media 
        (customer_id, age_range, gender, file_path, media_type, profit_score, device_id, mood_target, time_of_day_range)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (customer_id, age_range, gender, file_path, media_type, profit_score,
          device_id, mood, time_of_day))

    conn.commit()
    conn.close()

def get_s3_file_list(s3_client):
    """
    Get a list of files from the S3 folder that match the device ID.
    We'll filter on keys that start with: media_files/device_id/
    """
    response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=S3_FOLDER)
    if 'Contents' not in response:
        return []
    return [
        obj['Key'] for obj in response['Contents']
        if not obj['Key'].endswith('/') 
           and obj['Key'].startswith(f"{S3_FOLDER}/{device_id}/")
    ]

def get_local_file_list():
    """Get a list of files in the local folder."""
    return [
        f for f in os.listdir(LOCAL_FOLDER) 
        if os.path.isfile(os.path.join(LOCAL_FOLDER, f))
    ]

def sync_s3_folder(s3_client):
    """
    Sync the contents of the S3 folder with the local folder.

    Expected path structure:
       media_files/<device_id>/<customer_id>/<gender>/<age_range>/<profit_score>/<mood>/<time_of_day>/<filename>

    Where `<mood>` and `<time_of_day>` are optional. If missing:
      - mood defaults to 'neutral'
      - time_of_day defaults to 'none'
    """
    try:
        # Ensure the base S3 folder exists
        ensure_s3_folder_exists(s3_client, BUCKET_NAME, S3_FOLDER)

        s3_files = get_s3_file_list(s3_client)
        local_files = get_local_file_list()

        # Compare sets of sanitized filenames
        s3_file_names = set([sanitize_filename(os.path.basename(key)) for key in s3_files])
        local_file_names = set(local_files)

        if s3_file_names == local_file_names:
            print("Local and S3 contents are already in sync. No action required.")
            return

        # Clear local DB table & delete local files before syncing anew
        clear_media_table()
        delete_local_files()

        # Download each file from S3
        for s3_key in s3_files:
            # relative_path excludes "media_files/", so the first part is device_id
            relative_path = os.path.relpath(s3_key, S3_FOLDER)
            parts = relative_path.split('/')
            # Example parts:
            #   [device_id, customer_id, gender, age_range, profit_score, mood, time_of_day, filename]
            # or if mood/time_of_day are missing, fewer parts.

            # Initialize defaults
            mood = DEFAULT_MOOD
            time_of_day = DEFAULT_TIME_OF_DAY
            customer_id = "unknown"
            gender = "unknown"
            age_range = "unknown"
            profit_score = 0
            filename = None

            # Parse the new structure:
            #
            # Minimum 6 parts for the "no mood/time_of_day" scenario:
            #   0: device_id
            #   1: customer_id
            #   2: gender
            #   3: age_range
            #   4: profit_score
            #   5: filename
            #
            # If there is a mood folder => 7 parts:
            #   5: mood
            #   6: filename
            #
            # If there is mood + time_of_day => 8 parts:
            #   5: mood
            #   6: time_of_day
            #   7: filename
            #
            # If fewer than 6 parts, fallback to older structure.
            if len(parts) >= 6:
                # Basic five are known
                # device_id must match parts[0], though not strictly enforced here:
                customer_id = parts[1]
                gender = parts[2]
                age_range = parts[3]
                profit_score = parts[4]
                
                if len(parts) == 6:
                    # 5 => filename
                    filename = parts[5]
                elif len(parts) == 7:
                    # 5 => mood, 6 => filename
                    mood = parts[5] or DEFAULT_MOOD
                    filename = parts[6]
                elif len(parts) == 8:
                    # 5 => mood, 6 => time_of_day, 7 => filename
                    mood = parts[5] or DEFAULT_MOOD
                    time_of_day = parts[6] or DEFAULT_TIME_OF_DAY
                    filename = parts[7]
                else:
                    # If more than 8, or unexpected – fallback to last item as filename, 
                    # but handle to the best of our ability.
                    filename = parts[-1]
            else:
                # Fallback: older structure
                # device_id / customer_id / gender / age_range / profit_score / filename
                # e.g. 6 parts in total. 
                # If there's fewer than that, parse the best we can.
                if len(parts) == 5:
                    # 0: device_id
                    # 1: customer_id
                    # 2: gender
                    # 3: age_range
                    # 4: filename
                    customer_id = parts[1]
                    gender = parts[2]
                    age_range = parts[3]
                    filename = parts[4]
                elif len(parts) == 4:
                    # 0: device_id
                    # 1: customer_id
                    # 2: gender
                    # 3: filename
                    customer_id = parts[1]
                    gender = parts[2]
                    filename = parts[3]
                elif len(parts) == 3:
                    # 0: device_id
                    # 1: customer_id
                    # 2: filename
                    customer_id = parts[1]
                    filename = parts[2]
                else:
                    # Minimal fallback if we can't parse anything
                    filename = parts[-1]

            # Clean up the filename if it has any appended artifact
            sanitized_filename = sanitize_filename(filename or "unknown_file")
            local_file_path = os.path.join(LOCAL_FOLDER, sanitized_filename)

            # Download the file
            print(f"Downloading {s3_key} from S3 to {local_file_path} ...")
            s3_client.download_file(BUCKET_NAME, s3_key, local_file_path)

            # Determine media type
            media_type = determine_media_type(local_file_path)

            # Update the local DB
            try:
                update_database(
                    local_file_path,
                    customer_id,
                    age_range,
                    gender,
                    media_type,
                    int(profit_score) if str(profit_score).isdigit() else 0,
                    mood,
                    time_of_day
                )
            except Exception as ex:
                print(f"Error inserting DB record for {local_file_path}: {ex}")

    except Exception as e:
        print(f"Error during synchronization: {e}")

if __name__ == '__main__':
    # Initialize S3 client
    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    # Periodically sync
    while True:
        sync_s3_folder(s3_client)
        print(f"Waiting {SYNC_INTERVAL} seconds before the next sync...")
        time.sleep(SYNC_INTERVAL)
