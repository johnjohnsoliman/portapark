"""PortaPark Server — One-Sided Frame Ultrasonic Width & Area (Meters) + WiFi Camera + Auto-Gemini Vision."""
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request

import ai

app = Flask(__name__)
DB_FILE = "portapark.db"

# Locked to your Arduino Uno port (COM7) and ignores the ESP32-CAM port (COM5)
UNO_PORT = os.environ.get("UNO_PORT", "COM7").strip()

TAKEN_CM = 100.0        # 1.00 m threshold (< 1.00 m means object/vehicle closer than opposite stick)
SLOT_LENGTH_M = 5.0     # Default parking slot length in meters (5.0 m standard slot)
MAX_RANGE_CM = 400.0    # 4.00 m maximum ultrasonic range
CONFIRM_EDGE = 2
CONFIRM_GEMINI = 1

AI_INTERVAL = int(os.environ.get("AI_INTERVAL", "20"))
AI_MIN_GAP = 4.0
AI_FRESH = int(os.environ.get("AI_FRESH", "120"))
AI_CERTAINTY = {"high": 0.96, "medium": 0.85, "low": 0.68}

ai_backoff_until = 0
ai_wakeup = threading.Event()
last_ai_call_ts = 0

SYSTEM_CONFIG = {
    "mode": "ai_lead",
    "taken_cm": TAKEN_CM,
    "slot_length_m": SLOT_LENGTH_M,
}

# Live ultrasonic readings received directly from Arduino Uno over COM7 (stored in cm as float)
usb_sensor = {
    "d1": None,
    "d2": None,
    "at": 0.0,
    "port": None,
}

state = {}


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ts REAL, building TEXT, parking TEXT, slot INTEGER,
                 car REAL, empty REAL, person REAL, d1 REAL, d2 REAL,
                 raw TEXT, status TEXT, agree INTEGER, ai TEXT, src TEXT)"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(readings)")}
        for col in ("ai", "src"):
            if col not in cols:
                conn.execute("ALTER TABLE readings ADD COLUMN " + col + " TEXT")


def valid_distances(d1, d2):
    out = []
    for d in (d1, d2):
        if d is not None:
            try:
                val = float(d)
                if 2.0 <= val < 999.0:
                    out.append(val)
                elif val >= 999.0:
                    out.append(999.0)
            except Exception:
                pass
    return out


def cm_to_m(val_cm):
    """Convert centimeters to meters rounded to 2 decimal places (None if invalid/out of range)."""
    if val_cm is None:
        return None
    try:
        v = float(val_cm)
        if v < 0:
            return None
        if v >= 999.0:
            return 4.00
        return round(v / 100.0, 2)
    except Exception:
        return None


def compute_geometry(d1_cm, d2_cm):
    """Calculate Sensor 1 (m), Sensor 2 (m), Detected Width (m), and Parking Spot Area (m^2)."""
    d1_m = cm_to_m(d1_cm)
    d2_m = cm_to_m(d2_cm)
    length_m = float(SYSTEM_CONFIG.get("slot_length_m", SLOT_LENGTH_M))

    valid_m = [x for x in (d1_m, d2_m) if x is not None]
    if valid_m:
        width_m = round(sum(valid_m) / len(valid_m), 2)
        area_m2 = round(width_m * length_m, 2)
    else:
        width_m = None
        area_m2 = None

    return d1_m, d2_m, width_m, length_m, area_m2


def get_effective_distances(esp_d1, esp_d2):
    """Use direct USB Arduino Uno readings from COM7 if active (<60s), otherwise fallback to ESP32."""
    now = time.time()
    if usb_sensor["at"] and (now - usb_sensor["at"] <= 60.0):
        return usb_sensor["d1"], usb_sensor["d2"]
    return esp_d1, esp_d2


def fresh_ai(st):
    if SYSTEM_CONFIG["mode"] == "edge_only":
        return None
    a = st.get("ai") if st else None
    return a if a and (time.time() - a["at"] <= AI_FRESH) else None


