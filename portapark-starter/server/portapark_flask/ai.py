"""Cloud AI lead classifier using the Gemini API (Google AI Studio key).

Automatically uses Gemini 3 Flash models (gemini-3.5-flash-lite, gemini-3.6-flash,
gemini-3.8-flash, gemini-3.1-flash-lite) supported for new Google AI Studio accounts,
and guarantees a dictionary return so 'NoneType' errors never occur.
"""
import json
import os
import time

# Gemini 3 Flash models available to new Google AI Studio users
CANDIDATE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash",
    "gemini-3-flash-preview",
]
ACTIVE_MODEL = CANDIDATE_MODELS[0]
TIMEOUT_MS = int(os.environ.get("AI_TIMEOUT", "15")) * 1000

VEHICLES = {"car", "motorcycle", "bicycle", "tricycle", "truck_or_van"}
LABELS = VEHICLES | {"person", "animal", "object", "empty"}

SYNONYMS = {
    "truck": "truck_or_van",
    "van": "truck_or_van",
    "pickup": "truck_or_van",
    "jeepney": "truck_or_van",
    "suv": "car",
    "sedan": "car",
    "hatchback": "car",
    "vehicle": "car",
    "automobile": "car",
    "motorbike": "motorcycle",
    "scooter": "motorcycle",
    "underbone": "motorcycle",
    "bike": "bicycle",
    "ebike": "bicycle",
    "e_bike": "bicycle",
    "trike": "tricycle",
    "e_trike": "tricycle",
    "sidecar": "tricycle",
    "kolong_kolong": "tricycle",
    "dog": "animal",
    "cat": "animal",
    "pet": "animal",
    "human": "person",
    "pedestrian": "person",
    "man": "person",
    "woman": "person",
    "student": "person",
    "none": "empty",
    "nothing": "empty",
    "vacant": "empty",
    "free": "empty",
    "clear": "empty",
    "box": "object",
    "obstacle": "object",
    "chair": "object",
    "cone": "object",
    "barrier": "object",
}

DISPLAY_NAMES = {
    "car": "Car / SUV",
    "motorcycle": "Motorcycle",
    "tricycle": "Tricycle",
    "bicycle": "Bicycle",
    "truck_or_van": "Truck / Van",
    "person": "Person",
    "animal": "Animal",
    "object": "Object / Obstacle",
    "empty": "Empty (Free Slot)",
}

PROMPT = (
    "You are the primary AI vision classifier for PortaPark, a portable single-slot smart parking monitor "
    "using an ESP32-CAM (320x240 JPEG). Inspect the central monitored parking zone in front of the camera.\n"
    "1. Write a short, clear description (max 12 words) of what is inside the monitored zone.\n"
    "2. Choose the single most accurate label:\n"
    "   - car (sedan, SUV, hatchback, car, or model car during bench testing)\n"
    "   - motorcycle (motorbike, scooter)\n"
    "   - tricycle (Philippine tricycle with sidecar, e-trike)\n"
    "   - bicycle (bicycle, e-bike)\n"
    "   - truck_or_van (pickup, van, truck, jeepney)\n"
    "   - person (ONLY if a real human body, face, torso, or limbs are clearly visible in the zone)\n"
    "   - animal (dog, cat, or animal in the zone)\n"
    "   - object (box, cone, chair, bag, or non-vehicle obstacle blocking the zone)\n"
    "   - empty (the monitored floor/ground/slot is clear with no vehicle, person, animal, or obstacle)\n"
    "3. Priority rule: If both a vehicle and a person are present, label the vehicle and set people >= 1.\n"
    "4. Set certainty to high (clear view), medium (partially visible/dim), or low (very blurry/dark).\n"
    "Reply with valid JSON ONLY in this exact format:\n"
    '{"description": "silver sedan parked straight inside the slot", "label": "car", "certainty": "high", '
    '"color": "silver", "parked_properly": true, "people": 0}'
)

_client = None


def _key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    key = key.strip().strip('"').strip("'")
    if not key and os.path.exists("key.txt"):
        try:
            with open("key.txt", "r", encoding="utf-8") as f:
                key = f.readline().strip().strip('"').strip("'")
        except Exception:
            pass
    return key


def enabled():
    return bool(_key())


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        _client = genai.Client(
            api_key=_key(),
            http_options=types.HttpOptions(timeout=TIMEOUT_MS),
        )
    return _client


def base_of(label):
    if label in VEHICLES:
        return "car"
    if label == "person":
        return "person"
    if label in ("animal", "object"):
        return "object"
    return "empty"


def display_name(label):
    return DISPLAY_NAMES.get(label, str(label or "Unknown").replace("_", " ").title())


def _norm(label):
    label = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
    return SYNONYMS.get(label, label)


def parse(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = {}
    try:
        loaded = json.loads(text)
        if isinstance(loaded, list) and loaded and isinstance(loaded[0], dict):
            data = loaded[0]
        elif isinstance(loaded, dict):
            data = loaded
        label = _norm(data.get("label"))
    except Exception:
        data, low = {}, text.lower()
        label = next(
            (w for w in ("motorcycle", "bicycle", "tricycle", "truck_or_van", "person", "animal", "object", "car", "empty") if w in low),
            "",
        )
    if label not in LABELS:
        label = "empty"

    cert = str(data.get("certainty", "high")).strip().lower()
    if cert not in ("low", "medium", "high"):
        cert = "high"

    pp = data.get("parked_properly")
    try:
        people = int(data.get("people", 1 if label == "person" else 0))
    except Exception:
        people = 1 if label == "person" else 0

    color = data.get("color")
    if color and str(color).strip().lower() in ("null", "none", "n/a", ""):
        color = None

    return {
        "label": label,
        "display_label": display_name(label),
        "base": base_of(label),
        "certainty": cert,
        "color": str(color).strip()[:20] if color else None,
        "parked_properly": pp if isinstance(pp, bool) else None,
        "people": people,
        "description": str(data.get("description", "")).strip()[:120] or display_name(label),
    }


def classify_image(jpeg_bytes):
    """Classify one JPEG image using Gemini 3 Flash models and always return a valid dict."""
    global ACTIVE_MODEL
    from google.genai import types
    client = _get_client()
    start = time.time()

    models_to_try = [ACTIVE_MODEL] + [m for m in CANDIDATE_MODELS if m != ACTIVE_MODEL]
    last_err = RuntimeError("No Gemini model responded")

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            raw_text = getattr(response, "text", None) or ""
            if not raw_text:
                raise RuntimeError(f"Empty response text from {model_name}")
            ACTIVE_MODEL = model_name
            result = parse(raw_text)
            result["ms"] = int((time.time() - start) * 1000)
            return result
        except Exception as e:
            last_err = e
            msg = str(e)
            if any(k in msg for k in ("404", "NOT_FOUND", "no longer available", "not supported", "400")):
                continue
            raise RuntimeError(msg)

    raise RuntimeError(str(last_err))