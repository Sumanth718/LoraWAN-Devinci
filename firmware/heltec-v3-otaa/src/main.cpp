// lorawan.devinci.fr – test node for Heltec WiFi LoRa 32 V3 / V3.2
// LoRaWAN 1.0.4, OTAA, EU868, Class A. Uses RadioLib 7.x.
//
// Sends the "devinci-node v1" payload (codecs/devinci-node-v1.js) on fPort 1:
// the ESP32-S3 die temperature, since the board has no ambient sensor
// (good enough to prove the chain end to end; wire a BME280 for real readings).
// A downlink on fPort 2 (uint16 BE, seconds) changes the uplink interval.

#include <Arduino.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SPI.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy include/secrets.example.h to include/secrets.h and fill in your keys"
#endif

// Heltec V3 on-board SX1262 wiring
#define LORA_NSS 8
#define LORA_SCK 9
#define LORA_MOSI 10
#define LORA_MISO 11
#define LORA_RST 12
#define LORA_BUSY 13
#define LORA_DIO1 14

SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);
LoRaWANNode node(&radio, &EU868);
Preferences store;  // ESP32 NVS flash

const uint64_t joinEUI = LORAWAN_JOIN_EUI;
const uint64_t devEUI = LORAWAN_DEV_EUI;
uint8_t appKey[] = {LORAWAN_APP_KEY};

uint32_t uplinkIntervalS = UPLINK_INTERVAL_S;

// LoRaWAN 1.0.4 requires DevNonce to keep increasing across reboots, and
// ChirpStack rejects re-used values ("DevNonce has already been used").
// So the nonce state is saved to flash after every join attempt.
static void saveNonces() {
  store.putBytes("nonces", node.getBufferNonces(), RADIOLIB_LORAWAN_NONCES_BUF_SIZE);
}

static void restoreNonces() {
  if (store.getBytesLength("nonces") != RADIOLIB_LORAWAN_NONCES_BUF_SIZE) return;
  uint8_t buf[RADIOLIB_LORAWAN_NONCES_BUF_SIZE];
  store.getBytes("nonces", buf, sizeof(buf));
  int16_t st = node.setBufferNonces(buf);
  if (st != RADIOLIB_ERR_NONE) {
    Serial.printf("[lora] stored nonces not reused (%d): keys changed, starting fresh\n", st);
  }
}

static void joinNetwork() {
  // nwkKey = NULL selects LoRaWAN 1.0.x: the single AppKey is the root key.
  node.beginOTAA(joinEUI, devEUI, NULL, appKey);
  restoreNonces();

  uint32_t backoffS = 30;
  while (true) {
    Serial.println("[lora] sending join-request...");
    int16_t st = node.activateOTAA();
    saveNonces();
    if (st == RADIOLIB_LORAWAN_NEW_SESSION) {
      Serial.printf("[lora] joined, DevAddr=%08lX\n", (unsigned long)node.getDevAddr());
      return;
    }
    Serial.printf("[lora] join failed (%d), retry in %lus\n", st, (unsigned long)backoffS);
    delay(backoffS * 1000UL);
    backoffS = min<uint32_t>(backoffS * 2, 600);  // back off: radio time is shared
  }
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\n[boot] lorawan.devinci.fr test node (Heltec V3)");

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  int16_t st = radio.begin();
  if (st != RADIOLIB_ERR_NONE) {
    Serial.printf("[radio] SX1262 init failed (%d) - check board / antenna\n", st);
    while (true) delay(1000);
  }

  store.begin("lorawan", false);
  node.setDutyCycle(true);  // enforce the EU868 legal limit (1 %)
  node.setADR(true);
  joinNetwork();
}

void loop() {
  // --- build devinci-node v1 payload
  int16_t centiC = (int16_t)lroundf(temperatureRead() * 100.0f);
  uint8_t up[6] = {
      0x01,                                       // format version
      (uint8_t)(centiC >> 8), (uint8_t)centiC,    // temperature, 0.01 °C
      0xFF,                                       // humidity: no sensor
      0x00, 0x00,                                 // battery: unknown
  };

  uint8_t down[32];
  size_t downLen = 0;
  LoRaWANEvent_t evUp, evDown;
  int16_t st = node.sendReceive(up, sizeof(up), 1, down, &downLen, false, &evUp, &evDown);

  if (st < RADIOLIB_ERR_NONE) {
    Serial.printf("[lora] uplink error %d\n", st);
  } else {
    Serial.printf("[lora] uplink FCnt=%lu temp=%.2fC\n", (unsigned long)evUp.fCnt, centiC / 100.0);
    if (st > 0) {  // downlink received in RX1 (1) or RX2 (2)
      Serial.printf("[lora] downlink port=%u len=%u\n", evDown.fPort, (unsigned)downLen);
      if (evDown.fPort == 2 && downLen == 2) {
        uplinkIntervalS = max<uint32_t>(60, ((uint32_t)down[0] << 8) | down[1]);
        Serial.printf("[lora] uplink interval now %lus\n", (unsigned long)uplinkIntervalS);
      }
    }
  }

  // Wait for the configured interval, or longer if the duty cycle requires it.
  uint32_t waitMs = max<uint32_t>(uplinkIntervalS * 1000UL, node.timeUntilUplink());
  delay(waitMs);
}
