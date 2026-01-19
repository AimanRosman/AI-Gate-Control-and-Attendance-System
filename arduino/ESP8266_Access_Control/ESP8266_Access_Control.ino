#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <SPI.h>
#include <MFRC522.h>
#include <EEPROM.h>
#include <SoftwareSerial.h>

// ---------------- WiFi Configuration ----------------
// TODO: Enter your WiFi credentials here
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// ---------------- Pin Definitions ----------------
#define RST_PIN    D0  // GPIO5
#define SS_PIN     D8  // GPIO15
#define RELAY_PIN  D1  // GPIO4

// DFPlayer Mini pins
#define DFPLAYER_RX 3 // Connect to DFPlayer TX
#define DFPLAYER_TX 1 // Connect to DFPlayer RX

// ---------------- Settings ----------------
#define RELAY_ACTIVE_HIGH true
#define RELAY_ON_TIME_MS   5000
#define REG_MODE_TIMEOUT_MS 15000
#define MAX_CARDS 50
#define EEPROM_SIZE 512

// DFPlayer audio file indices
// Adjust these indices to match your SD card files (0001.mp3, 0002.mp3, etc.)
#define AUDIO_BEEP_FAIL      1
#define AUDIO_CUSTOMER       2
#define AUDIO_ADMIN_1        3 
#define AUDIO_ADMIN_2        4 
#define AUDIO_ADMIN_3        5 
#define AUDIO_ADMIN_4        6 
#define AUDIO_ADMIN_5        7 
#define AUDIO_ADMIN_6        8 
#define AUDIO_BEEP_OK        9
#define AUDIO_BOSS           10
#define AUDIO_ATTENDANCE     11
#define AUDIO_FAILURE        12

// Clock In Audio (Mappings for specific users)
#define AUDIO_ADMIN_1_CLOCKIN   13 
#define AUDIO_ADMIN_2_CLOCKIN   14 
#define AUDIO_ADMIN_3_CLOCKIN   15 
#define AUDIO_ADMIN_4_CLOCKIN   16 

#define AUDIO_MASTER            17 
#define AUDIO_REGISTER          18 

// Clock Out Audio
#define AUDIO_ADMIN_1_CLOCKOUT   19
#define AUDIO_ADMIN_2_CLOCKOUT   20
#define AUDIO_ADMIN_3_CLOCKOUT   21
#define AUDIO_ADMIN_4_CLOCKOUT   22

MFRC522 mfrc522(SS_PIN, RST_PIN);
ESP8266WebServer server(80);
SoftwareSerial dfSerial(DFPLAYER_RX, DFPLAYER_TX);

bool regMode = false;
unsigned long regModeStarted = 0;

// ---------------- Function Declarations ----------------
void initEEPROM();
void handleMasterScan();
void activateRelay();
void playAudio(int fileNumber);
void sendDFCommand(byte command, byte param1, byte param2);
bool compareUID(byte a[4], byte b[4]);
bool isUIDEmpty(byte u[4]);
void readMasterUID(byte out[4]);
void writeMasterUID(byte in[4]);
byte readCount();
void writeCount(byte c);
int cardAddr(int index);
bool isAuthorized(byte uid[4]);
void addCard(byte uid[4]);
void removeCardAtIndex(byte idx);
void toggleAuthorizedCard(byte uid[4]);
void printStoredInfo();
void printUIDHex(byte uid[4]);

// Web Handlers
void handleRoot();
void handleCommand(); 
void handleReboot();
void handleNotFound();

// ---------------- DFPlayer Mini Functions ----------------
void initDFPlayer() {
  dfSerial.begin(9600);
  delay(1000);
  sendDFCommand(0x06, 0, 30); // Volume 30
  delay(100);
  Serial.println("DFPlayer Mini initialized");
}

void sendDFCommand(byte command, byte param1, byte param2) {
  byte buffer[10] = {0x7E, 0xFF, 0x06, command, 0x00, param1, param2, 0x00, 0x00, 0xEF};
  int sum = -(buffer[1] + buffer[2] + buffer[3] + buffer[4] + buffer[5] + buffer[6]);
  buffer[7] = (sum >> 8) & 0xFF;
  buffer[8] = sum & 0xFF;
  for (int i = 0; i < 10; i++) dfSerial.write(buffer[i]);
  delay(100);
}

