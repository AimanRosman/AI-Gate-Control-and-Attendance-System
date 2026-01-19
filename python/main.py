
import face_recognition
import cv2
import numpy as np
import sys
import os
import time
from threading import Thread, Lock, Event
import queue
import requests
import torch
from ultralytics import YOLO
import json
import base64
from datetime import datetime, time as dt_time

# --- GOOGLE SHEET API IMPORTS ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
import io

# --- LOAD CONFIGURATION ---
try:
    import config
except ImportError:
    print("ERROR: config.py not found. Please rename config.py.example to config.py and configure it.")
    sys.exit(1)

# ---------------------------------

# --- 2. Global State 🌎 ---
notification_lock = Lock()
audio_lock = Lock()  # Protects audio queue operations
audio_queue = queue.Queue()  # Queue for sequential audio playback
audio_stop_event = Event()  # Event to signal stopping audio worker
audio_clear_event = Event()  # Event to signal clearing the queue for priority audio
last_esp_notified_time = {} # Stores {'name': timestamp} for ESP8266 cooldown
customer_presence_start_time = None 
is_customer_notified = False 

# Face stability tracking for steady capture
face_stability_tracker = {}  # {name: {'positions': [], 'frame_count': 0, 'last_capture_time': 0}}

# Mouse Interaction Globals
WINDOW_NAME = 'Live Recognition - Press Q to Quit'
drawing = False
ix, iy = -1, -1
current_x, current_y = -1, -1

frame_counter = 0


# --- 3. ESP8266 Communication Functions 📡 ---
# Audio duration estimates (in seconds) for different audio types
AUDIO_DURATIONS = {
    'attendance': 2.0,
    'customer': 3.0,
    'clockin': 2.5,
    'clockout': 2.5,
    'relay': 0.5,  # Relay has no audio, just a short delay
    'default': 3.0  # Default for admin audio files
}

def get_audio_duration(endpoint):
    """Estimates the duration of an audio based on endpoint name."""
    endpoint_lower = endpoint.lower()
    if 'relay' in endpoint_lower:
        return AUDIO_DURATIONS['relay']
    elif 'clockin' in endpoint_lower:
        return AUDIO_DURATIONS['clockin']
    elif 'clockout' in endpoint_lower:
        return AUDIO_DURATIONS['clockout']
    elif endpoint_lower == 'attendance':
        return AUDIO_DURATIONS['attendance']
    elif endpoint_lower == 'customer':
        return AUDIO_DURATIONS['customer']
    else:
        return AUDIO_DURATIONS['default']


def audio_worker():
    """
    Background worker thread that processes the audio queue sequentially.
    Each audio request is sent to ESP8266 and we wait for its estimated duration
    before processing the next request in the queue.
    """
    while not audio_stop_event.is_set():
        try:
            # Get next audio request from queue (blocks until available or timeout)
            audio_request = audio_queue.get(timeout=0.5)
            
            if audio_request is None:
                # Shutdown signal
                break
            
            endpoint, wait_time = audio_request
            
            # Check if we should clear the queue (priority audio was requested)
            if audio_clear_event.is_set():
                audio_clear_event.clear()
                # Clear the queue
                while not audio_queue.empty():
                    try:
                        audio_queue.get_nowait()
                        audio_queue.task_done()
                    except queue.Empty:
                        break
            
            # Send the audio request to ESP8266
            url = f"http://{config.ESP8266_IP}:{config.ESP8266_PORT}/{endpoint}"
            
            try:
                response = requests.get(url, timeout=wait_time) 
                
                if response.status_code == 200:
                    print(f"✅ Signal '{endpoint}' sent to ESP8266.")
                else:
                    print(f"⚠️ ESP8266 returned status: {response.status_code}")
                    
            except Exception:
                print(f"⛔ FAILED to send '{endpoint}' to ESP8266.")
            
            # Wait for audio to finish playing (unless it's a relay command)
            if 'relay' not in endpoint.lower():
                audio_duration = get_audio_duration(endpoint)
                # Wait in small increments to remain responsive to clear events
                wait_end = time.time() + audio_duration
                while time.time() < wait_end:
                    if audio_clear_event.is_set():
                        # Priority audio requested - stop waiting and let it process
                        break
                    time.sleep(0.1)
            
            audio_queue.task_done()
            
        except queue.Empty:
            # No request in queue, just continue checking
            continue
        except Exception as e:
            print(f"Audio worker error: {e}")


def send_to_esp(endpoint, wait_time=2.0, retries=3, is_audio=True, priority=False):
    """
    Queue an audio request to ESP8266 for sequential playback.
    If priority=True (admin detected), clears the queue and plays immediately.
    Relay commands are sent directly without queuing.
    """
    # Relay commands are sent directly without queuing (no audio to wait for)
    if 'relay' in endpoint.lower():
        def _send_relay():
            url = f"http://{config.ESP8266_IP}:{config.ESP8266_PORT}/{endpoint}"
            try:
                response = requests.get(url, timeout=wait_time) 
                if response.status_code == 200:
                    print(f"✅ Signal '{endpoint}' sent to ESP8266.")
                else:
                    print(f"⚠️ ESP8266 returned status: {response.status_code}")
            except Exception:
                print(f"⛔ FAILED to send '{endpoint}' to ESP8266.")
        
        Thread(target=_send_relay, daemon=True).start()
        return
    
    # Audio commands are queued for sequential playback
    if is_audio:
        with audio_lock:
            if priority:
                # Priority audio (admin detected) - clear queue and add this to front
                print(f"🔊 Priority audio '{endpoint}' - clearing queue for immediate playback")
                audio_clear_event.set()
                # Clear existing queue
                while not audio_queue.empty():
                    try:
                        audio_queue.get_nowait()
                        audio_queue.task_done()
                    except queue.Empty:
                        break
            
            # Add to queue
            audio_queue.put((endpoint, wait_time))
            print(f"🔊 Audio '{endpoint}' queued for playback")


