// lorawan.devinci.fr – test node for Arduino MKR WAN 1310 (Murata CMWX1ZZABZ)
// Library: "MKRWAN" by Arduino (Library Manager). Board: "Arduino MKR WAN 1310".
//
// The Murata module has its own factory DevEUI: open the Serial Monitor once,
// copy the printed DevEUI and provision it:
//   python3 scripts/provision.py --gateway-eui <gw> --dev-eui <printed> --mac-version 1.0.2
// then paste the AppKey printed by provision.py into arduino_secrets.h.
//
// Payload: devinci-node v1 (codecs/devinci-node-v1.js) on fPort 1, with battery voltage.

#include <MKRWAN.h>
#include "arduino_secrets.h"

LoRaModem modem;

String appEui = SECRET_APP_EUI;  // = JoinEUI
String appKey = SECRET_APP_KEY;

unsigned long uplinkIntervalS = 300;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 5000) {}
  Serial.println("\n[boot] lorawan.devinci.fr test node (MKR WAN 1310)");

  if (!modem.begin(EU868)) {
    Serial.println("[lora] modem init failed");
    while (true) {}
  }
  Serial.print("[lora] module version: ");
  Serial.println(modem.version());
  Serial.print("[lora] DevEUI: ");
  Serial.println(modem.deviceEUI());

  unsigned long backoffS = 30;
  while (!modem.joinOTAA(appEui, appKey)) {
    Serial.print("[lora] join failed, retry in ");
    Serial.print(backoffS);
    Serial.println("s");
    delay(backoffS * 1000UL);
    backoffS = min(backoffS * 2, 600UL);
  }
  Serial.println("[lora] joined");
  modem.setPort(1);
  modem.setADR(true);
}

void loop() {
  // Battery voltage through the on-board divider (ADC_BATTERY); 0 = unknown.
  uint16_t mv = (uint16_t)(analogRead(ADC_BATTERY) * 4300UL / 1023UL);

  uint8_t up[6] = {
    0x01,              // format version
    0x7F, 0xFF,        // temperature: no sensor (add an MKR ENV shield to fill it)
    0xFF,              // humidity: no sensor
    (uint8_t)(mv >> 8), (uint8_t)mv,
  };

  modem.beginPacket();
  modem.write(up, sizeof(up));
  int err = modem.endPacket(false);  // unconfirmed
  if (err > 0) {
    Serial.print("[lora] uplink sent, battery mV=");
    Serial.println(mv);
  } else {
    Serial.println("[lora] uplink failed (duty cycle or not joined)");
  }

  // A Class A downlink arrives right after the uplink.
  delay(3000);
  uint8_t down[16];
  int n = 0;
  while (modem.available() && n < (int)sizeof(down)) down[n++] = (uint8_t)modem.read();
  if (n == 2) {  // fPort 2 "set interval" command from the codec
    uplinkIntervalS = max(60UL, ((unsigned long)down[0] << 8) | down[1]);
    Serial.print("[lora] uplink interval now ");
    Serial.println(uplinkIntervalS);
  }

  delay(uplinkIntervalS * 1000UL);
}
