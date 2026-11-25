/* ==========================================================================
   PortaPark - ESP32-CAM  (camera + AI model ONLY)

   Every 2 seconds this board:
     1. takes a photo and runs your Edge Impulse model (Car / Empty / Person)
     2. sends the result to the Flask server over WiFi
   It also serves the newest photo at  http://<board-ip>/capture
   and a small status page at          http://<board-ip>/status

   THE DISTANCE SENSORS ARE NOT HANDLED HERE ANY MORE.
   The Arduino Uno is plugged into the laptop by USB and the Flask server reads
   it directly. So nothing needs to be wired between the Uno and this board:
   remove the 1k/2k divider, the GPIO 13 wire, the GPIO 14 wire and the ground
   jumper. This board only needs its own USB power.

   WHAT IS DIFFERENT FROM THE OLD SKETCH
     - No Serial2 / Uno code.
     - A camera problem no longer stops everything. The old sketch quit setup()
       when the camera failed, so WiFi never started. Now WiFi and the web
       server always start, a dead camera is retried every 15 s, and the server
       is told "cam_ok": false so it can show the camera is down.
     - A wrong SERVER_URL can no longer freeze the board for 5 seconds.

   BEFORE UPLOADING
     - Edit the lines under "EDIT THESE".
     - Library name must match your Edge Impulse library:
       SmartParkingSlot1-Proj_inferencing
     - Tools: Board = AI Thinker ESP32-CAM, PSRAM = Enabled,
              Partition Scheme = Huge APP (3MB No OTA)

   NOTE: the model was trained with "Squash" (whole picture shrunk to 96 x 96),
   so this sketch shrinks the whole picture the same way.
   ========================================================================== */

#include <SmartParkingSlot1-Proj_inferencing.h>
#include "esp_camera.h"
#include "img_converters.h"
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>

/* ===== EDIT THESE ===== */
const char *WIFI_NAME  = "Your wifi name goes here";                         // 2.4 GHz network
const char *WIFI_PASS  = "Your wifi password goes here";
const char *SERVER_URL = "http://IP address here:5000/api/reading";   // laptop IP + :5000
const char *BUILDING   = "cict";
const char *PARKING    = "A";
const int   SLOT       = 1;
const unsigned long CYCLE_MS = 2000;                         // time between readings
/* ====================== */

// AI Thinker ESP32-CAM pins
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM    5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

#define CAM_W 320
#define CAM_H 240
#define LAST_JPG_MAX 60000

static uint8_t *rgb_buf   = nullptr;    // full photo as RGB (320 x 240 x 3)
static uint8_t *small_buf = nullptr;    // photo shrunk to the model size
static uint8_t *last_jpg  = nullptr;    // newest JPEG, served at /capture
static size_t   last_jpg_len = 0;

WebServer server(80);

bool buffersOK = false;                 // memory for the camera was allocated
bool camOK     = false;                 // camera is running
int  camFails  = 0;                     // captures that failed in a row

float scores[EI_CLASSIFIER_LABEL_COUNT];
unsigned long lastCycle = 0, lastWifiTry = 0, lastCamTry = 0;

/* ---------- model input ---------- */

// Same colour order as the Edge Impulse example (the camera driver gives BGR)
static int get_data(size_t offset, size_t length, float *out_ptr) {
  size_t px = offset * 3;
  for (size_t i = 0; i < length; i++) {
    out_ptr[i] = (small_buf[px + 2] << 16) + (small_buf[px + 1] << 8) + small_buf[px];
    px += 3;
  }
  return 0;
}

// Shrinks the WHOLE photo (no cropping) by averaging blocks of pixels
static void squash_resize(const uint8_t *src, int sw, int sh, uint8_t *dst, int dw, int dh) {
  for (int y = 0; y < dh; y++) {
    int y0 = y * sh / dh, y1 = (y + 1) * sh / dh;
    if (y1 <= y0) y1 = y0 + 1;
    for (int x = 0; x < dw; x++) {
      int x0 = x * sw / dw, x1 = (x + 1) * sw / dw;
      if (x1 <= x0) x1 = x0 + 1;
      uint32_t r = 0, g = 0, b = 0, n = 0;
      for (int yy = y0; yy < y1; yy++) {
        for (int xx = x0; xx < x1; xx++) {
          const uint8_t *p = src + (yy * sw + xx) * 3;
          r += p[0]; g += p[1]; b += p[2]; n++;
        }
      }
      uint8_t *o = dst + (y * dw + x) * 3;
      o[0] = r / n; o[1] = g / n; o[2] = b / n;
    }
  }
}

/* ---------- camera ---------- */

bool startCamera(int attempts) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 10000000;          // slower clock: this module is more reliable at 10 MHz
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_QVGA;      // 320 x 240
  c.jpeg_quality = 12;
  c.fb_count = 1;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  for (int attempt = 1; attempt <= attempts; attempt++) {
    esp_err_t err = esp_camera_init(&c);
    if (err == ESP_OK) return true;
    Serial.printf("Camera init attempt %d failed (0x%x)\n", attempt, err);
    esp_camera_deinit();
    delay(1000);
  }
  return false;
}

/* ---------- take a photo and classify it ---------- */