# Start the audio worker thread
audio_worker_thread = Thread(target=audio_worker, daemon=True)
audio_worker_thread.start()


def send_attendance_sequence(name, action_type="CLOCKIN"):
    """
    Sends a sequence of ESP8266 commands for attendance:
    1. Play attendance audio
    2. Wait for image capture (handled by caller)
    3. Play specific admin audio (if available)
    4. Control relay
    """
    # Step 1: Play attendance audio
    print(f"🔊 Playing attendance audio...")
    send_to_esp("attendance", wait_time=0.5)
    
    # Step 2 is handled by the caller (image capture)
    # This function will be called again after capture
    
def send_admin_audio_and_relay(name, action_type="CLOCKIN"):
    """
    Sends admin-specific audio and relay control.
    Called AFTER image capture is complete.
    For CLOCKOUT, only plays audio without triggering relay.
    """
    # Step 3: Play specific admin audio (if available)
    if name in config.ADMINS_WITH_AUDIO:
        # Format: username_clockin or username_clockout (lowercase)
        audio_endpoint = f"{name.lower()}_{action_type.lower()}"
        print(f"🔊 Playing {name}'s {action_type} audio: {audio_endpoint}")
        send_to_esp(audio_endpoint, wait_time=0.5, priority=True)
        
        # Small delay to let audio start playing
        time.sleep(0.3)
    else:
         # Generic welcome if no custom audio
         pass
    
    # Step 4: Control relay (ONLY for CLOCKIN, not CLOCKOUT)
    if action_type == "CLOCKIN":
        # Use relay-only endpoint to avoid playing admin audio again
        relay_endpoint = f"{name.lower()}_relay"
        print(f"🔓 Triggering relay for {name} (relay only, no audio)")
        send_to_esp(relay_endpoint, wait_time=0.5)
    else:
        print(f"ℹ️ Check-out complete - No relay trigger needed")


# --- Daily Status Management ---
def get_daily_status():
    """Reads the current attendance status from JSON file."""
    if not os.path.exists(config.DAILY_STATUS_FILE):
        return {"date": None, "checked_in_admins": [], "checked_out_admins": []}
    
    try:
        with open(config.DAILY_STATUS_FILE, 'r') as f:
            data = json.load(f)
            
            # Check if the stored date is today. If not, reset the log.
            today_date_str = datetime.now().strftime("%Y-%m-%d")
            if data.get("date") != today_date_str:
                return {"date": today_date_str, "checked_in_admins": [], "checked_out_admins": []}
            
            # Ensure both fields exist (for backwards compatibility)
            if "checked_in_admins" not in data:
                data["checked_in_admins"] = data.get("logged_admins", [])
            if "checked_out_admins" not in data:
                data["checked_out_admins"] = []
            
            return data
    except Exception as e:
        print(f"Error reading daily status: {e}")
        return {"date": None, "checked_in_admins": [], "checked_out_admins": []}

