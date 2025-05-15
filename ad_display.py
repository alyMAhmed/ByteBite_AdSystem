import pygame
import sys
import os
import socket
import threading
import time
from collections import deque
import cv2  # OpenCV for video handling

########################
# CONFIG
########################
HOST = "127.0.0.1"
PORT = 5005

# Initial window size
INITIAL_WIDTH = 1280
INITIAL_HEIGHT = 720

# Fade/hold durations
FADE_IN_DURATION = 1.0  # seconds
HOLD_DURATION = 2.0     # seconds (for images only)
FADE_OUT_DURATION = 1.0  # seconds

# Maximum ads in queue
MAX_QUEUE_SIZE = 5

########################
# Pygame Initialization
########################
pygame.init()
pygame.display.set_caption("Ad Display (Resizable)")

# Create a resizable window with double buffering for smooth rendering
screen = pygame.display.set_mode((INITIAL_WIDTH, INITIAL_HEIGHT), pygame.RESIZABLE | pygame.DOUBLEBUF)
clock = pygame.time.Clock()

# Store current window size
window_width, window_height = INITIAL_WIDTH, INITIAL_HEIGHT

ad_queue = deque()

# Fade states
class AdState:
    IDLE = 0
    FADING_IN = 1
    HOLDING = 2
    FADING_OUT = 3

current_media = None  # Can be an image (Surface) or video (VideoPlayer)
current_image_path = None  # Keep track of the current image file path (if is_video == False)
current_stage = AdState.IDLE
stage_start_time = 0.0
alpha = 0.0
is_video = False
video_player = None

