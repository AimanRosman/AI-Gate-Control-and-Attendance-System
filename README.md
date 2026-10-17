# AI Gate Control and Attendance System

A computer vision and embedded systems prototype that connects face recognition, attendance records, RFID access, and ESP8266 relay control. Python processes a camera feed with OpenCV, `face_recognition`, and YOLOv8; the ESP8266 handles RFID cards, audio feedback, and the relay.

This project demonstrates integration across vision, HTTP communication, microcontroller firmware, and Google APIs. It is a development prototype: the current HTTP controls have no authentication, and the face recognition flow does not implement liveness detection. Use a disconnected demonstration relay and an isolated network while evaluating it.

## How it works

```text
Camera -> Python vision pipeline -> recognition and attendance rules
                                  |-> Google Sheets + configured image host
                                  `-> HTTP -> ESP8266 -> audio / relay
RFID card -------------------------------> ESP8266 -> relay
```

- Detects people within an adjustable region of interest and matches faces against locally supplied images.
- Applies configured check-in, late arrival, and check-out windows, with local daily status tracking.
- Uploads attendance images through ImgBB, Imgur, or Google Drive and records their links in Google Sheets.
- Queues audio requests and sends relay commands to named firmware endpoints.
- Provides a local ESP8266 web page for commands and an RFID enrollment flow backed by EEPROM.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`python/main.py`](python/main.py) | Camera processing, attendance rules, cloud uploads, and device requests |
| [`python/config.py.example`](python/config.py.example) | Local configuration template |
| [`python/service_account.json.example`](python/service_account.json.example) | Placeholder Google service account document |
| [`python/requirements.txt`](python/requirements.txt) | Python dependencies |
| [`arduino/ESP8266_Access_Control/ESP8266_Access_Control.ino`](arduino/ESP8266_Access_Control/ESP8266_Access_Control.ino) | ESP8266 firmware and web UI |

Face images, model downloads, real credentials, and audio recordings must be supplied locally.

## Hardware and software

- A desktop Python environment with a graphical display, OpenCV-compatible camera input, and enough resources for dlib and YOLO inference. The dependency file does not pin a tested Python or package combination.
- An ESP8266 board with Arduino ESP8266 support, an RC522 RFID reader, a relay module, and optional DFPlayer Mini, speaker, and microSD card.
- Arduino libraries: `MFRC522`; the sketch also uses the ESP8266 core, SPI, EEPROM, and SoftwareSerial. DFPlayer commands are sent directly over serial.
- A Google service account, a Google Sheet, and credentials for the selected image upload service.

Check the pin definitions against your exact board before wiring. The sketch assigns relay `D1`, RFID reset `D0`, RFID select `D8`, and DFPlayer RX/TX GPIO `3`/`1`; the DFPlayer pins overlap the hardware serial pins. Power actuators appropriately and begin with the lock disconnected.

## Python setup

From the repository root:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r python/requirements.txt
```

`face_recognition` depends on dlib and may require native compiler tools. Copy `python/config.py.example` to `python/config.py`, then configure:

| Setting | What to provide |
| --- | --- |
| `CAMERA_URL` | An RTSP URL or OpenCV camera index; keep embedded camera credentials private |
| `OUTPUT_SIZE`, `ROI_COORDINATES` | Processing dimensions and a valid region inside that image |
| `ESP8266_IP`, `ESP8266_PORT` | Address of the device on the isolated demo network |
| `FACE_DATA_DIR` | Local enrollment image directory, relative to the working directory |
| Attendance time settings | Local check-in/check-out schedule |
| `SERVICE_ACCOUNT_FILE`, `SPREADSHEET_NAME` | Local Google key file and shared spreadsheet name |
| Image upload settings | Enable exactly the intended backend and provide its credentials |

For Google Sheets, enable the Sheets and Drive APIs in your Google Cloud project, download a service account key to `python/service_account.json`, and share the target spreadsheet with that account. Use these first-row columns, in this order:

```text
Date | Name | Check-in Time | Status | Check-out Time | Check-in Image | Check-out Image
```

Create enrollment folders such as `python/known_faces/Admin1/` and add consented `.jpg`, `.jpeg`, or `.png` images. Folder names are normalized with Python's `capitalize()` and then lowercased for HTTP requests. Match them to firmware routes: the supplied sketch defines `admin1` through `admin6` and `boss`; specialized clock-in/out audio exists for `admin1` through `admin4`. Update `ADMINS_WITH_AUDIO` to the matching normalized names, for example `Admin1`.

## Firmware setup

1. Open the `.ino` file in Arduino IDE and select your ESP8266 board.
2. Set the placeholder Wi-Fi credentials locally. Do not commit the edited credentials.
3. Review relay polarity, GPIO assignments, and audio index definitions.
4. Supply audio tracks matching the `AUDIO_*` indices and test playback on the DFPlayer.
5. Flash the firmware and read the device address from the serial monitor at 115200 baud.

On an uninitialized card database, the first scanned RFID card becomes the master. Scanning the master toggles enrollment mode; scanning another card in that mode adds or removes its UID. Protect physical access during enrollment.

## Run and controls

Run from `python/` so relative configuration and data paths resolve correctly:

```bash
cd python
python main.py
```

The application can download `yolov8n.pt` through Ultralytics when the weights are not already present. It opens an OpenCV window, attempts the configured camera, and falls back to camera `0` if opening fails.

- Drag the mouse to set the region of interest. **Changing it also resets the local day's check-in/check-out status.**
- Press `q` to close the application.
- Open the device's HTTP address to view the command page on the isolated network.

## Security, privacy, and limitations

- The firmware's unlock and reboot routes accept unauthenticated HTTP requests. Network access is sufficient to operate them; this needs authentication, transport protection, and request authorization before real access control use.
- Face matching alone is not proof of a live person. There is no anti-spoofing mechanism or documented recognition accuracy evaluation.
- Attendance uploads contain identifying images and names. The Google Drive upload explicitly grants `anyone` reader access; ImgBB and Imgur also return image links for spreadsheet display. Review this behavior before using real people or private attendance records.
- `config.py`, service account keys, face images, and daily status files are excluded by `.gitignore`. An ignore rule does not remove files already committed to Git history.
- Cloud upload and sheet failures can prevent attendance completion. There is no durable offline retry queue, and image upload requests do not define timeouts.
- RFID authorization compares four UID bytes; it is a prototype identity mechanism, not a secure card authentication protocol.
- Package versions are not locked. No automated test suite, performance benchmark, or hardware compatibility matrix is included.

## Validation

Before a live demonstration, test camera access, a known and unknown face, exact Python-to-firmware route names, time-window boundaries, cloud failure behavior, and relay polarity with the real lock disconnected. Verify that Google Sheet rows and uploaded image permissions match your intended privacy policy. The repository does not include an automated end-to-end test.

## License

Released under the [MIT License](LICENSE). External libraries, models, and service providers retain their own terms.