def save_daily_status(data):
    """Saves the current attendance status to JSON file."""
    try:
        data["date"] = datetime.now().strftime("%Y-%m-%d")
        with open(config.DAILY_STATUS_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving daily status: {e}")


# --- Upload Image to Google Drive (Shared Drive) ---
def upload_image_to_drive(image_base64, filename):
    """
    Uploads an image to Google Drive (Shared Drive) and returns a publicly accessible URL.
    """
    try:
        # Setup credentials
        scope = ['https://www.googleapis.com/auth/drive.file']
        creds = ServiceAccountCredentials.from_json_keyfile_name(config.SERVICE_ACCOUNT_FILE, scope)
        
        # Build Drive service
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Decode base64 to bytes
        image_bytes = base64.b64decode(image_base64)
        
        # Create file metadata
        file_metadata = {
            'name': filename,
            'mimeType': 'image/jpeg'
        }
        
        # Add folder parent if specified (must be in Shared Drive)
        if config.GOOGLE_DRIVE_FOLDER_ID:
            file_metadata['parents'] = [config.GOOGLE_DRIVE_FOLDER_ID]
        elif config.SHARED_DRIVE_ID:
            # If no folder specified, use root of Shared Drive
            file_metadata['parents'] = [config.SHARED_DRIVE_ID]
        
        # Upload file with Shared Drive support
        media = MediaInMemoryUpload(image_bytes, mimetype='image/jpeg', resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink',
            supportsAllDrives=True  # Required for Shared Drives
        ).execute()
        
        file_id = file.get('id')
        
        # Make file publicly accessible
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        drive_service.permissions().create(
            fileId=file_id,
            body=permission,
            supportsAllDrives=True  # Required for Shared Drives
        ).execute()
        
        # Generate direct image URL
        image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
        
        print(f"✅ Image uploaded to Drive: {filename}")
        return image_url
        
    except Exception as e:
        print(f"ERROR uploading image to Drive: {e}")
        return None


# --- Upload Image to ImgBB (Alternative) ---
def upload_image_to_imgbb(image_base64, filename):
    """
    Uploads an image to ImgBB and returns the image URL.
    """
    try:
        if not config.IMGBB_API_KEY:
            print("ERROR: IMGBB_API_KEY not set. Get one from https://api.imgbb.com/")
            return None
        
        data = {
            'key': config.IMGBB_API_KEY,
            'image': image_base64,
            'name': filename
        }
        
        response = requests.post('https://api.imgbb.com/1/upload', data=data)
        
        if response.status_code == 200:
            result = response.json()
            if result['success']:
                image_url = result['data']['url']
                print(f"✅ Image uploaded to ImgBB: {filename}")
                return image_url
            else:
                print(f"ERROR: ImgBB upload failed: {result}")
                return None
        else:
            print(f"ERROR: ImgBB upload failed with status {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        print(f"ERROR uploading image to ImgBB: {e}")
        return None


# --- Upload Image to Imgur (Alternative) ---
def upload_image_to_imgur(image_base64, filename):
    """
    Uploads an image to Imgur and returns the image URL.
    """
    try:
        if not config.IMGUR_CLIENT_ID:
            print("ERROR: IMGUR_CLIENT_ID not set. Get one from https://api.imgur.com/oauth2/addclient")
            return None
        
        headers = {
            'Authorization': f'Client-ID {config.IMGUR_CLIENT_ID}'
        }
        
        data = {
            'image': image_base64,
            'type': 'base64',
            'name': filename
        }
        
        response = requests.post('https://api.imgur.com/3/image', headers=headers, data=data)
        
        if response.status_code == 200:
            result = response.json()
            image_url = result['data']['link']
            print(f"✅ Image uploaded to Imgur: {filename}")
            return image_url
        else:
            print(f"ERROR: Imgur upload failed with status {response.status_code}: {response.text}")
            return None
            
    except Exception as e:
        print(f"ERROR uploading image to Imgur: {e}")
        return None


# --- Upload Image (Dispatcher) ---
def upload_image(image_base64, filename):
    """
    Uploads image using the configured method.
    """
    if config.USE_IMGBB:
        return upload_image_to_imgbb(image_base64, filename)
    elif config.USE_IMGUR:
        return upload_image_to_imgur(image_base64, filename)
    elif config.USE_SHARED_DRIVE:
        return upload_image_to_drive(image_base64, filename)
    else:
        print("ERROR: No image upload method configured!")
        return None

# --- Google Sheet Logging Function ---
def google_sheet_log(name, image_base64, action_type="CHECK-IN", attendance_status=""):
    """
    Connects to Google Sheets and logs attendance, using get_all_values
    to bypass duplicate header issues during row searching.
    """
    # 1. API Setup
    try:
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        
        creds = ServiceAccountCredentials.from_json_keyfile_name(config.SERVICE_ACCOUNT_FILE, scope)
        client = gspread.authorize(creds)

    except Exception as e:
        print(f"CRITICAL: Failed to authorize Google Sheets API. Error: {e}")
        return False

    # 2. Open the Sheet and Worksheet
    try:
        sheet = client.open(config.SPREADSHEET_NAME).sheet1

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"CRITICAL: Spreadsheet '{config.SPREADSHEET_NAME}' not found.")
        return False
    except Exception as e:
        print(f"CRITICAL: Error opening worksheet: {e}")
        return False

    # 3. Upload image to hosting service and get URL
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{name.replace(':', '_').replace(' ', '_')}_{action_type}_{timestamp}.jpg"
    
    image_url = upload_image(image_base64, filename)
    
    if not image_url:
        print("ERROR: Failed to upload image")
        return False
    
    image_formula = f'=IMAGE("{image_url}", 1)'

    # 4. Prepare and Append/Update Data
    try:
        date_only = datetime.now().strftime("%Y-%m-%d")
        time_only = datetime.now().strftime("%H:%M:%S")
        
        # --- ROBUSTNESS CHANGE: Use get_all_values() to avoid header crash ---
        all_data = sheet.get_all_values()
        
        # Check if the sheet is empty or only has headers
        if not all_data:
            # Sheet is totally empty, treat as if no record found
            header = []
            data_rows = []
        else:
            header = all_data[0]
            data_rows = all_data[1:]
        
        # Define the header names we expect
        # NOTE: These must match the text in Row 1 of your sheet!
        EXPECTED_HEADERS = {
            'Date': 1, 'Name': 2, 'Check-in Time': 3, 'Status': 4, 
            'Check-out Time': 5, 'Check-in Image': 6, 'Check-out Image': 7
        }
        
        # Find the column index (1-based) for each required header
        # We must use 1-based indexing for gspread updates
        COL_MAP = {}
        for col_name, expected_index in EXPECTED_HEADERS.items():
            try:
                # Find the 1-based index (index() is 0-based, add 1)
                COL_MAP[col_name] = header.index(col_name) + 1
            except ValueError:
                # If a column header is missing, use the expected default index
                COL_MAP[col_name] = expected_index 
        
        
        # Search for existing row for this person today
        row_index = None
        # data_rows is 0-indexed, so the actual sheet row is idx + 2 (header is row 1)
        for idx, row in enumerate(data_rows): 
            # Check the 'Date' column and 'Name' column based on their indices
            date_col_index = COL_MAP['Date'] - 1 
            name_col_index = COL_MAP['Name'] - 1
            
            # Ensure the row has enough columns before accessing
            if len(row) > max(date_col_index, name_col_index):
                if row[date_col_index] == date_only and row[name_col_index] == name:
                    row_index = idx + 2 # Actual sheet row number (1-based)
                    break
        
        
        # 5. Update the Sheet
        if action_type == "CHECK-IN":
            if row_index:
                # Update existing row
                sheet.update_cell(row_index, COL_MAP['Check-in Time'], time_only)
                sheet.update_cell(row_index, COL_MAP['Status'], attendance_status)
                sheet.update_cell(row_index, COL_MAP['Check-in Image'], image_formula)
                print(f"✅ Check-in updated for {name} with status: {attendance_status}")
            else:
                # Create new row
                data_row = [date_only, name, time_only, attendance_status, "", image_formula, ""]
                sheet.append_row(data_row, value_input_option='USER_ENTERED')
                print(f"✅ Check-in logged for {name} with status: {attendance_status}")
        
        elif action_type == "CHECK-OUT":
            if row_index:
                # Update existing row
                sheet.update_cell(row_index, COL_MAP['Check-out Time'], time_only)
                sheet.update_cell(row_index, COL_MAP['Check-out Image'], image_formula)
                print(f"✅ Check-out logged for {name}")
            else:
                # No check-in found, create row with check-out only.
                # Fill in columns C and F with blanks for Check-in data
                data_row = [date_only, name, "", attendance_status, time_only, "", image_formula]
                sheet.append_row(data_row, value_input_option='USER_ENTERED')
                print(f"⚠️ Check-out logged for {name} (no check-in found)")
        
        return True

    except Exception as e:
        print(f"ERROR: Failed to update Google Sheet. Error: {e}")
        return False


# --- Face Frame Counter (Auto-snap after 3 frames) ---
def check_face_stability(name, face_location):
    """
    Counts frames after face recognition.
    Returns True when 3 frames have been detected (ready for capture).
    """
    global face_stability_tracker
    
    # Initialize tracker for this person if not exists
    if name not in face_stability_tracker:
        face_stability_tracker[name] = {
            'frame_count': 1,
            'last_capture_time': 0,
            'missed_frames': 0
        }
        return False
    
    tracker = face_stability_tracker[name]
    
    # Check cooldown period (don't capture too frequently)
    time_since_last_capture = time.time() - tracker['last_capture_time']
    if time_since_last_capture < config.CAPTURE_COOLDOWN and tracker['last_capture_time'] > 0:
        return False
    
    # Increment frame count
    tracker['frame_count'] += 1
    tracker['missed_frames'] = 0
    
    # Return True when we've seen 3 frames
    if tracker['frame_count'] >= 3:
        # Mark capture time and reset for next capture
        tracker['last_capture_time'] = time.time()
        tracker['frame_count'] = 0
        return True
    
    return False

def reset_face_stability(name):
    """Resets stability tracking for a person."""
    global face_stability_tracker
    if name in face_stability_tracker:
        del face_stability_tracker[name]
        

# --- Image Capture and Encoding ---
def capture_and_encode_face(display_frame, face_location):
    """Captures the full display frame, then creates a small thumbnail for upload."""
    try:
        # face_location is from the small_frame (resized for face detection)
        t, r, b, l = face_location
        
        # Scale coordinates back to the display frame size
        top = int(t / config.RESIZE_FACTOR)
        right = int(r / config.RESIZE_FACTOR)
        bottom = int(b / config.RESIZE_FACTOR)
        left = int(l / config.RESIZE_FACTOR)
        
        # Add padding around the face for context (30% vertical, 20% horizontal)
        height = bottom - top
        width = right - left
        
        padding_y = int(height * 0.3)
        padding_x = int(width * 0.2)
        
        # Apply padding with boundary checks
        top_padded = max(0, top - padding_y)
        bottom_padded = min(display_frame.shape[0], bottom + padding_y)
        left_padded = max(0, left - padding_x)
        right_padded = min(display_frame.shape[1], right + padding_x)
        
        # Crop the face region with padding from display frame
        face_img = display_frame[top_padded:bottom_padded, left_padded:right_padded]
        
        # Check if we got a valid crop
        if face_img.size == 0:
            print("ERROR: Empty face crop")
            return None
        
        # Create a small thumbnail for Google Sheets (to keep file size reasonable)
        face_thumbnail = cv2.resize(face_img, config.FACE_CROP_SIZE, interpolation=cv2.INTER_LINEAR)
        
        # Encode with high quality
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 110]
        ret, buffer = cv2.imencode('.jpg', face_thumbnail, encode_param)
        if not ret: 
            print("ERROR: Failed to encode image")
            return None
        
        # Convert buffer to Base64 string
        base64_encoded = base64.b64encode(buffer).decode('utf-8')
        return base64_encoded
        
    except Exception as e:
        print(f"ERROR encoding face image: {e}")
        import traceback
        traceback.print_exc()
        return None


