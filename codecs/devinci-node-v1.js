// ChirpStack v4 payload codec – "devinci-node v1"
// Paste into: Device profile > Codec > JavaScript functions
// (scripts/provision.py does this automatically).
//
// Uplink, fPort 1 (6 bytes, big-endian):
//   byte 0     format version (0x01)
//   byte 1-2   temperature, int16, 0.01 °C     (0x7FFF = no sensor)
//   byte 3     relative humidity, uint8, 0.5 % (0xFF   = no sensor)
//   byte 4-5   battery, uint16, mV             (0x0000 = unknown)
//
// Downlink, fPort 2 (2 bytes, big-endian):
//   byte 0-1   new uplink interval in seconds (firmware clamps to >= 60 s)
//   Enqueue with JSON object {"interval_s": 300} on fPort 2.

function decodeUplink(input) {
  var b = input.bytes;
  if (input.fPort !== 1) {
    return { errors: ["unexpected fPort " + input.fPort] };
  }
  if (b.length < 6 || b[0] !== 0x01) {
    return { errors: ["unknown payload format (len " + b.length + ", version " + b[0] + ")"] };
  }

  var data = {};
  var warnings = [];

  var tRaw = (b[1] << 8) | b[2];
  if (tRaw !== 0x7fff) {
    if (tRaw & 0x8000) tRaw -= 0x10000; // sign-extend int16
    data.temperature_c = tRaw / 100;
  }

  if (b[3] !== 0xff) {
    data.humidity_pct = b[3] / 2;
  }

  var mv = (b[4] << 8) | b[5];
  if (mv !== 0) {
    data.battery_v = mv / 1000;
    if (mv < 3300) warnings.push("low battery");
  }

  return { data: data, warnings: warnings };
}

function encodeDownlink(input) {
  var s = Math.round(Number(input.data.interval_s));
  if (!(s >= 60 && s <= 65535)) {
    throw new Error("interval_s must be between 60 and 65535");
  }
  return { bytes: [(s >> 8) & 0xff, s & 0xff] };
}

// Lets the unit tests require() this file; ChirpStack ignores it.
if (typeof module !== "undefined") {
  module.exports = { decodeUplink: decodeUplink, encodeDownlink: encodeDownlink };
}
