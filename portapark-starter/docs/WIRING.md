# Wiring

## Sensors to the Arduino Uno

| From | To |
|---|---|
| Sensor 1 VCC / GND | Uno 5V / GND |
| Sensor 1 TRIG / ECHO | Uno pin 9 / pin 10 |
| Sensor 2 VCC / GND | Uno 5V / GND |
| Sensor 2 TRIG / ECHO | Uno pin 11 / pin 12 |

The sketch fires the two sensors one at a time (60 ms apart) so they do not hear each other.

## Uno to ESP32-CAM (serial link)

| From | To |
|---|---|
| Uno pin 3 (SoftwareSerial TX) | 1 kOhm resistor, then ESP32-CAM GPIO 13 (RX) |
| Junction of the 1 kOhm resistor and GPIO 13 | 2 kOhm resistor to GND |
| Uno GND | ESP32-CAM GND |

The two resistors form a divider that lowers the Uno's 5 V signal to about 3.3 V.
The boards must share ground. Avoid ESP32-CAM GPIO 12 (boot strapping pin).

<!-- EDIT: if your final build sends the sensor readings to the PC over the Uno's USB cable instead,
     describe that here and skip the serial link above. -->

## Power

- ESP32-CAM: 5 V, 2 A (phone charger or power bank) through its USB-C base. Weak power causes camera errors.
- Uno: USB from a power bank or the PC.