def fuse(scores, d1, d2, ai_result=None):
    """Gemini API controls image classification; one-sided ultrasonic sensors measure width & obstacle distance in meters."""
    taken_cm = float(SYSTEM_CONFIG.get("taken_cm", TAKEN_CM))
    taken_m = round(taken_cm / 100.0, 2)
    ds = valid_distances(d1, d2)
    sensor_taken = None if not ds else (min(ds) < taken_cm)
    min_d_m = round(min(ds) / 100.0, 2) if ds else None
    mode = SYSTEM_CONFIG.get("mode", "ai_lead")

    edge_name, edge_conf = max(scores.items(), key=lambda kv: kv[1]) if scores else ("empty", 0.0)

    if isinstance(ai_result, dict) and mode != "edge_only" and ai_result.get("base") in ("car", "person", "object", "empty"):
        cam_base = ai_result["base"]
        detected_class = ai_result.get("label", cam_base)
        display_class = ai_result.get("display_label") or ai.display_name(detected_class)
        conf = AI_CERTAINTY.get(ai_result.get("certainty"), 0.85)
        source = "gemini"
        cam = cam_base
    else:
        cam_base = edge_name.lower()
        detected_class = cam_base
        display_class = ai.display_name(detected_class)
        conf = float(edge_conf)
        source = "edge"
        cam = cam_base

    if source == "gemini":
        if cam in ("car", "person", "object"):
            status = cam
            if sensor_taken is True:
                reason = f"Gemini Vision identified {display_class} + Ultrasonic sensor detected at {min_d_m:.2f} m."
            else:
                reason = f"Gemini Vision identified {display_class} in camera view."
        else:
            if mode == "ai_strict" or not sensor_taken:
                status = "free"
                display_class = "Empty (Free Slot)"
                reason = (
                    f"Slot is clear (Width to opposite stick: {min_d_m:.2f} m)."
                    if min_d_m is not None else "Gemini Vision confirms the parking slot is clear."
                )
            else:
                status = "car"
                detected_class = "object"
                display_class = "Obstacle in Sensor Zone"
                reason = f"Camera view is clear, but Ultrasonic Sensor detected an object at {min_d_m:.2f} m (< {taken_m:.2f} m)."
    else:
        if cam in ("car", "person", "object"):
            status = cam
            reason = f"Camera detected {display_class} (waiting for next Gemini Vision scan)."
        else:
            if sensor_taken:
                status = "car"
                detected_class = "car"
                display_class = "Occupied (Sensor Detected)"
                reason = f"Ultrasonic Sensor detected an object at {min_d_m:.2f} m (< {taken_m:.2f} m)."
            else:
                status = "free"
                display_class = "Empty (Free Slot)"
                reason = (
                    f"Slot is clear (Measured width: {min_d_m:.2f} m)."
                    if min_d_m is not None else "Slot is clear."
                )

    cam_taken = (cam != "empty")
    agree = None if sensor_taken is None else (sensor_taken == cam_taken)
    return status, agree, cam, conf, source, detected_class, display_class, reason


def smooth(key, raw, now, source="edge"):
    st = state.setdefault(
        key,
        {"status": "free", "pending": None, "count": 0, "since": now, "latest": {}},
    )
    needed = CONFIRM_GEMINI if source == "gemini" else CONFIRM_EDGE
    if raw == st["status"]:
        st["pending"], st["count"] = None, 0
    else:
        if st["pending"] == raw:
            st["count"] += 1
        else:
            st["pending"], st["count"] = raw, 1
        if st["count"] >= needed or st["status"] == "offline":
            st["status"], st["since"] = raw, now
            st["pending"], st["count"] = None, 0
    return st


def fetch_camera(ip, timeout=3.0):
    with urllib.request.urlopen(f"http://{ip}/capture", timeout=timeout) as r:
        return r.read()


