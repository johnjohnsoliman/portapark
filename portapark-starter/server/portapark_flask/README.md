# PortaPark v5 — AI-Lead Smart Portable Parking Monitor (Flask Server)

## What's New in PortaPark v5
1. **Gemini API Takes the Lead (`ai.py` + `app.py`)**:
   - Whenever `key.txt` is present and internet is active, **Gemini API is the Primary Lead Classifier** (`CONFIRM_GEMINI = 1` for instant response) and identifies 9 specific classes: `car`, `motorcycle`, `tricycle`, `bicycle`, `truck_or_van`, `person`, `animal`, `object`, and `empty`, plus vehicle color and proper line alignment (`parked_properly`).
   - Clicking **"Ask Gemini API Now"** immediately re-evaluates fusion and updates the slot status on the dashboard without waiting for 3 ESP32 cycles.
   - Automatic wake-up triggers a Gemini check immediately when the first reading arrives or whenever the ultrasonic sensors cross the `< 100 cm` threshold.
   - Smart frame caching prevents multiple browsers and Gemini from overloading the ESP32-CAM's HTTP server.
2. **Redesigned User-Friendly Dashboard (`/` and `/campus`)**:
   - **`http://localhost:5000/`**: Full Single-Slot AI + IoT Monitor showing the 3-step decision pipeline (**1. Gemini Cloud AI Lead**, **2. Ultrasonic Sensors Backup**, **3. Edge Impulse Offline Fallback**), live camera feed, exact vehicle class & alignment badges, and a built-in **Test Lab** simulator.
   - **`http://localhost:5000/campus`**: Your NEUST Sumacab Campus Lot Map (`PortaPark.html`) now connected live to Flask `/readings`!

## Quick Start
1. Install dependencies:
   ```
   pip install flask google-genai
   ```
2. Put your Google AI Studio API key in `key.txt` (one line, no spaces or quotes).
3. Double-click `start_demo.bat` (or run `python app.py`).
4. Open `http://localhost:5000` on your laptop or `http://LAPTOP-IP:5000` on your phone.
