# Build your own LoRaWAN gateway (Raspberry Pi + SX1302)

We don't have access to the school's Kerlink Wirnet iFemtoCell gateways, so the prototype uses
a gateway we build ourselves. A Raspberry Pi plus an SX1302/SX1303 concentrator HAT is a real
8-channel LoRaWAN gateway, in the same class as commercial indoor gateways. When the Kerlinks
become available, they connect to the same server (see the last section).

## Shopping list (one indoor gateway)

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2 GB+) or Pi 5 | A Pi 3B+ also works for the gateway role alone |
| Concentrator HAT, **EU868 version** | One of the boards supported by ChirpStack Concentratord: **RAK2287 + RAK Pi HAT**, RAK5146 + Pi HAT, **Seeed WM1302 (SPI)** + WM1302 Pi HAT, **Waveshare SX1302 LoRaWAN Gateway HAT**, Dragino PG1302 |
| 868 MHz antenna (u.FL/SMA pigtail if needed) | **Never power the concentrator without an antenna**: it can damage the RF front-end |
| microSD 16 GB+, official PSU | |
| End node(s) | Heltec WiFi LoRa 32 **V3** (868 MHz variant) is the cheapest; Arduino MKR WAN 1310 if available in the lab |

Buy the **868 MHz / EU868** SKU of every radio part. 915 MHz variants look identical and won't work legally in France.

## Option A (recommended): ChirpStack Gateway OS

ChirpStack Gateway OS is an OpenWrt image for the Raspberry Pi that bundles ChirpStack
Concentratord (the HAT driver) and the forwarders. It's configured from a web UI and needs
no compiling.

1. Download the latest **base** image for your Pi from
   <https://github.com/chirpstack/chirpstack-gateway-os/releases>. The *full* image also
   embeds a whole ChirpStack server; we don't need it, because our server is the Docker stack.
2. Flash it with Raspberry Pi Imager or balenaEtcher, then boot the Pi with the HAT and antenna fitted.
3. Connect to the Pi (Ethernet, or its default Wi-Fi access point) and open the web UI.
   Set a root password straight away.
4. **Concentratord**: pick your shield model (e.g. `rak_2287`, `seeed_wm1302`,
   `waveshare_sx1302_lorawan_gateway_hat`) and the region **EU868**. Save and apply.
5. Note the **Gateway ID** shown by Concentratord (16 hex characters).
6. **Forwarder**: enable the **UDP forwarder** (Semtech UDP) and point it at
   `<server-ip>:1700`.
7. Register the gateway in ChirpStack with that ID:
   `python3 scripts/provision.py --gateway-eui <gateway-id>` (or through the UI).
   It should show as **online** within about 30 s.

Exact menu names change between Gateway OS releases. If something differs, follow the
ChirpStack Gateway OS docs for your version.

## Option B: Raspberry Pi OS + Semtech `sx1302_hal`

This is more manual, and it's useful if you want to see every layer (good for the report).

```bash
sudo raspi-config            # Interface Options -> enable SPI (and I2C for the temp sensor)
sudo apt install -y git build-essential
git clone https://github.com/Lora-net/sx1302_hal.git && cd sx1302_hal
make
cd packet_forwarder
cp ../tools/reset_lgw.sh .   # then edit the GPIO numbers to match YOUR HAT's datasheet
cp global_conf.json.sx1250.EU868 global_conf.json
sudo ../util_chip_id/chip_id # prints the concentrator EUI -> use it as gateway_ID
```

In `global_conf.json`, edit the `gateway_conf` section:

```json
"gateway_ID": "<EUI printed by chip_id>",
"server_address": "<server-ip or lorawan.devinci.fr>",
"serv_port_up": 1700,
"serv_port_down": 1700
```

Then run `sudo ./lora_pkt_fwd`. Once it works, turn it into a systemd service.
The reset GPIO differs between HATs (RAK, Seeed, Waveshare), and a wrong pin is the most
common reason for `lgw_start failed`.

## Testing without any radio

`scripts/virtual_gateway.py` speaks the same UDP protocol as the packet forwarder above. Use it
to validate the server before the hardware arrives, or to load-test with many devices.

## Network requirements (input for the DSI / WP2)

The gateway only needs **outbound** traffic. Nothing has to reach it from outside.

| Flow | Protocol | Purpose |
|---|---|---|
| gateway → server :1700 | UDP | Semtech UDP packet forwarder (uplinks, downlinks over the same flow) |
| gateway → server :3001 | TCP (WebSocket, TLS in production) | LoRa Basics Station (Kerlink, later) |
| gateway → NTP, DNS | UDP 123 / 53 | Time and name resolution |
| admin → gateway :22/:80/:443 | TCP | Management, from the admin VLAN only |

This is what the dedicated gateway VLAN and its filtering rules need to allow.

## Radio and regulatory reminders (EU868, ETSI EN 300 220)

- **Sub-band g1 (868.0–868.6 MHz), the default join and uplink channels:** 25 mW ERP max and
  1 % duty cycle. That's about 36 s of airtime per hour per device. The firmware enforces it.
- **RX2 (869.525 MHz, sub-band g3):** up to 500 mW and 10 %. Downlinks use it.
- Gateways are also transmitters (downlinks, join-accepts), so they're subject to the same limits.
- Indoor gateway placement: high up, near a window, away from metal racks. Expect a few hundred
  metres indoors and kilometres outdoors with line of sight (WP4).

## Plugging in the Kerlink iFemtoCell later

The Kerlinks support **LoRa Basics Station**. The stack already runs a Basics Station bridge on
port 3001 (`chirpstack-gateway-bridge-basicstation`). In the Kerlink's management interface, set
the LNS URI to `ws://<server>:3001` for a first test, then `wss://` with certificates for
production. No server change is needed.