# --- COMBINED Notification Logic (ESP8266 + Attendance) ---
def notify_detection(name, display_frame, face_location=None):
    """
    Handles both ESP8266 notifications AND attendance tracking.
    """
    current_time = time.time()
    
    # --- CUSTOMER LOGIC (ESP8266 only) ---
    if name == "Customer":
        print(f"🔔 Customer presence confirmed (Body detected) - Triggering customer.mp3")
        send_to_esp("customer")
        return
    
    # --- ADMIN/EMPLOYEE LOGIC ---
    if name != "Unknown":
        # Check current time window
        current_dt = datetime.now()
        current_time_dt = current_dt.time()
        is_saturday = current_dt.weekday() == 5
        
        in_checkin_window = config.CHECK_IN_START <= current_time_dt <= config.LATE_CHECK_IN_END
        
        if is_saturday:
            in_checkout_window = config.SATURDAY_CHECK_OUT_START <= current_time_dt <= config.CHECK_OUT_END
        else:
            in_checkout_window = config.CHECK_OUT_START <= current_time_dt <= config.CHECK_OUT_END
        
        # --- OUTSIDE ATTENDANCE WINDOWS: Immediate ESP8266 notification (NO stabilization) ---
        if not (in_checkin_window or in_checkout_window):
            with notification_lock:
                # Check for ESP8266 cooldown
                if name in last_esp_notified_time and (current_time - last_esp_notified_time[name] < config.ADMIN_COOLDOWN_TIME):
                    # Still in cooldown period for ESP8266
                    return
                else:
                    # Update the last notified time and trigger ESP8266 IMMEDIATELY
                    last_esp_notified_time[name] = current_time
                    print(f"🔔 {name} detected - Triggering audio + relay IMMEDIATELY (no stabilization)")
                    admin_endpoint = name.lower()
                    send_to_esp(admin_endpoint, priority=True)
            print(f"👤 {name} detected outside attendance windows. Access Granted.")
            # No need to track stability outside attendance windows
            return
        
        # --- DURING ATTENDANCE WINDOWS: Full sequence with stabilization ---
        # Check if face is stable before capturing for attendance (ONLY during clock-in/out)
        if face_location and not check_face_stability(name, face_location):
            # Face is still moving, wait for stability before capturing image
            return
        
        if face_location:
            print(f"✓ {name}'s face is stable, processing attendance...")
            # STEP 1: Play "kehadiran" (attendance) audio immediately when face is stable
            print(f"🔊 Playing attendance audio (kehadiran)...")
            send_to_esp("attendance", wait_time=0.5)
        
        with notification_lock:
            status = get_daily_status()
            
            # CHECK-IN Logic 
            if in_checkin_window:
                if name in status["checked_in_admins"]:
                    # Already checked in, but still grant access with admin audio + relay
                    print(f"⚠️ {name} already checked in today. Granting access...")
                    admin_endpoint = name.lower()
                    send_to_esp(admin_endpoint, priority=True)
                    print(f"✅ Access granted to {name} (already checked in)")
                    return
                
                # Determine attendance status: ON TIME or LATE
                attendance_status = "LATE" if current_time_dt >= config.LATE_THRESHOLD else "ON TIME"
                print(f"---> Check-in Status: {attendance_status}")
                
                # Capture image and log to Google Sheets
                if face_location:
                    image_base64 = capture_and_encode_face(display_frame, face_location)
                    
                    if image_base64 and google_sheet_log(name, image_base64, "CHECK-IN", attendance_status=attendance_status):
                        status["checked_in_admins"].append(name)
                        save_daily_status(status)
                        print(f"✅ {name} checked in successfully as {attendance_status}.")
                        
                        # STEP 3 & 4: Play admin audio and control relay
                        send_admin_audio_and_relay(name, "CLOCKIN")
                    elif not image_base64:
                        print("ERROR: Failed to capture face image for check-in.")
            
            # CHECK-OUT Logic 
            elif in_checkout_window:
                if name in status["checked_out_admins"]:
                    # Already checked out, but still grant access with admin audio + relay
                    print(f"⚠️ {name} already checked out today. Granting access...")
                    admin_endpoint = name.lower()
                    send_to_esp(admin_endpoint, priority=True)
                    print(f"✅ Access granted to {name} (already checked out)")
                    return
                
                if name not in status["checked_in_admins"]:
                    print(f"⚠️ {name} attempting check-out without check-in!")
                
                # Capture image and log to Google Sheets
                if face_location:
                    image_base64 = capture_and_encode_face(display_frame, face_location)
                    
                    if image_base64 and google_sheet_log(name, image_base64, "CHECK-OUT", attendance_status="CHECK-OUT"):
                        status["checked_out_admins"].append(name)
                        save_daily_status(status)
                        print(f"✅ {name} checked out successfully.")
                        
                        # STEP 3 & 4: Play admin audio and control relay
                        send_admin_audio_and_relay(name, "CLOCKOUT")
                    elif not image_base64:
                        print("ERROR: Failed to capture face image for check-out.")


