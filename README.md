# PortaPark: AI + IoT portable parking slot monitor

PortaPark watches **one parking slot** and tells you what is in it. An ESP32-CAM takes photos, the
**Gemini vision API** identifies what it sees (an on-device **Edge Impulse** model is the offline
backup), two **HC-SR04** ultrasonic sensors give a distance reading, and a **Flask** web dashboard shows
the live status, the camera photo, and usage statistics on any phone or laptop on the same network.

Made as an IoT and Machine Learning course project (NEUST, Philippines, 2026).

<!-- EDIT: add a screenshot of the dashboard, e.g.  ![Dashboard](docs/dashboard.png) -->

## Features

- Identifies: car, motorcycle, tricycle, bicycle, truck or van, person, animal, other object, or empty
- Says whether a vehicle is parked straight inside the lines
- Combines the AI answer, the distance sensors, and the offline Edge Impulse model, and keeps working
  when the internet or the AI limit is unavailable
- Live photo, status, cars arrived today, average stay, and occupancy by hour (SQLite history)
- Portable: one camera and two sensors on a foldable frame, powered from a power bank

## How it works

```
Uno + 2 x HC-SR04  --serial-->  ESP32-CAM  --WiFi (HTTP/JSON)-->  Flask server  -->  Dashboard in a browser
                                (camera + Edge Impulse model)      (SQLite + Gemini)
```

<!-- EDIT: if your final build reads the Uno over USB on the PC, change the diagram to match. -->

## Try it without any hardware

You only need Python 3.10 or newer.

```bash
git clone https://github.com/YOUR-USERNAME/portapark.git
cd portapark/server
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:5000>. The dashboard has a **Test Lab** with buttons that simulate cars,
people, and empty slots, so you can see how the status, analytics, and fallback logic behave.
On Windows you can also double-click `start_demo.bat`.

**Optional: turn on the Gemini AI.** Get a free key from [Google AI Studio](https://aistudio.google.com),
copy `key.txt.example` to `key.txt`, and paste the key on the first line. Never commit `key.txt`
(it is in `.gitignore`). Without a key the server runs in backup mode.

## Build the hardware

**Parts:** ESP32-CAM (AI-Thinker, OV2640, with a USB-C programmer base), Arduino Uno R3, two HC-SR04
sensors, a 1 kOhm and a 2 kOhm resistor, jumper wires, a 5 V 2 A power supply, and a frame or pole.
Wiring is in [`docs/WIRING.md`](docs/WIRING.md).

**Firmware setup**

1. Install Arduino IDE 2.x. In Boards Manager install **esp32 by Espressif Systems, version 3.x** (tested with 3.3.12).
2. Install the Edge Impulse library from [`model/`](model/) (Sketch > Include Library > Add .ZIP Library).
3. Copy `firmware/esp32cam/secrets.h.example` to `firmware/esp32cam/PortaPark_ESP32CAM/secrets.h` and fill in your 2.4 GHz WiFi name, password, and your PC's address (run `ipconfig` and use the IPv4 address, for example `http://192.168.1.10:5000/api/reading`).
4. Open `PortaPark_ESP32CAM.ino`. Tools: Board **AI Thinker ESP32-CAM**, PSRAM **Enabled**, Partition Scheme **Huge APP (3MB No OTA)**. Upload.
5. Open `PortaPark_Uno_Sensors.ino`, choose Board **Arduino Uno**, and upload.
6. Start the server, power both boards, and watch the dashboard fill in within a few seconds.


## Settings

| Setting | Where | What it does |
|---|---|---|
| `key.txt` or `GEMINI_API_KEY` | server | your Gemini API key |
| `GEMINI_MODEL` | environment variable | which Gemini model to use |
| `TAKEN_CM` | `server/app.py` | a sensor closer than this (cm) means something is in the slot |

## Limitations

- Monitors a single slot. Tested at a small scale, not in weather or at night.
- The AI answer depends on camera quality, lighting, and the internet connection.
- The Edge Impulse model was trained on a small dataset, so its accuracy is limited.

## Security

Never commit `key.txt`, `secrets.h`, WiFi passwords, or photos of people. If a key is ever committed or shared,
delete it in Google AI Studio and create a new one.

## License

MIT. See [`LICENSE`](LICENSE).
