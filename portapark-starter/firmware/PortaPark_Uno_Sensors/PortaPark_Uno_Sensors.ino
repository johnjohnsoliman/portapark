/* ==========================================================================
   PortaPark - Arduino Uno: two distance sensors

   Reads two HC-SR04 sensors and sends one line to the ESP32-CAM about
   once a second, for example:   D,45,240
   (D, distance of sensor 1 in cm, distance of sensor 2 in cm; 999 = nothing seen)

   WIRING
     Sensor 1:  VCC -> 5V   GND -> GND   TRIG -> pin 9    ECHO -> pin 10
     Sensor 2:  VCC -> 5V   GND -> GND   TRIG -> pin 11   ECHO -> pin 12

     To the ESP32-CAM (one signal wire plus ground):
       Uno pin 3 (TX) --[ 1k resistor ]--+--> ESP32-CAM GPIO 13 (RX)
                                         |
                                  [ 2k resistor ]
                                         |
                                        GND
       Uno GND -> ESP32-CAM GND   (the two boards MUST share ground)
     The two resistors lower the Uno's 5V signal to about 3.3V so it is safe
     for the ESP32-CAM.

   Upload with Tools > Board > Arduino Uno. Open the Serial Monitor at 9600
   to watch the same lines on the computer.
   ========================================================================== */

#include <SoftwareSerial.h>

const int TRIG1 = 9,  ECHO1 = 10;
const int TRIG2 = 11, ECHO2 = 12;

SoftwareSerial toCam(2, 3);   // RX = pin 2 (not used), TX = pin 3 -> ESP32-CAM

/* distance in centimetres (999 means nothing was seen) */
long readCm(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  unsigned long duration = pulseIn(echoPin, HIGH, 30000UL);   // wait up to 30 ms
  if (duration == 0) return 999;
  return duration / 58;
}

void setup() {
  Serial.begin(9600);
  toCam.begin(9600);
  pinMode(TRIG1, OUTPUT);
  pinMode(ECHO1, INPUT);
  pinMode(TRIG2, OUTPUT);
  pinMode(ECHO2, INPUT);
}

void loop() {
  long d1 = readCm(TRIG1, ECHO1);
  delay(60);                      // let the first sound fade so the sensors do not hear each other
  long d2 = readCm(TRIG2, ECHO2);

  toCam.print("D,");
  toCam.print(d1);
  toCam.print(",");
  toCam.println(d2);

  Serial.print("D,");
  Serial.print(d1);
  Serial.print(",");
  Serial.println(d2);

  delay(900);
}