bool captureAndClassify() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return false;

  if (fb->width != CAM_W || fb->height != CAM_H) {      // safety check
    esp_camera_fb_return(fb);
    return false;
  }
  if (fb->len <= LAST_JPG_MAX) {                        // keep the photo for /capture
    memcpy(last_jpg, fb->buf, fb->len);
    last_jpg_len = fb->len;
  }
  bool ok = fmt2rgb888(fb->buf, fb->len, PIXFORMAT_JPEG, rgb_buf);
  esp_camera_fb_return(fb);
  if (!ok) return false;

  squash_resize(rgb_buf, CAM_W, CAM_H, small_buf, EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT);

  ei::signal_t signal;
  signal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
  signal.get_data = &get_data;

  ei_impulse_result_t result = {0};
  EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);
  if (err != EI_IMPULSE_OK) {
    Serial.printf("Classifier error %d\n", err);
    return false;
  }
  for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) scores[i] = result.classification[i].value;
  return true;
}

/* ---------- send to the Flask server ---------- */

String scoresJson() {
  String s = "{";
  for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    if (i) s += ",";
    s += "\"";
    s += ei_classifier_inferencing_categories[i];
    s += "\":";
    s += String(scores[i], 3);
  }
  s += "}";
  return s;
}

void postReading() {
  if (WiFi.status() != WL_CONNECTED) return;
  // No distances here: the server gets them straight from the Uno over USB.
  String body = "{\"building\":\"" + String(BUILDING) + "\",\"parking\":\"" + String(PARKING) +
                "\",\"slot\":" + String(SLOT) + ",\"scores\":" + scoresJson() +
                ",\"cam_ok\":" + String(camOK ? "true" : "false") +
                ",\"cam_ip\":\"" + WiFi.localIP().toString() + "\"}";
  HTTPClient http;
  http.setConnectTimeout(1500);         // a wrong SERVER_URL must not freeze the board for 5 s
  http.setTimeout(2000);
  if (http.begin(SERVER_URL)) {
    http.addHeader("Content-Type", "application/json");
    int code = http.POST(body);
    Serial.printf("Sent to server: HTTP %d\n", code);       // 200 = good, negative = cannot reach server
    http.end();
  }
}

/* ---------- web pages on the board ---------- */

void handleCapture() {
  if (last_jpg_len == 0) { server.send(503, "text/plain", "no photo yet"); return; }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.setContentLength(last_jpg_len);
  server.send(200, "image/jpeg", "");
  WiFiClient client = server.client();
  client.write(last_jpg, last_jpg_len);
}

void handleStatus() {
  String s = "{\"scores\":" + scoresJson() + ",\"cam_ok\":" + String(camOK ? "true" : "false") + "}";
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", s);
}

/* ---------- setup and loop ---------- */

void setup() {
  Serial.begin(115200);
  delay(1500);                                          // let the camera power up
  Serial.println("\n=== PortaPark ESP32-CAM (camera only) ===");
  memset(scores, 0, sizeof(scores));

  // The camera is OPTIONAL at start-up: if anything below fails, the board still
  // connects to WiFi and tells the server that the camera is down.
  if (!psramFound()) {
    Serial.println("PSRAM not found. Set Tools > PSRAM to Enabled. (camera off)");
  } else {
    rgb_buf   = (uint8_t *)ps_malloc(CAM_W * CAM_H * 3);
    small_buf = (uint8_t *)ps_malloc(EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT * 3);
    last_jpg  = (uint8_t *)ps_malloc(LAST_JPG_MAX);
    buffersOK = rgb_buf && small_buf && last_jpg;
    if (!buffersOK) {
      Serial.println("Out of memory (camera off)");
    } else {
      camOK = startCamera(5);
      lastCamTry = millis();
      if (camOK) Serial.println("Camera OK");
      else Serial.println("Camera failed. Check ribbon and USB cable. Will retry every 15 s.");
    }
  }

  WiFi.begin(WIFI_NAME, WIFI_PASS);
  WiFi.setSleep(false);
  Serial.print("Connecting to WiFi");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) Serial.println("WiFi not connected yet; will keep trying.");
  else { Serial.print("Board address: http://"); Serial.println(WiFi.localIP()); }

  server.on("/", []() { server.send(200, "text/html", "<img src='/capture' style='max-width:100%'><p><a href='/status'>status</a></p>"); });
  server.on("/capture", handleCapture);
  server.on("/status", handleStatus);
  server.begin();
}

void loop() {
  server.handleClient();

  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 10000) {
    WiFi.reconnect();
    lastWifiTry = millis();
  }

  // camera is down: try to bring it back every 15 s
  if (!camOK && buffersOK && millis() - lastCamTry > 15000) {
    lastCamTry = millis();
    esp_camera_deinit();
    camOK = startCamera(1);
    camFails = 0;
    Serial.println(camOK ? "Camera recovered" : "Camera still down");
  }

  if (millis() - lastCycle >= CYCLE_MS) {
    lastCycle = millis();

    if (camOK) {
      if (captureAndClassify()) {
        camFails = 0;
      } else if (++camFails >= 5) {                     // 5 bad captures in a row = camera is gone
        camOK = false;
        lastCamTry = millis();
        memset(scores, 0, sizeof(scores));
        Serial.println("Camera stopped answering. Retrying in 15 s.");
      } else {
        Serial.println("Capture or classify failed (keeping the last result)");
      }
    }

    Serial.printf("Scores:");
    for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++)
      Serial.printf("  %s %.2f", ei_classifier_inferencing_categories[i], scores[i]);
    Serial.printf("   Camera: %s\n", camOK ? "OK" : "DOWN");
    postReading();
  }
}