# --- 4. Utility Functions 📐 ---
def get_check_point(bbox, resize_factor=1.0, point_type='top'):
    """Calculates the top-center point of a bounding box for ROI check."""
    x, y, w, h = [int(v / resize_factor) for v in bbox]
    X_center = x + (w // 2)
    Y_point = y 
    return (X_center, Y_point)

def is_point_in_roi(point, roi):
    """Checks if a single (x, y) point is within the ROI (rx, ry, rw, rh)."""
    px, py = point
    rx, ry, rw, rh = roi
    return (px >= rx) and (px <= rx + rw) and (py >= ry) and (py <= ry + rh)


# --- 5. Mouse Callback Function (For setting ROI) ---
def draw_roi(event, x, y, flags, param):
    """Handles mouse events for drawing and setting the ROI."""
    global ix, iy, current_x, current_y, drawing, customer_presence_start_time, is_customer_notified

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y
        current_x, current_y = x, y 

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            current_x, current_y = x, y

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False

        x0 = min(ix, x)
        y0 = min(iy, y)
        x1 = max(ix, x)
        y1 = max(iy, y)
        w = x1 - x0
        h = y1 - y0

        if w > 10 and h > 10:
            config.ROI_COORDINATES[0] = x0
            config.ROI_COORDINATES[1] = y0
            config.ROI_COORDINATES[2] = w
            config.ROI_COORDINATES[3] = h

            print(f"ROI updated to: {config.ROI_COORDINATES}. Recognition state reset.")
            with notification_lock:
                # Reset ESP8266 cooldown
                last_esp_notified_time.clear()
                # Reset attendance status
                status = get_daily_status()
                status["checked_in_admins"] = []
                status["checked_out_admins"] = []
                save_daily_status(status)
            customer_presence_start_time = None
            is_customer_notified = False
            face_stability_tracker.clear()

        ix, iy = -1, -1
        current_x, current_y = -1, -1


# --- 6. Load Known Faces and Generate Encodings 👤 ---
known_face_encodings = []
known_face_names = [] 

def load_faces_from_directory(person_dir, name):
    """Loads all images from a directory, gets their face encodings."""
    full_path = os.path.join(config.FACE_DATA_DIR, person_dir)
    
    if not os.path.isdir(full_path):
        print(f"Warning: Face directory not found: {full_path}.")
        return

    face_count = 0
    for filename in os.listdir(full_path):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            file_path = os.path.join(full_path, filename)
            try:
                image = face_recognition.load_image_file(file_path)
                encoding = face_recognition.face_encodings(image)
                if encoding:
                    known_face_encodings.append(encoding[0])
                    known_face_names.append(name) 
                    face_count += 1
                else:
                    print(f"Warning: No face found in image file: {filename}.")
            except Exception as e:
                print(f"ERROR processing file {filename}: {e}")
    if face_count == 0:
        print(f"Warning: No faces were successfully loaded for {name}.")
    else:
        print(f"Successfully loaded {face_count} faces for {name}.")

# Load faces dynamically from the 'known_faces' directory
if not os.path.exists(config.FACE_DATA_DIR):
    os.makedirs(config.FACE_DATA_DIR)
    print(f"Created {config.FACE_DATA_DIR} directory. Please add subdirectories for each person.")
else:
    # Iterate through all subdirectories in known_faces
    for person_name in os.listdir(config.FACE_DATA_DIR):
        person_path = os.path.join(config.FACE_DATA_DIR, person_name)
        if os.path.isdir(person_path):
            # Use the directory name as the person's name (e.g., 'Akmal', 'Owen')
            # You might want to capitalize it or format it
            load_faces_from_directory(person_name, person_name.capitalize())


if not known_face_encodings:
    print("Warning: No known faces loaded. Detection will only work for 'Unknown' faces.")


# --- 7. Initialize YOLO Model (Body Detection) 🏃 ---
print("Initializing YOLOv8 model for presence detection...")
try:
    yolo_model = YOLO("yolov8n.pt") 
    if torch.cuda.is_available():
        yolo_model.to('cuda')
    print(f"YOLOv8 initialized on {'GPU' if torch.cuda.is_available() else 'CPU'}.")
except Exception as e:
    print(f"FATAL ERROR: Could not load YOLO model. Details: {e}")
    sys.exit()
    
def run_yolo_detection(frame):
    """Runs YOLO to find all people (class 0)."""
    results = yolo_model.predict(
        source=frame,
        conf=config.YOLO_CONFIDENCE_THRESHOLD,
        classes=0, 
        verbose=False,
        imgsz=640,
    )

    detections = []
    if results and len(results) > 0 and results[0].boxes:
        for box in results[0].boxes:
            x1, y1, x2, y2 = box.xyxy[0].int().tolist()
            detections.append({
                'box': (x1, y1, x2 - x1, y2 - y1), 
                'confidence': box.conf[0].item()
            })
    return detections

# --- Threaded Video Stream Class (Eliminates RTSP Buffer Lag) ---
class VideoStream:
    """Read video stream in a separate thread to drop buffered frames."""
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        self.stream.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Reduce buffer size
        self.stream.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.stream.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        
        (self.grabbed, self.frame) = self.stream.read()
        self.is_running = self.grabbed
        self.thread = Thread(target=self.update, args=())
        self.thread.daemon = True 
        self.thread.start()

    def update(self):
        """Continuously reads frames, keeping only the newest."""
        while self.is_running:
            (grabbed, frame) = self.stream.read()
            if not grabbed:
                self.is_running = False
                break
            
            # Critical: Overwrite the previous frame with the new one
            self.frame = frame 
        
        self.stream.release()

    def read(self):
        """Returns the most recently grabbed frame."""
        return self.grabbed, self.frame

    def isOpened(self):
        return self.is_running

    def stop(self):
        """Stops the thread and releases resources."""
        self.is_running = False
        self.thread.join()
        self.stream.release()


# --- 8. Initialize Video Stream 📹 ---
print(f"\nAttempting to connect to camera stream at: {config.CAMERA_URL}")

# Initialize the stream using the threaded class
video_stream = VideoStream(config.CAMERA_URL)

if not video_stream.isOpened():
    print("Warning: Could not open video stream. Falling back to default webcam...")
    video_stream = VideoStream(0) # Fallback to default webcam
    if not video_stream.isOpened():
          print("FATAL ERROR: Default webcam also failed. Exiting.")
          sys.exit()

cv2.namedWindow(WINDOW_NAME)
cv2.setMouseCallback(WINDOW_NAME, draw_roi)


# --- 9. Main Video Processing Loop 💻 ---
print(f"\nStarting live recognition. Press 'q' to quit.")
print(f"Check-in window (ON TIME): {config.CHECK_IN_START.strftime('%H:%M')} - {config.CHECK_IN_END.strftime('%H:%M')}")
print(f"Check-in window (LATE): {config.LATE_THRESHOLD.strftime('%H:%M')} - {config.LATE_CHECK_IN_END.strftime('%H:%M')}")
print(f"Check-out window: {config.CHECK_OUT_START.strftime('%H:%M')} - {config.CHECK_OUT_END.strftime('%H:%M')}")

while True:
    ret, frame = video_stream.read() # Use the threaded stream's read method
    if not ret:
        time.sleep(0.1)
        continue

    frame_counter += 1

    run_yolo_this_frame = (frame_counter % config.YOLO_FRAME_SKIP == 0)
    run_face_rec_this_frame = (frame_counter % config.FACE_RECOGNITION_SKIP == 0)

    # 9.1. Pre-process Frame
    display_frame = cv2.resize(frame, config.OUTPUT_SIZE, interpolation=cv2.INTER_LINEAR)
    rx, ry, rw, rh = config.ROI_COORDINATES

    # --- 9.2. Body Detection (YOLO) - Uses TOP-CENTER POINT ---
    yolo_detections = []
    if run_yolo_this_frame:
        yolo_detections = run_yolo_detection(display_frame)

    body_detected_in_roi = False
    
    # Check YOLO detections against ROI (TOP-CENTER rule)
    yolo_boxes_in_roi = [] 
    for det in yolo_detections:
        x, y, w, h = det['box']
        # Get the top point for the body detection check
        check_point = get_check_point(det['box'], point_type='top')
        
        if is_point_in_roi(check_point, config.ROI_COORDINATES):
            body_detected_in_roi = True
            yolo_boxes_in_roi.append({'box': (x,y,w,h), 'check_point': check_point})


    # --- 9.3. Face Detection and Recognition ---
    face_locations = []
    face_encodings = []
    
    if run_face_rec_this_frame:
        # Extract ROI region only for faster processing
        roi_frame = display_frame[ry:ry+rh, rx:rx+rw]
        small_roi = cv2.resize(roi_frame, (0, 0), fx=config.RESIZE_FACTOR, fy=config.RESIZE_FACTOR)
        rgb_small_roi = cv2.cvtColor(small_roi, cv2.COLOR_BGR2RGB)

        # Run face detection on ROI only with reduced upsampling for speed
        face_locations = face_recognition.face_locations(rgb_small_roi, number_of_times_to_upsample=0)
        face_encodings = face_recognition.face_encodings(rgb_small_roi, face_locations)

    current_face_names = []
    admin_detected_this_frame = False

    for face_encoding, face_location in zip(face_encodings, face_locations):
        
        # Adjust face location coordinates to account for ROI offset
        t, r, b, l = face_location
        # Add ROI offset to get coordinates in full display_frame space
        t_adjusted = t + int(ry * config.RESIZE_FACTOR)
        r_adjusted = r + int(rx * config.RESIZE_FACTOR)
        b_adjusted = b + int(ry * config.RESIZE_FACTOR)
        l_adjusted = l + int(rx * config.RESIZE_FACTOR)
        
        face_bbox_scaled = (l_adjusted, t_adjusted, r_adjusted - l_adjusted, b_adjusted - t_adjusted)
        
        # Get the top point for the face detection check 
        face_top_center_point = get_check_point(face_bbox_scaled, resize_factor=config.RESIZE_FACTOR, point_type='top')

        if not is_point_in_roi(face_top_center_point, config.ROI_COORDINATES):
             continue

        matches = face_recognition.compare_faces(known_face_encodings, face_encoding,
                                                 tolerance=config.FACE_RECOGNITION_TOLERANCE)

        name = "Unknown"
        
        # Store adjusted face location for stability tracking and drawing
        face_location_adjusted = (t_adjusted, r_adjusted, b_adjusted, l_adjusted)

        if True in matches:
            distances = face_recognition.face_distance(known_face_encodings, face_encoding)
            best_match_index = np.argmin(distances)
            name = known_face_names[best_match_index]
            
            admin_detected_this_frame = True
            # Call the COMBINED notification logic (ESP8266 + Attendance)
            # Use adjusted coordinates!
            notify_detection(name, display_frame, face_location_adjusted)

        current_face_names.append(name)
        
        # --- Drawing face box and top point ---
        # Use adjusted coordinates for drawing
        top, right, bottom, left = [int(val / config.RESIZE_FACTOR) for val in face_location_adjusted]
        color = (0, 255, 0) if name != "Unknown" else (0, 255, 255) 
        cv2.rectangle(display_frame, (left, top), (right, bottom), color, 2)
        cv2.putText(display_frame, name, (left + 6, bottom - 6), cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 255), 1)
        
        # Draw the top-center point for faces (Red Dot)
        cv2.circle(display_frame, face_top_center_point, 5, (0, 0, 255), -1) 


    # 9.4. Customer Presence Logic (Rule B)
    
    # Logic 1: Admin priority - if admin is detected, it overrides customer and resets state.
    if admin_detected_this_frame:
        customer_presence_start_time = None
        is_customer_notified = False

    # Logic 2: If no Admin is seen BUT a Body is present (Customer Fallback)
    else:
        # Handle grace period for stability tracker
        keys_to_remove = []
        for name, tracker in face_stability_tracker.items():
            tracker['missed_frames'] += 1
            if tracker['missed_frames'] > 5: # Allow missing for 5 checks
                keys_to_remove.append(name)
        
        for name in keys_to_remove:
            del face_stability_tracker[name]

        if body_detected_in_roi and not admin_detected_this_frame:
            
            if customer_presence_start_time is None:
                customer_presence_start_time = time.time()
                
            elapsed_time = time.time() - customer_presence_start_time
            
            if elapsed_time >= config.CUSTOMER_PRESENCE_WAIT_TIME and not is_customer_notified:
                notify_detection("Customer", display_frame, face_location=None) 
                is_customer_notified = True
            
        
    # Logic 3: No Admin face AND no Body in ROI: reset timer.
    if not body_detected_in_roi and not admin_detected_this_frame:
        customer_presence_start_time = None
        is_customer_notified = False
        

    # 9.5. Drawing and Stats
    
    # Draw non-face body detection boxes 
    if not admin_detected_this_frame:
        for det_info in yolo_boxes_in_roi: 
            x, y, w, h = det_info['box']
            check_point = det_info['check_point']

            # Draw a green box for presence detection
            cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(display_frame, "Presence", (x, y - 10), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 255, 0), 2)
            
            # Draw the check point for bodies (Green Dot is the Top Point)
            cv2.circle(display_frame, check_point, 5, (0, 255, 0), -1) 
                 
    # Draw ROI
    cv2.rectangle(display_frame, (rx, ry), (rx + rw, ry + rh), (255, 0, 0), 2)
    cv2.putText(display_frame, "ROI", (rx + 5, ry + 25), cv2.FONT_HERSHEY_DUPLEX, 0.7, (255, 0, 0), 2)
    
    # Draw timer status
    if customer_presence_start_time is not None:
        current_time_val = time.time() - customer_presence_start_time
        timer_color = (0, 0, 255) if current_time_val < config.CUSTOMER_PRESENCE_WAIT_TIME else (0, 255, 0)
        
        timer_text = f"Customer Timer: {min(current_time_val, config.CUSTOMER_PRESENCE_WAIT_TIME):.1f}/{config.CUSTOMER_PRESENCE_WAIT_TIME:.1f}s"
        cv2.putText(display_frame, timer_text, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, timer_color, 2)
        
    # Draw ESP8266 cooldown status
    for name, last_time in last_esp_notified_time.items():
        cooldown_remaining = max(0, config.ADMIN_COOLDOWN_TIME - (time.time() - last_time))
        if cooldown_remaining > 0:
            cooldown_text = f"{name} ESP Cooldown: {cooldown_remaining:.1f}s"
            cv2.putText(display_frame, cooldown_text, (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

    # Display current time and stats
    current_time_str = datetime.now().strftime("%H:%M:%S")
    stats_text = f"Time: {current_time_str} | Faces: {len(current_face_names)} | Bodies: {len(yolo_boxes_in_roi)}"
    cv2.putText(display_frame, stats_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    if drawing:
        cv2.rectangle(display_frame, (ix, iy), (current_x, current_y), (255, 255, 255), 2)

    cv2.imshow(WINDOW_NAME, display_frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# --- 10. Cleanup 🧹 ---
video_stream.stop() # Stop the thread
cv2.destroyAllWindows()
print("\nProgram finished.")
