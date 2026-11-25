# Firmware

| Folder | Board | Sketch |
|---|---|---|
| `esp32cam/PortaPark_ESP32CAM/` | ESP32-CAM (AI-Thinker) | camera, Edge Impulse model, WiFi, photo and status pages |
| `uno/PortaPark_Uno_Sensors/` | Arduino Uno R3 | reads two HC-SR04 sensors |

Arduino requires each sketch to sit in a folder with the same name as the `.ino` file.
Copy `esp32cam/secrets.h.example` to `esp32cam/PortaPark_ESP32CAM/secrets.h` and edit it.