def get_cached_or_fetch_camera(st, max_age=1.5):
    now = time.time()
    if st.get("cam_img") and (now - st.get("cam_img_at", 0) <= max_age):
        return st["cam_img"]
    ip = (st.get("latest") or {}).get("cam_ip")
    if not ip:
        return st.get("cam_img")
    img = fetch_camera(ip, timeout=3.0)
    if img:
        st["cam_img"] = img
        st["cam_img_at"] = now
    return img


def refresh_state_with_ai(key, st):
    lat = st.get("latest")
    if not lat:
        return
    now = time.time()
    ai_res = fresh_ai(st)
    scores = lat.get("scores") or {"car": 0.0, "empty": 0.0, "person": 0.0}
    d1, d2 = get_effective_distances(lat.get("d1"), lat.get("d2"))
    raw, agree, cam, conf, source, det_cls, disp_cls, reason = fuse(scores, d1, d2, ai_res)
    smooth(key, raw, now, source=source)
    lat.update({
        "status": st["status"],
        "raw": raw,
        "since": st["since"],
        "camera_says": cam,
        "camera_conf": round(conf, 3),
        "camera_source": source,
        "detected_class": det_cls,
        "display_class": disp_cls,
        "decision_reason": reason,
        "d1": d1,
        "d2": d2,
        "agree": agree,
    })


def apply_usb_sensor_reading(d1, d2):
    """Immediately update all slot states when new distances arrive from COM7."""
    now = time.time()
    prev_d1, prev_d2 = usb_sensor["d1"], usb_sensor["d2"]
    if d1 is not None:
        usb_sensor["d1"] = round(float(d1), 1)
    if d2 is not None:
        usb_sensor["d2"] = round(float(d2), 1)
    usb_sensor["at"] = now

    eff_d1 = usb_sensor["d1"]
    eff_d2 = usb_sensor["d2"]

    keys = list(state.keys()) if state else [("cict", "A", 1)]
    for key in keys:
        st = state.setdefault(
            key,
            {"status": "free", "pending": None, "count": 0, "since": now, "latest": {}},
        )
        lat = st["latest"]
        scores = lat.get("scores") or {"car": 0.0, "empty": 1.0, "person": 0.0}
        ai_res = fresh_ai(st)
        raw, agree, cam, conf, source, det_cls, disp_cls, reason = fuse(scores, eff_d1, eff_d2, ai_res)
        smooth(key, raw, now, source=source)

        lat.update({
            "building": key[0],
            "parking": key[1],
            "slot": key[2],
            "status": st["status"],
            "raw": raw,
            "since": st["since"],
            "at": now,
            "scores": scores,
            "camera_says": cam,
            "camera_conf": round(conf, 3),
            "camera_source": source,
            "detected_class": det_cls,
            "display_class": disp_cls,
            "decision_reason": reason,
            "edge_label": lat.get("edge_label", "empty"),
            "d1": eff_d1,
            "d2": eff_d2,
            "agree": agree,
            "cam_ip": lat.get("cam_ip"),
        })

    taken_cm = float(SYSTEM_CONFIG.get("taken_cm", TAKEN_CM))
    was_near = any(2 <= x < taken_cm for x in valid_distances(prev_d1, prev_d2))
    is_near = any(2 <= x < taken_cm for x in valid_distances(eff_d1, eff_d2))
    if was_near != is_near and ai.enabled() and SYSTEM_CONFIG["mode"] != "edge_only":
        ai_wakeup.set()


def parse_d_line(raw_line):
    """Parse 'D,45,240' format."""
    idx = raw_line.find("D,")
    if idx < 0:
        return None
    sub = raw_line[idx:].strip()
    parts = sub.split(",")
    if len(parts) >= 3:
        try:
            return round(float(parts[1].strip()), 1), round(float(parts[2].strip()), 1)
        except ValueError:
            return None
    return None