void playAudio(int fileNumber) {
  sendDFCommand(0x03, 0, fileNumber);
  Serial.print("Playing audio file: ");
  Serial.println(fileNumber);
}

// ---------------- NEW HTML & WEB SERVER LOGIC ----------------

// Store HTML in Flash Memory (PROGMEM) to save RAM
const char INDEX_HTML[] PROGMEM = R"=====(
<!DOCTYPE html>
<html>
<head>
  <title>Access Control System</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { --primary: #2563eb; --bg: #f3f4f6; --card: #ffffff; --text: #1f2937; --success: #10b981; --danger: #ef4444; --warn: #f59e0b; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 20px; }
    .container { max-width: 800px; margin: 0 auto; }
    h1 { text-align: center; color: var(--primary); margin-bottom: 10px; }
    h2 { border-bottom: 2px solid var(--bg); padding-bottom: 10px; margin-top: 0; font-size: 1.2rem; color: #4b5563; }
    
    .card { background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); padding: 20px; margin-bottom: 20px; }
    .status-item { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 0.9rem; }
    .badge { background: #e5e7eb; padding: 2px 8px; border-radius: 12px; font-weight: bold; font-size: 0.8rem; }
    .badge.good { background: #d1fae5; color: #065f46; }
    
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
    
    button { border: none; padding: 12px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: all 0.2s; color: white; width: 100%; font-size: 0.9rem; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    button:active { transform: scale(0.96); }
    
    .btn-blue { background-color: var(--primary); }
    .btn-blue:hover { background-color: #1d4ed8; }
    .btn-green { background-color: var(--success); }
    .btn-green:hover { background-color: #059669; }
    .btn-red { background-color: var(--danger); }
    .btn-red:hover { background-color: #dc2626; }
    .btn-orange { background-color: var(--warn); }
    .btn-orange:hover { background-color: #d97706; }
    .btn-purple { background-color: #8b5cf6; }
    .btn-purple:hover { background-color: #7c3aed; }
    
    /* Toast Notification */
    #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 8px; padding: 16px; position: fixed; z-index: 1; left: 50%; bottom: 30px; transform: translateX(-50%); box-shadow: 0 4px 12px rgba(0,0,0,0.3); opacity: 0; transition: opacity 0.3s; }
    #toast.show { visibility: visible; opacity: 1; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Access Control</h1>
    
    <div class="card">
      <h2>System Status</h2>
      <div class="status-item"><span>WiFi Status</span> <span class="badge good" id="wifi-status">Connected</span></div>
      <div class="status-item"><span>IP Address</span> <span class="badge" id="ip-addr">%IP%</span></div>
      <div class="status-item"><span>ESP8266 Uptime</span> <span class="badge" id="uptime">%UPTIME% sec</span></div>
    </div>

    <div class="card">
      <h2>Admins & Customer (Access & Audio)</h2>
      <div class="grid">
        <button class="btn-blue" onclick="send('/admin1')">Admin 1</button>
        <button class="btn-blue" onclick="send('/admin2')">Admin 2</button>
        <button class="btn-blue" onclick="send('/admin3')">Admin 3</button>
        <button class="btn-blue" onclick="send('/admin4')">Admin 4</button>
        <button class="btn-blue" onclick="send('/admin5')">Admin 5</button>
        <button class="btn-blue" onclick="send('/admin6')">Admin 6</button>
        <button class="btn-blue" onclick="send('/boss')">Boss</button>
        <button class="btn-blue" onclick="send('/customer')">Customer</button>
      </div>
    </div>

    <div class="card">
      <h2>Clock In</h2>
      <div class="grid">
        <button class="btn-green" onclick="send('/admin1_clockin')">Admin 1 In</button>
        <button class="btn-green" onclick="send('/admin2_clockin')">Admin 2 In</button>
        <button class="btn-green" onclick="send('/admin3_clockin')">Admin 3 In</button>
        <button class="btn-green" onclick="send('/admin4_clockin')">Admin 4 In</button>
      </div>
    </div>

    <div class="card">
      <h2>Clock Out</h2>
      <div class="grid">
        <button class="btn-purple" onclick="send('/admin1_clockout')">Admin 1 Out</button>
        <button class="btn-purple" onclick="send('/admin2_clockout')">Admin 2 Out</button>
        <button class="btn-purple" onclick="send('/admin3_clockout')">Admin 3 Out</button>
        <button class="btn-purple" onclick="send('/admin4_clockout')">Admin 4 Out</button>
      </div>
    </div>

    <div class="card">
      <h2>System Sounds</h2>
      <div class="grid">
        <button class="btn-orange" onclick="send('/attendance')">Attendance</button>
        <button class="btn-orange" onclick="send('/master')">Master Card</button>
        <button class="btn-orange" onclick="send('/register')">Register New Card</button>
        <button class="btn-red" onclick="send('/failure')">Attendance Fail</button>
        <button class="btn-red" onclick="send('/beep_fail')">Unregistered Card</button>
        <button class="btn-green" onclick="send('/beep_ok')">Registered Card</button>
      </div>
    </div>
    
    <div style="text-align: center; margin-top: 30px;">
       <button class="btn-red" style="width: auto; padding: 10px 30px;" onclick="reboot()">Reboot System</button>
    </div>
  </div>

  <div id="toast">Command Sent</div>

  <script>
    function send(endpoint) {
      showToast("Sending: " + endpoint.replace('/', ''));
      fetch(endpoint).then(resp => {
        if(resp.ok) console.log('OK');
      }).catch(err => {
        showToast("Error sending command");
      });
    }

    function reboot() {
      if(confirm("Are you sure you want to reboot?")) {
        send('/reboot');
        setTimeout(() => { location.reload(); }, 5000);
      }
    }

    function showToast(message) {
      var x = document.getElementById("toast");
      x.innerText = message;
      x.className = "show";
      setTimeout(function(){ x.className = x.className.replace("show", ""); }, 3000);
    }
  </script>
</body>
</html>
)=====";

void handleRoot() {
  String html = FPSTR(INDEX_HTML);
  html.replace("%IP%", WiFi.localIP().toString());
  html.replace("%UPTIME%", String(millis() / 1000));
  server.send(200, "text/html", html);
}

void handleCommand(const char* name, int audioID, bool triggerRelay) {
  Serial.print("Web Command: "); Serial.println(name);
  playAudio(audioID);
  if (triggerRelay) activateRelay();
  server.send(200, "text/plain", "OK");
}

void handleReboot() {
  server.send(200, "text/plain", "Rebooting...");
  delay(500);
  ESP.restart();
}

void handleNotFound() {
  server.send(404, "text/plain", "Not Found");
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  delay(100);
  
  Serial.println("\n\nESP8266 Access Control System Starting...");
  
  // Initialize WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  Serial.println(WiFi.localIP());
  
  initEEPROM();
  initDFPlayer();
  SPI.begin();
  mfrc522.PCD_Init();
  
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_HIGH ? LOW : HIGH);
  
  // --- Server Routes ---
  server.on("/", handleRoot);
  
  // Generic Admin Handlers
  server.on("/customer", []() { handleCommand("Customer", AUDIO_CUSTOMER, false); });
  server.on("/admin1", []() { handleCommand("Admin 1", AUDIO_ADMIN_1, true); });
  server.on("/admin2", []() { handleCommand("Admin 2", AUDIO_ADMIN_2, true); });
  server.on("/admin3", []() { handleCommand("Admin 3", AUDIO_ADMIN_3, true); });
  server.on("/admin4", []() { handleCommand("Admin 4", AUDIO_ADMIN_4, true); });
  server.on("/admin5", []() { handleCommand("Admin 5", AUDIO_ADMIN_5, true); });
  server.on("/admin6", []() { handleCommand("Admin 6", AUDIO_ADMIN_6, true); });
  server.on("/boss", []() { handleCommand("Boss", AUDIO_BOSS, true); });
  
  server.on("/attendance", []() { handleCommand("Attendance", AUDIO_ATTENDANCE, false); });
  
  // Clock In Handlers
  server.on("/admin1_clockin", []() { handleCommand("Admin 1 Clock In", AUDIO_ADMIN_1_CLOCKIN, false); });
  server.on("/admin2_clockin", []() { handleCommand("Admin 2 Clock In", AUDIO_ADMIN_2_CLOCKIN, false); });
  server.on("/admin3_clockin", []() { handleCommand("Admin 3 Clock In", AUDIO_ADMIN_3_CLOCKIN, false); });
  server.on("/admin4_clockin", []() { handleCommand("Admin 4 Clock In", AUDIO_ADMIN_4_CLOCKIN, false); });
  
  // Clock Out Handlers
  server.on("/admin1_clockout", []() { handleCommand("Admin 1 Clock Out", AUDIO_ADMIN_1_CLOCKOUT, false); });
  server.on("/admin2_clockout", []() { handleCommand("Admin 2 Clock Out", AUDIO_ADMIN_2_CLOCKOUT, false); });
  server.on("/admin3_clockout", []() { handleCommand("Admin 3 Clock Out", AUDIO_ADMIN_3_CLOCKOUT, false); });
  server.on("/admin4_clockout", []() { handleCommand("Admin 4 Clock Out", AUDIO_ADMIN_4_CLOCKOUT, false); });
  
  // Relay-only endpoints
  server.on("/admin1_relay", []() { handleCommand("Admin 1 Relay Only", 0, true); });
  server.on("/admin2_relay", []() { handleCommand("Admin 2 Relay Only", 0, true); });
  server.on("/admin3_relay", []() { handleCommand("Admin 3 Relay Only", 0, true); });
  server.on("/admin4_relay", []() { handleCommand("Admin 4 Relay Only", 0, true); });
  server.on("/admin5_relay", []() { handleCommand("Admin 5 Relay Only", 0, true); });
  server.on("/admin6_relay", []() { handleCommand("Admin 6 Relay Only", 0, true); });
  server.on("/boss_relay", []() { handleCommand("Boss Relay Only", 0, true); });
  
  server.on("/failure", []() { handleCommand("Failure", AUDIO_FAILURE, false); });
  server.on("/beep_ok", []() { handleCommand("Beep OK", AUDIO_BEEP_OK, false); });
  server.on("/beep_fail", []() { handleCommand("Beep Fail", AUDIO_BEEP_FAIL, false); });
  server.on("/master", []() { handleCommand("Master", AUDIO_MASTER, false); });
  server.on("/register", []() { handleCommand("Register", AUDIO_REGISTER, false); });
  
  server.on("/reboot", handleReboot);
  server.onNotFound(handleNotFound);
  
  server.begin();
  Serial.println("Web server started");
  
  printStoredInfo();
}

// ---------------- Main Loop ----------------
void loop() {
  server.handleClient();
  
  if (!mfrc522.PICC_IsNewCardPresent()) return;
  if (!mfrc522.PICC_ReadCardSerial()) return;

  byte uid[4];
  for (byte i = 0; i < 4; i++) uid[i] = mfrc522.uid.uidByte[i];

  Serial.print("Card read: ");
  printUIDHex(uid);

  byte master[4];
  readMasterUID(master);

  if (isUIDEmpty(master)) {
    Serial.println("No master set. This card becomes MASTER.");
    writeMasterUID(uid);
    playAudio(AUDIO_MASTER);
    delay(500);
    return;
  }

  if (compareUID(uid, master)) {
    handleMasterScan();
    mfrc522.PICC_HaltA();
    return;
  }

  if (regMode) {
    toggleAuthorizedCard(uid);
  } else {
    if (isAuthorized(uid)) {
      Serial.println("Authorized -> opening gate");
      playAudio(AUDIO_BEEP_OK); 
      activateRelay();
    } else {
      Serial.println("Unauthorized card");
      playAudio(AUDIO_BEEP_FAIL);
    }
  }

  mfrc522.PICC_HaltA();

  if (regMode && (millis() - regModeStarted) > REG_MODE_TIMEOUT_MS) {
    regMode = false;
    Serial.println("Registration mode timed out.");
  }
}

// ---------------- EEPROM & RFID Functions ----------------
void initEEPROM() {
  EEPROM.begin(EEPROM_SIZE);
  byte count = EEPROM.read(4);
  if (count > MAX_CARDS) {
    for (int i = 0; i < EEPROM_SIZE; i++) EEPROM.write(i, 0xFF);
    EEPROM.write(4, 0);
    EEPROM.commit();
    Serial.println("EEPROM initialized");
  }
}

void handleMasterScan() {
  if (!regMode) {
    regMode = true;
    regModeStarted = millis();
    Serial.println("MASTER -> Registration mode ON");
    playAudio(AUDIO_MASTER);
  } else {
    regMode = false;
    Serial.println("MASTER -> Registration mode CANCELLED");
    playAudio(AUDIO_BEEP_OK);
  }
}

void activateRelay() {
  Serial.println("Relay activated");
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_HIGH ? HIGH : LOW);
  delay(RELAY_ON_TIME_MS);
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_HIGH ? LOW : HIGH);
  Serial.println("Relay deactivated");
}

void printUIDHex(byte uid[4]) {
  for (byte i = 0; i < 4; i++) {
    if (uid[i] < 0x10) Serial.print("0");
    Serial.print(uid[i], HEX);
    if (i < 3) Serial.print(":");
  }
  Serial.println();
}

bool compareUID(byte a[4], byte b[4]) {
  for (byte i = 0; i < 4; i++) if (a[i] != b[i]) return false;
  return true;
}

bool isUIDEmpty(byte u[4]) {
  for (byte i = 0; i < 4; i++) if (u[i] != 0xFF && u[i] != 0x00) return false;
  return true;
}

void readMasterUID(byte out[4]) {
  for (int i = 0; i < 4; i++) out[i] = EEPROM.read(i);
}

void writeMasterUID(byte in[4]) {
  for (int i = 0; i < 4; i++) EEPROM.write(i, in[i]);
  EEPROM.commit();
}

byte readCount() {
  return EEPROM.read(4);
}

void writeCount(byte c) {
  EEPROM.write(4, c);
  EEPROM.commit();
}

int cardAddr(int index) {
  return 5 + index * 4;
}

bool isAuthorized(byte uid[4]) {
  byte cnt = readCount();
  for (byte i = 0; i < cnt; i++) {
    byte stored[4];
    int addr = cardAddr(i);
    for (int b = 0; b < 4; b++) stored[b] = EEPROM.read(addr + b);
    if (compareUID(uid, stored)) return true;
  }
  return false;
}

void addCard(byte uid[4]) {
  byte cnt = readCount();
  if (cnt >= MAX_CARDS) return;
  int addr = cardAddr(cnt);
  for (int b = 0; b < 4; b++) EEPROM.write(addr + b, uid[b]);
  writeCount(cnt + 1);
  EEPROM.commit();
  Serial.print("Card ADDED: ");
  printUIDHex(uid);
  playAudio(AUDIO_REGISTER); 
}

void removeCardAtIndex(byte idx) {
  byte cnt = readCount();
  if (idx >= cnt) return;
  for (int i = idx; i < cnt - 1; i++) {
    byte next[4];
    int nextAddr = cardAddr(i + 1);
    for (int b = 0; b < 4; b++) next[b] = EEPROM.read(nextAddr + b);
    int addr = cardAddr(i);
    for (int b = 0; b < 4; b++) EEPROM.write(addr + b, next[b]);
  }
  int lastAddr = cardAddr(cnt - 1);
  for (int b = 0; b < 4; b++) EEPROM.write(lastAddr + b, 0xFF);
  writeCount(cnt - 1);
  EEPROM.commit();
}

void toggleAuthorizedCard(byte uid[4]) {
  byte cnt = readCount();
  for (byte i = 0; i < cnt; i++) {
    byte stored[4];
    int addr = cardAddr(i);
    for (int b = 0; b < 4; b++) stored[b] = EEPROM.read(addr + b);
    if (compareUID(uid, stored)) {
      removeCardAtIndex(i);
      Serial.print("Card REMOVED: ");
      printUIDHex(uid);
      playAudio(AUDIO_BEEP_FAIL); 
      return;
    }
  }
  addCard(uid);
}

void printStoredInfo() {
  Serial.println("---- Stored Data ----");
  byte master[4];
  readMasterUID(master);
  Serial.print("Master: ");
  if (isUIDEmpty(master)) Serial.println("<not set>");
  else printUIDHex(master);
  Serial.print("Count: "); Serial.println(readCount());
  Serial.println("---------------------");
}
