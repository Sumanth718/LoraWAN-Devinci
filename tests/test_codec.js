// node tests/test_codec.js
const assert = require("assert");
const { decodeUplink, encodeDownlink } = require("../codecs/devinci-node-v1.js");
const hex = (h) => [...Buffer.from(h, "hex")];

// Payload produced by the virtual gateway: 21.19 °C, 45.5 %, 4.100 V
let r = decodeUplink({ fPort: 1, bytes: hex("0108475b1004") });
assert.deepStrictEqual(r.data, { temperature_c: 21.19, humidity_pct: 45.5, battery_v: 4.1 });

// Negative temperature, no humidity sensor, unknown battery (Heltec without sensor)
r = decodeUplink({ fPort: 1, bytes: hex("01fc18ff0000") });
assert.deepStrictEqual(r.data, { temperature_c: -10 });

// No temperature sensor, low battery (MKR WAN 1310)
r = decodeUplink({ fPort: 1, bytes: hex("017fffff0c80") });
assert.deepStrictEqual(r.data, { battery_v: 3.2 });
assert.deepStrictEqual(r.warnings, ["low battery"]);

assert.ok(decodeUplink({ fPort: 3, bytes: hex("01") }).errors);
assert.ok(decodeUplink({ fPort: 1, bytes: hex("02000000") }).errors);

assert.deepStrictEqual(encodeDownlink({ data: { interval_s: 300 } }).bytes, [0x01, 0x2c]);
assert.throws(() => encodeDownlink({ data: { interval_s: 5 } }));

console.log("codec: all tests passed");