def parse_sensor_text_line(raw_line):
    """Parse 'Sensor 1: 54.18 cm - DETECTED' or 'Sensor 1: 0.54 m'."""
    m = re.search(r"sensor\s*([12])\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*(cm|m)?", raw_line, re.IGNORECASE)
    if m:
        sensor_num = int(m.group(1))
        val = float(m.group(2))
        unit = (m.group(3) or "cm").lower()
        val_cm = val * 100.0 if unit == "m" else val
        cm_clean = 999.0 if (val_cm <= 0 or val_cm > 450.0) else round(val_cm, 1)
        return sensor_num, cm_clean
    m_none = re.search(r"sensor\s*([12])\s*:", raw_line, re.IGNORECASE)
    if m_none and any(w in raw_line.lower() for w in ("out", "none", "clear", "inf")):
        return int(m_none.group(1)), 999.0
    return None


def release_arduino_ide_locks():
    if os.name == "nt":
        for proc in ("serial-monitor.exe", "serial-discovery.exe", "arduino-cli.exe"):
            subprocess.run(
                ["taskkill", "/F", "/IM", proc],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def uno_usb_loop():
    """Keep COM7 open continuously and parse both 'Sensor 1/2: XX cm' and 'D,d1,d2' formats."""
    try:
        import serial
    except ImportError:
        print("[USB ERROR] Run: pip install pyserial")
        return

    release_arduino_ide_locks()
    target_port = UNO_PORT
    print(f"[USB Reader] Connecting to Arduino Uno on {target_port} at 9600 baud (COM5 camera ignored)...")

    while True:
        try:
            ser = serial.Serial()
            ser.port = target_port
            ser.baudrate = 9600
            ser.timeout = 2.0
            ser.dtr = False
            ser.rts = False
            ser.open()

            print(f"[USB Reader] {target_port} opened! Reading Ultrasonic Sensor 1 & Sensor 2...")
            time.sleep(1.5)
            ser.reset_input_buffer()

            cur_d1 = None
            cur_d2 = None
            last_print = 0.0

            while True:
                raw_bytes = ser.readline()
                if not raw_bytes:
                    continue
                raw_line = raw_bytes.decode("utf-8", errors="ignore").strip()
                if not raw_line:
                    continue

                parsed_d = parse_d_line(raw_line)
                if parsed_d is not None:
                    cur_d1, cur_d2 = parsed_d
                    usb_sensor["port"] = target_port
                    apply_usb_sensor_reading(cur_d1, cur_d2)
                    if time.time() - last_print >= 2.0:
                        print(f"[COM7 Ultrasonic OK] S1: {cur_d1/100.0:.2f} m | S2: {cur_d2/100.0:.2f} m")
                        last_print = time.time()
                    continue

                parsed_s = parse_sensor_text_line(raw_line)
                if parsed_s is not None:
                    s_num, cm_val = parsed_s
                    if s_num == 1:
                        cur_d1 = cm_val
                    else:
                        cur_d2 = cm_val
                    usb_sensor["port"] = target_port
                    apply_usb_sensor_reading(
                        cur_d1 if cur_d1 is not None else cm_val,
                        cur_d2 if cur_d2 is not None else cm_val,
                    )
                    if s_num == 2 and (time.time() - last_print >= 2.0):
                        s1_m = (cur_d1 or cm_val) / 100.0
                        s2_m = (cur_d2 or cm_val) / 100.0
                        print(f"[COM7 Ultrasonic OK] S1: {s1_m:.2f} m | S2: {s2_m:.2f} m | Width: {(s1_m+s2_m)/2:.2f} m")
                        last_print = time.time()
        except Exception as e:
            msg = str(e)
            print(f"[COM7 Notice] {msg[:120]}")
            if "PermissionError" in msg or "Access is denied" in msg:
                release_arduino_ide_locks()
            usb_sensor["port"] = None
            time.sleep(2.5)


def scene_changed(prev_lat, scores, d1, d2, taken_cm):
    if not prev_lat:
        return True
    prev_d1, prev_d2 = prev_lat.get("d1"), prev_lat.get("d2")
    was_near = any(2 <= x < taken_cm for x in valid_distances(prev_d1, prev_d2))
    is_near = any(2 <= x < taken_cm for x in valid_distances(d1, d2))
    if was_near != is_near:
        return True
    for cur_d, old_d in ((d1, prev_d1), (d2, prev_d2)):
        if cur_d is not None and old_d is not None and cur_d >= 2 and old_d >= 2:
            if abs(float(cur_d) - float(old_d)) >= 25.0 and (cur_d < 200 or old_d < 200):
                return True
    prev_scores = prev_lat.get("scores") or {}
    for k in ("car", "empty", "person"):
        if abs(scores.get(k, 0.0) - prev_scores.get(k, 0.0)) >= 0.18:
            return True
    return False


@app.route("/api/uno", methods=["GET", "POST"])
def api_uno():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if "slot_length_m" in data:
            try:
                SYSTEM_CONFIG["slot_length_m"] = max(0.5, min(20.0, float(data["slot_length_m"])))
            except Exception:
                pass
        if "taken_m" in data:
            try:
                SYSTEM_CONFIG["taken_cm"] = max(10.0, min(400.0, float(data["taken_m"]) * 100.0))
            except Exception:
                pass
        d1, d2 = data.get("d1"), data.get("d2")
        if d1 is not None and d2 is not None:
            apply_usb_sensor_reading(float(d1), float(d2))
    now = time.time()
    fresh = bool(usb_sensor["at"] and (now - usb_sensor["at"] <= 60.0))
    d1_m, d2_m, width_m, length_m, area_m2 = compute_geometry(
        usb_sensor["d1"] if fresh else None,
        usb_sensor["d2"] if fresh else None,
    )
    return jsonify(
        ok=fresh,
        d1=usb_sensor["d1"] if fresh else None,
        d2=usb_sensor["d2"] if fresh else None,
        d1_m=d1_m,
        d2_m=d2_m,
        slot_width_m=width_m,
        slot_length_m=length_m,
        slot_area_m2=area_m2,
        port=usb_sensor["port"],
        age_s=round(now - usb_sensor["at"], 1) if usb_sensor["at"] else None,
    )


@app.route("/api/reading", methods=["POST"])
def api_reading():
    data = request.get_json(force=True, silent=True) or {}
    scores = {str(k).lower(): float(v) for k, v in (data.get("scores") or {}).items()}
    for k in ("car", "empty", "person"):
        scores.setdefault(k, 0.0)
    esp_d1, esp_d2 = data.get("d1"), data.get("d2")
    d1, d2 = get_effective_distances(esp_d1, esp_d2)

    building = str(data.get("building", "cict")).lower()
    parking = str(data.get("parking", "A")).upper()
    slot = int(data.get("slot", 1))
    now = time.time()

    key = (building, parking, slot)
    st = state.setdefault(
        key,
        {"status": "free", "pending": None, "count": 0, "since": now, "latest": {}},
    )
    prev_latest = dict(st.get("latest") or {})

    ai_res = fresh_ai(st)
    raw, agree, cam, conf, source, det_cls, disp_cls, reason = fuse(scores, d1, d2, ai_res)
    smooth(key, raw, now, source=source)

    edge_top = max(scores, key=scores.get)
    st["latest"] = {
        "building": building,
        "parking": parking,
        "slot": slot,
        "status": st["status"],
        "raw": raw,
        "since": st["since"],
        "at": now,
        "scores": scores,
        "camera_says": cam,
        "camera_conf": round(conf, 3),
        "camera_source": source,
        "detected_class": det_cls,
        "display_class": disp_cls,
        "decision_reason": reason,
        "edge_label": edge_top,
        "d1": d1,
        "d2": d2,
        "agree": agree,
        "cam_ip": data.get("cam_ip"),
    }

    with db() as conn:
        conn.execute(
            "INSERT INTO readings (ts,building,parking,slot,car,empty,person,d1,d2,raw,status,agree,ai,src) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now, building, parking, slot,
                scores["car"], scores["empty"], scores["person"],
                d1, d2, raw, st["status"],
                None if agree is None else int(agree),
                ai_res["label"] if isinstance(ai_res, dict) else None,
                source,
            ),
        )

    taken_cm = float(SYSTEM_CONFIG.get("taken_cm", TAKEN_CM))
    if ai.enabled() and SYSTEM_CONFIG["mode"] != "edge_only":
        if ai_res is None or scene_changed(prev_latest, scores, d1, d2, taken_cm):
            ai_wakeup.set()

    return jsonify(ok=True, status=st["status"], detected_class=det_cls, source=source, d1=d1, d2=d2)


