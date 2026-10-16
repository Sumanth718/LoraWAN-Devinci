// Copy to secrets.h (git-ignored) and paste the values printed by scripts/provision.py.
// One DevEUI + AppKey per physical board. Never commit real keys.
#pragma once

#define LORAWAN_JOIN_EUI 0x0000000000000000
#define LORAWAN_DEV_EUI  0x0200000000000001
#define LORAWAN_APP_KEY  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, \
                         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00

// Seconds between uplinks. Can be changed remotely with a downlink on fPort 2.
#define UPLINK_INTERVAL_S 300
