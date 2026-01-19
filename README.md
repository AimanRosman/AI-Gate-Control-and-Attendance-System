# AI Gate Control & Attendance System

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-ESP8266%20%7C%20Windows%2FLinux-lightgrey.svg)
![Status](https://img.shields.io/badge/status-Stable-green.svg)

An open-source, AI-powered access control and attendance tracking system using **Python (OpenCV, Face Recognition, YOLO)** and **ESP8266 (Relays, RFID, Audio)**.

## 🚀 Features

*   **Real-time Face Recognition**: Uses `face_recognition` (dlib) for high-accuracy identification.
*   **Body Detection**: Integrates `YOLOv8` to detect human presence.
*   **Smart Attendance Logging**: Automatically logs Check-in/Check-out times to **Google Sheets**.
*   **Image Evidence**: Captures and uploads face snapshots to **Google Drive**, **ImgBB**, or **Imgur**.
*   **Audio Feedback**: Plays personalized greeting messages via **DFPlayer Mini** on the ESP8266.
*   **Physical Access Control**: Controls an electric gate/door lock via a Relay.
*   **Web Interface**: ESP8266 hosts a web dashboard for manual control and status monitoring.
*   **RFID Support**: Fallback access using RC522 RFID module.

---

## 🛠️ Hardware Requirements

1.  **Computer/Server**: To run the Python computer vision script (Windows/Linux/Raspberry Pi 4+ recommended).
2.  **IP Camera or Webcam**: For video input (RTSP supported).
3.  **ESP8266 (NodeMCU/Wemos)**: The main microcontroller for hardware control.
4.  **DFPlayer Mini**: For playing audio files (MP3).
5.  **Relay Module**: To trigger the electronic lock/gate.
6.  **RC522 RFID Module** (Optional): For card-based access.
7.  **Speaker**: Connected to DFPlayer Mini.

---

## 📂 Project Structure

```
opensource_release/
├── python/
│   ├── main.py                 # Main computer vision & logic script
│   ├── config.py.example       # Configuration template (RENAME TO config.py)
│   ├── requirements.txt        # Python dependencies
│   ├── service_account.json.example # Google API credentials template
│   └── known_faces/            # Directory to store user face images (Create this!)
│
└── arduino/
    └── ESP8266_Access_Control/
        └── ESP8266_Access_Control.ino  # Firmware for ESP8266
```

---

## ⚙️ Installation & Setup

### 1. Python Environment (Computer)

1.  **Install Python 3.8+**.
2.  **Install Dependencies**:
    ```bash
    pip install -r python/requirements.txt
    ```
    *Note: Installing `dlib` (dependency of `face_recognition`) on Windows can be tricky. You may need Visual Studio C++ Build Tools.*

3.  **Configuration**:
    *   Rename `python/config.py.example` to `python/config.py`.
    *   Open `config.py` and edit it with your settings:
        *   **Camera URL**: RTSP link or `0` for webcam.
        *   **ESP8266 IP**: The IP address of your ESP8266 (displayed on Serial Monitor after boot).
        *   **API Keys**: Add credentials for ImgBB/Imgur if used.

4.  **Google Sheets Setup**:
    *   Create a project in [Google Cloud Console](https://console.cloud.google.com/).
    *   Enable **Google Sheets API** and **Google Drive API**.
    *   Create a **Service Account** and download the JSON key.
    *   Rename the JSON file to `service_account.json` and place it in the `python/` folder.
    *   Share your Google Sheet with the service account email address.

5.  **Add Users**:
    *   Create a folder named `known_faces` inside `python/`.
    *   Create subfolders for each person (e.g., `known_faces/Admin1`, `known_faces/Admin2`).
    *   Add clear face images (.jpg/.png) inside their respective folders.

### 2. Arduino/ESP8266 Setup

1.  Open `arduino/ESP8266_Access_Control/ESP8266_Access_Control.ino` in Arduino IDE.
2.  Install required libraries via Library Manager:
    *   `MFRC522`
    *   `DFRobotDFPlayerMini` (if using library, though code might use raw serial commands)
3.  **Edit WiFi Credentials**:
    *   Find `const char* ssid = "YOUR_WIFI_SSID";` at the top and update it.
4.  **Flash Firmware**: Upload the sketch to your ESP8266.
5.  **Audio Files**:
    *   Format a microSD card as FAT32.
    *   Copy MP3 files to the root or `mp3` folder (depending on DFPlayer structure).
    *   **Naming Convention**: Files must be named `0001.mp3`, `0002.mp3`... to match the IDs in `#define` section of the code.

---

## 🚀 Usage

1.  **Power on the ESP8266**.
2.  **Run the Python Script**:
    ```bash
    cd python
    python main.py
    ```
3.  The system will open a window showing the live feed.
    *   **Faces detected** in the `known_faces` folder will be recognized.
    *   **Attendance** will be logged to Google Sheets.
    *   **Audio** will play on the ESP8266.
    *   **Gate/Relay** will trigger for authorized users.

### Controls
*   **Draw ROI**: Click and drag on the video feed to define the Region of Interest.
*   **Quit**: Press `q` to exit.

---

## 🔒 Security Note

*   **Never commit `config.py` or `service_account.json` to a public repository.**
*   The `.gitignore` file included in this release is pre-configured to exclude these sensitive files.

## 📄 License

This project is open-source. Feel free to modify and distribute.