@app.route("/api/latest")
def api_latest():
    now = time.time()
    # If state is empty but USB sensor has data, initialize default slot
    if not state and usb_sensor["at"]:
        apply_usb_sensor_reading(usb_sensor["d1"], usb_sensor["d2"])

    out = []
    for (b, p, s), st in state.items():
        if not st.get("latest"):
            continue
        item = dict(st["latest"])
        d1, d2 = get_effective_distances(item.get("d1"), item.get("d2"))
        d1_m, d2_m, width_m, length_m, area_m2 = compute_geometry(d1, d2)

        item["d1"], item["d2"] = d1, d2
        item["d1_m"], item["d2_m"] = d1_m, d2_m
        item["slot_width_m"] = width_m
        item["slot_length_m"] = length_m
        item["slot_area_m2"] = area_m2
        item["uno_port"] = usb_sensor.get("port")

        last_active = max(item.get("at", 0.0), usb_sensor.get("at", 0.0))
        item["age_s"] = round(now - last_active, 1)

        a = st.get("ai") if isinstance(st.get("ai"), dict) else None
        item["ai_on"] = ai.enabled() and SYSTEM_CONFIG["mode"] != "edge_only"
        item["ai_key_present"] = ai.enabled()
        item["ai_model"] = ai.ACTIVE_MODEL
        item["ai_label"] = a.get("label") if a else None
        item["ai_display_label"] = a.get("display_label") if a else None
        item["ai_certainty"] = a.get("certainty") if a else None
        item["ai_ms"] = a.get("ms") if a else None
        item["ai_color"] = a.get("color") if a else None
        item["ai_parked"] = a.get("parked_properly") if a else None
        item["ai_people"] = a.get("people") if a else None
        item["ai_desc"] = a.get("description") if a else None
        item["ai_age_s"] = round(now - a["at"], 1) if (a and "at" in a) else None
        item["ai_fresh"] = bool(a and "at" in a and (now - a["at"] <= AI_FRESH))
        item["ai_error"] = st.get("ai_error")
        item["system_mode"] = SYSTEM_CONFIG["mode"]
        item["taken_cm"] = SYSTEM_CONFIG["taken_cm"]
        item["taken_m"] = round(float(SYSTEM_CONFIG["taken_cm"]) / 100.0, 2)
        out.append(item)
    return jsonify(out)


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    data = request.get_json(force=True, silent=True) or {}
    scenario = data.get("scenario", "car")
    building = str(data.get("building", "cict")).lower()
    parking = str(data.get("parking", "A")).upper()
    slot = int(data.get("slot", 1))
    now = time.time()
    key = (building, parking, slot)
    st = state.setdefault(
        key,
        {"status": "free", "pending": None, "count": 0, "since": now, "latest": {}},
    )

    presets = {
        "empty": {
            "scores": {"car": 0.04, "empty": 0.92, "person": 0.04},
            "d1": 250.0, "d2": 248.0,
            "ai": {"label": "empty", "display_label": "Empty (Free Slot)", "base": "empty", "certainty": "high",
                   "color": None, "parked_properly": None, "people": 0, "description": "Clear parking slot to opposite stick (2.50 m width)", "ms": 780, "at": now},
        },
        "car": {
            "scores": {"car": 0.88, "empty": 0.08, "person": 0.04},
            "d1": 54.0, "d2": 56.0,
            "ai": {"label": "car", "display_label": "Car / SUV", "base": "car", "certainty": "high",
                   "color": "Silver", "parked_properly": True, "people": 0, "description": "Silver sedan parked straight inside the slot", "ms": 860, "at": now},
        },
        "crooked_car": {
            "scores": {"car": 0.79, "empty": 0.15, "person": 0.06},
            "d1": 32.0, "d2": 118.0,
            "ai": {"label": "car", "display_label": "Car / SUV", "base": "car", "certainty": "high",
                   "color": "White", "parked_properly": False, "people": 0, "description": "White SUV parked crooked across the slot", "ms": 890, "at": now},
        },
        "motorcycle": {
            "scores": {"car": 0.52, "empty": 0.35, "person": 0.13},
            "d1": 68.0, "d2": 245.0,
            "ai": {"label": "motorcycle", "display_label": "Motorcycle", "base": "car", "certainty": "high",
                   "color": "Black", "parked_properly": True, "people": 0, "description": "Black motorcycle parked in center of slot", "ms": 840, "at": now},
        },
        "tricycle": {
            "scores": {"car": 0.64, "empty": 0.22, "person": 0.14},
            "d1": 50.0, "d2": 62.0,
            "ai": {"label": "tricycle", "display_label": "Tricycle", "base": "car", "certainty": "high",
                   "color": "Blue", "parked_properly": True, "people": 0, "description": "Blue Philippine tricycle with sidecar in slot", "ms": 880, "at": now},
        },
        "person": {
            "scores": {"car": 0.08, "empty": 0.18, "person": 0.74},
            "d1": 75.0, "d2": 245.0,
            "ai": {"label": "person", "display_label": "Person", "base": "person", "certainty": "high",
                   "color": None, "parked_properly": None, "people": 1, "description": "Person standing inside the parking slot", "ms": 820, "at": now},
        },
        "object": {
            "scores": {"car": 0.48, "empty": 0.42, "person": 0.10},
            "d1": 54.0, "d2": 55.0,
            "ai": {"label": "object", "display_label": "Object / Obstacle", "base": "object", "certainty": "high",
                   "color": "White", "parked_properly": None, "people": 0, "description": "Obstacle detected at 0.54 m inside the slot", "ms": 850, "at": now},
        },
    }
    p = presets.get(scenario, presets["car"])
    st["ai"] = p["ai"]
    st["ai_error"] = None
    usb_sensor["d1"], usb_sensor["d2"], usb_sensor["at"] = p["d1"], p["d2"], now

    for _ in range(3):
        ai_res = fresh_ai(st)
        raw, agree, cam, conf, source, det_cls, disp_cls, reason = fuse(p["scores"], p["d1"], p["d2"], ai_res)
        smooth(key, raw, now, source=source)

    st["latest"] = {
        "building": building,
        "parking": parking,
        "slot": slot,
        "status": st["status"],
        "raw": raw,
        "since": st["since"],
        "at": now,
        "scores": p["scores"],
        "camera_says": cam,
        "camera_conf": round(conf, 3),
        "camera_source": source,
        "detected_class": det_cls,
        "display_class": disp_cls,
        "decision_reason": reason,
        "edge_label": max(p["scores"], key=p["scores"].get),
        "d1": p["d1"],
        "d2": p["d2"],
        "agree": agree,
        "cam_ip": (st.get("latest") or {}).get("cam_ip"),
    }
    with db() as conn:
        conn.execute(
            "INSERT INTO readings (ts,building,parking,slot,car,empty,person,d1,d2,raw,status,agree,ai,src) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now, building, parking, slot,
                p["scores"]["car"], p["scores"]["empty"], p["scores"]["person"],
                p["d1"], p["d2"], raw, st["status"],
                None if agree is None else int(agree),
                p["ai"]["label"], source,
            ),
        )
    return jsonify(ok=True, status=st["status"], detected_class=det_cls, display_class=disp_cls)