########################
# Video Player Class
########################
class VideoPlayer:
    def __init__(self, video_path, width, height):
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise ValueError(f"Unable to open video file: {video_path}")
        self.width = width
        self.height = height
        self.ret = False
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30  # Default to 30 FPS if unknown
        self.frame_duration = max(1.0 / self.fps, 1.0 / 30)  # Ensure minimum 30 FPS
        self.last_frame_time = time.time()
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'XVID'))  # Optimize codec

    def get_frame(self):
        """Read a frame from the video, scale it to (self.width, self.height), 
           and return a pygame.Surface."""
        if not self.cap.isOpened():
            return None
        
        self.ret, frame = self.cap.read()
        if not self.ret:
            # Restart video if it ends
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.ret, frame = self.cap.read()
            if not self.ret:
                return None  # If restart fails, return None

        # Convert BGR -> RGB
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Maintain video aspect ratio inside the window
        video_aspect = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) / self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        screen_aspect = self.width / self.height

        if video_aspect > screen_aspect:
            new_width = self.width
            new_height = int(self.width / video_aspect)
        else:
            new_height = self.height
            new_width = int(self.height * video_aspect)

        frame = cv2.resize(frame, (new_width, new_height))

        # Create a surface the size of the window, then blit the video frame centered
        frame_surface = pygame.Surface((self.width, self.height))
        frame_surface.fill((0, 0, 0))  # fill black so letterbox areas are black
        frame_surface.blit(
            pygame.surfarray.make_surface(frame.swapaxes(0, 1)),
            ((self.width - new_width) // 2, (self.height - new_height) // 2)
        )
        return frame_surface

    def release(self):
        self.cap.release()

########################
# Networking (Thread)
########################
def handle_client_connection(conn, addr):
    """Handles incoming ad requests."""
    with conn:
        data = conn.recv(2048).decode().strip()
        if not data:
            return
        if data.startswith("SHOW_IMAGE:") or data.startswith("SHOW_VIDEO:"):
            path = data.split(":")[-1].strip()
            media_type = "image" if data.startswith("SHOW_IMAGE:") else "video"

            if os.path.isfile(path):
                if len(ad_queue) < MAX_QUEUE_SIZE:
                    ad_queue.append((path, media_type))
                    print(f"Added {media_type}: {path} to queue. Queue size: {len(ad_queue)}")
                else:
                    print(f"Queue is full. Cannot add {media_type}: {path}")
        elif data == "QUIT":
            pygame.quit()
            sys.exit(0)

def server_thread():
    """Runs the TCP server for ad playback commands."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen(5)
        print(f"[ad_display.py] Listening on {HOST}:{PORT} for ad commands...")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_client_connection, args=(conn, addr), daemon=True).start()

threading.Thread(target=server_thread, daemon=True).start()

########################
# Media Handling
########################
def start_new_ad(media_path, media_type):
    """Handles switching between image and video."""
    global current_media, current_image_path, current_stage, stage_start_time
    global alpha, is_video, video_player

    # If there was a video playing, release it
    if is_video and video_player:
        video_player.release()
        video_player = None

    if media_type == "image":
        # Store the path so we can re-load on resize
        current_image_path = media_path
        try:
            image_surf = pygame.image.load(media_path).convert_alpha()
            # Scale to current window size
            image_surf = pygame.transform.scale(image_surf, (window_width, window_height))
            current_media = image_surf
            is_video = False
        except Exception as e:
            print(f"Error loading image '{media_path}': {e}")
            current_media = None
            is_video = False
            return
    elif media_type == "video":
        current_image_path = None
        try:
            video_player = VideoPlayer(media_path, window_width, window_height)
            current_media = video_player
            is_video = True
        except Exception as e:
            print(f"Error initializing VideoPlayer: {e}")
            current_media = None
            is_video = False
            return

    # Begin fade-in
    current_stage = AdState.FADING_IN
    stage_start_time = time.time()
    alpha = 0.0

def update_fade():
    """Handles fade-in, hold, and fade-out logic."""
    global current_stage, stage_start_time, alpha, current_media, is_video, video_player

    if current_stage == AdState.IDLE:
        if ad_queue:
            path, media_type = ad_queue.popleft()
            start_new_ad(path, media_type)
        return

    now = time.time()
    elapsed = now - stage_start_time

    if current_stage == AdState.FADING_IN:
        alpha = min(255, int(255 * elapsed / FADE_IN_DURATION))
        if elapsed >= FADE_IN_DURATION:
            current_stage = AdState.HOLDING
            stage_start_time = now

    elif current_stage == AdState.HOLDING:
        if is_video:
            # Check if video has reached its last frame
            if video_player.cap.get(cv2.CAP_PROP_POS_FRAMES) >= video_player.cap.get(cv2.CAP_PROP_FRAME_COUNT) - 1:
                current_stage = AdState.FADING_OUT
                stage_start_time = now
        else:
            # For images, hold a fixed duration
            if elapsed >= HOLD_DURATION:
                current_stage = AdState.FADING_OUT
                stage_start_time = now

    elif current_stage == AdState.FADING_OUT:
        alpha = max(0, 255 - int(255 * elapsed / FADE_OUT_DURATION))
        if elapsed >= FADE_OUT_DURATION:
            current_stage = AdState.IDLE
            stage_start_time = time.time()
            if is_video and current_media:
                current_media.release()
            current_media = None

def render():
    """Draw the current frame to the screen."""
    screen.fill((0, 0, 0))
    if current_media:
        # If it's video, get the current frame; if image, just copy
        frame = current_media.get_frame() if is_video else current_media.copy()
        if frame:
            frame.set_alpha(alpha)
            screen.blit(frame, (0, 0))
    pygame.display.update()

########################
# Main Loop
########################
def main_loop():
    global running, window_width, window_height, screen
    global current_media, current_image_path, is_video, video_player

    running = True
    target_fps = 30
    while running:
        clock.tick(target_fps)
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False

            elif event.type == pygame.VIDEORESIZE:
                # Update the global window size
                window_width, window_height = event.w, event.h
                # Recreate the display surface
                screen = pygame.display.set_mode(
                    (window_width, window_height),
                    pygame.RESIZABLE | pygame.DOUBLEBUF
                )
                # If we're showing an image, re-load and scale it
                if current_image_path and not is_video:
                    try:
                        image_surf = pygame.image.load(current_image_path).convert_alpha()
                        image_surf = pygame.transform.scale(image_surf, (window_width, window_height))
                        current_media = image_surf
                    except Exception as e:
                        print(f"Error reloading image on resize: {e}")
                # If it's a video, update the video player's dimensions
                if is_video and video_player:
                    video_player.width = window_width
                    video_player.height = window_height

        update_fade()
        render()

    if is_video and video_player:
        video_player.release()
    pygame.quit()
    sys.exit(0)

if __name__ == "__main__":
    main_loop()