def today_start():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@app.route("/api/stats")
def api_stats():
    with db() as conn:
        rows = conn.execute(
            "SELECT ts,status,agree,car,empty,person,ai FROM readings WHERE ts>=? ORDER BY ts",
            (today_start(),),
        ).fetchall()
    arrivals, stays, events = 0, [], []
    arrival_types, last_ai = {}, None
    prev, since = None, None
    for r in rows:
        if r["ai"]:
            last_ai = r["ai"]
        if r["status"] != prev:
            if prev == "car" and since is not None:
                stays.append(r["ts"] - since)
            if r["status"] == "car":
                arrivals += 1
                kind = (
                    last_ai
                    if last_ai in ("car", "motorcycle", "bicycle", "tricycle", "truck_or_van")
                    else "car"
                )
                arrival_types[kind] = arrival_types.get(kind, 0) + 1
            if prev is not None:
                events.append({
                    "ts": r["ts"],
                    "status": r["status"],
                    "ai_label": last_ai,
                })
            prev, since = r["status"], r["ts"]
    return jsonify(
        arrivals=arrivals,
        avg_stay_min=round(sum(stays) / len(stays) / 60, 1) if stays else None,
        arrival_types=arrival_types,
        events=events[-10:][::-1],
    )


@app.route("/api/hourly")
def api_hourly():
    with db() as conn:
        rows = conn.execute(
            """SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INTEGER) AS h,
                      COUNT(*) AS n,
                      SUM(CASE WHEN status IN ('car','person','object') THEN 1 ELSE 0 END) AS taken
               FROM readings WHERE ts>=? AND status!='offline' GROUP BY h""",
            (today_start(),),
        ).fetchall()
    out = [None] * 24
    for r in rows:
        out[r["h"]] = round(100 * r["taken"] / r["n"])
    return jsonify(out)


@app.route("/camera.jpg")
def camera_jpg():
    st = next((s for s in state.values() if (s.get("latest") or {}).get("cam_ip")), None)
    if not st:
        return Response("no camera yet", status=503)
    try:
        img = get_cached_or_fetch_camera(st, max_age=1.5)
        if not img:
            return Response("no camera yet", status=503)
    except Exception:
        if st.get("cam_img"):
            return Response(st["cam_img"], mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
        return Response("camera not reachable", status=503)
    return Response(img, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


def ai_step():
    global ai_backoff_until, last_ai_call_ts
    if not ai.enabled() or SYSTEM_CONFIG["mode"] == "edge_only":
        return
    now = time.time()
    if now < ai_backoff_until:
        return
    if now - last_ai_call_ts < AI_MIN_GAP:
        return

    for key, st in list(state.items()):
        lat = st.get("latest") or {}
        ip = lat.get("cam_ip")
        if not ip or time.time() - lat.get("at", 0) > 15:
            continue
        try:
            t0 = time.time()
            img = get_cached_or_fetch_camera(st, max_age=1.2)
            if not img:
                continue
            last_ai_call_ts = time.time()
            res = ai.classify_image(img)
            if not isinstance(res, dict):
                raise RuntimeError("Gemini returned an empty result")
            res["at"] = t0
            st["ai"], st["ai_error"], st["ai_img"] = res, None, img
            refresh_state_with_ai(key, st)
            print(
                "[Gemini Auto-Vision (%s)] %s (%s) in %d ms - %s"
                % (ai.ACTIVE_MODEL, res["label"], res["certainty"], res["ms"], res["description"])
            )
        except Exception as e:
            msg = str(e)
            st["ai_error"] = msg[:160]
            print("[Gemini warning]", msg[:200])
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                ai_backoff_until = time.time() + 60


def ai_loop():
    while True:
        ai_wakeup.wait(timeout=AI_INTERVAL)
        ai_wakeup.clear()
        ai_step()


@app.route("/")
@app.route("/monitor")
def monitor():
    return render_template("monitor.html")


if __name__ == "__main__":
    init_db()
    threading.Thread(target=uno_usb_loop, daemon=True).start()
    if ai.enabled():
        threading.Thread(target=ai_loop, daemon=True).start()
        print("Gemini check is ON - Auto-Vision active (model %s)" % ai.ACTIVE_MODEL)
    else:
        print("Gemini check is OFF (add key.txt to enable).")
    app.run(host="0.0.0.0", port=5000, debug=False)