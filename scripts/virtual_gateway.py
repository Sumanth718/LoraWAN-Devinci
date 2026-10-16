#!/usr/bin/env python3
"""Virtual LoRaWAN gateway + end device, for testing lorawan.devinci.fr with zero radio hardware.

It speaks the Semtech UDP packet-forwarder protocol to the ChirpStack Gateway Bridge
(UDP 1700) exactly like a real Raspberry Pi + SX1302 gateway would, and plays one
OTAA end device behind it:

    join-request -> join-accept -> periodic uplinks (devinci-node v1 payload) -> downlinks

ChirpStack cannot tell the difference, so it exercises the whole stack: gateway bridge,
MQTT, network server, codec, application integrations, dashboards.

    pip install cryptography
    python3 scripts/virtual_gateway.py \
        --gateway-eui 0016c001ff100001 \
        --dev-eui 70b3d57ed0000001 --app-key 000102030405060708090a0b0c0d0e0f

The gateway and device must exist in ChirpStack first (scripts/provision.py does it).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import queue
import random
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lorawan_crypto as lw  # noqa: E402

PROTOCOL_VERSION = 2
PUSH_DATA, PUSH_ACK, PULL_DATA, PULL_RESP, PULL_ACK, TX_ACK = 0, 1, 2, 3, 4, 5
EU868_DEFAULT_CHANNELS = [868.1, 868.3, 868.5]  # join + uplinks stay on these


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class SemtechUdpGateway:
    """Just enough of the packet-forwarder protocol (PROTOCOL.TXT v2)."""

    def __init__(self, server: str, port: int, gateway_eui: str):
        self.addr = (server, port)
        self.eui = bytes.fromhex(gateway_eui)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.downlinks: "queue.Queue[bytes]" = queue.Queue()
        self.acked = threading.Event()
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self.rx_count = self.tx_count = 0

    def tmst(self) -> int:
        return int((time.monotonic() - self._t0) * 1e6) & 0xFFFFFFFF

    def _send(self, ident: int, payload: bytes = b"", with_eui: bool = True) -> None:
        token = random.getrandbits(16)
        hdr = struct.pack(">BHB", PROTOCOL_VERSION, token, ident)
        self.sock.sendto(hdr + (self.eui if with_eui else b"") + payload, self.addr)

    def start(self) -> None:
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _keepalive_loop(self) -> None:
        n = 0
        while not self._stop.is_set():
            self._send(PULL_DATA)  # opens the downlink path through NAT / firewalls
            if n % 3 == 0:
                self.push_stat()
            n += 1
            self._stop.wait(10)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as e:  # e.g. ICMP port unreachable: bridge not running
                log(f"[gw] socket error: {e}")
                time.sleep(1)
                continue
            if len(data) < 4:
                continue
            ident = data[3]
            if ident in (PUSH_ACK, PULL_ACK):
                self.acked.set()
            elif ident == PULL_RESP:
                txpk = json.loads(data[4:])["txpk"]
                phy = base64.b64decode(txpk["data"])
                self.tx_count += 1
                # Tell the server the packet was accepted for transmission.
                hdr = struct.pack(">BHB", PROTOCOL_VERSION, struct.unpack(">H", data[1:3])[0], TX_ACK)
                self.sock.sendto(hdr + self.eui + json.dumps({"txpk_ack": {"error": "NONE"}}).encode(), self.addr)
                self.downlinks.put(phy)

    def push_stat(self) -> None:
        stat = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S GMT", time.gmtime()),
            "rxnb": self.rx_count, "rxok": self.rx_count, "rxfw": self.rx_count,
            "ackr": 100.0, "dwnb": self.tx_count, "txnb": self.tx_count,
        }
        self._send(PUSH_DATA, json.dumps({"stat": stat}).encode())

    def push_uplink(self, phy: bytes, rssi: int, snr: float) -> None:
        self.rx_count += 1
        rxpk = {
            "tmst": self.tmst(), "chan": 0, "rfch": 0,
            "freq": random.choice(EU868_DEFAULT_CHANNELS),
            "stat": 1, "modu": "LORA", "datr": "SF7BW125", "codr": "4/5",
            "rssi": rssi, "lsnr": snr, "size": len(phy),
            "data": base64.b64encode(phy).decode(),
        }
        self._send(PUSH_DATA, json.dumps({"rxpk": [rxpk]}).encode())

    def wait_downlink(self, timeout: float) -> bytes | None:
        try:
            return self.downlinks.get(timeout=timeout)
        except queue.Empty:
            return None


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fake_sensor(t: float) -> bytes:
    """devinci-node v1 payload (see codecs/devinci-node-v1.js)."""
    temp = 21.0 + 2.5 * math.sin(t / 600) + random.uniform(-0.2, 0.2)
    rh = 45 + random.uniform(-3, 3)
    batt_mv = 4100 - int((t / 3600) * 5)
    return struct.pack(">BhBH", 0x01, int(round(temp * 100)), int(round(rh * 2)), max(batt_mv, 3000))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default=os.getenv("GW_SERVER", "127.0.0.1"), help="gateway bridge host")
    p.add_argument("--port", type=int, default=1700)
    p.add_argument("--gateway-eui", default=os.getenv("GATEWAY_EUI", "0016c001ff100001"))
    p.add_argument("--dev-eui", default=os.getenv("DEV_EUI"), required=os.getenv("DEV_EUI") is None)
    p.add_argument("--join-eui", default=os.getenv("JOIN_EUI", "0000000000000000"))
    p.add_argument("--app-key", default=os.getenv("APP_KEY"), required=os.getenv("APP_KEY") is None,
                   help="LoRaWAN 1.0.x AppKey (hex)")
    p.add_argument("--interval", type=float, default=60, help="seconds between uplinks")
    p.add_argument("--count", type=int, default=0, help="uplinks to send (0 = forever)")
    p.add_argument("--confirmed", action="store_true", help="send confirmed uplinks")
    p.add_argument("--state-file", type=Path, default=Path(".virtual_gateway_state.json"),
                   help="remembers the last DevNonce (ChirpStack rejects re-used DevNonces)")
    a = p.parse_args()

    app_key = bytes.fromhex(a.app_key)
    gw = SemtechUdpGateway(a.server, a.port, a.gateway_eui)
    gw.start()
    if not gw.acked.wait(5):
        log(f"[gw] no PULL_ACK from {a.server}:{a.port} - is chirpstack-gateway-bridge running?")
        return 1
    log(f"[gw] {a.gateway_eui} connected to {a.server}:{a.port}")

    # ---- OTAA join
    state = load_state(a.state_file)
    dev_state = state.setdefault(a.dev_eui.lower(), {})
    session = None
    for attempt in range(1, 6):
        dev_nonce = dev_state.get("dev_nonce", -1) + 1
        dev_state["dev_nonce"] = dev_nonce
        a.state_file.write_text(json.dumps(state, indent=2))  # persist before sending
        log(f"[dev] join-request #{attempt} DevEUI={a.dev_eui} DevNonce={dev_nonce}")
        gw.push_uplink(lw.build_join_request(a.join_eui, a.dev_eui, dev_nonce, app_key), -60, 9.5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            phy = gw.wait_downlink(deadline - time.monotonic())
            if phy is None:
                break
            try:
                session = lw.parse_join_accept(phy, app_key, dev_nonce)
                break
            except ValueError as e:
                log(f"[dev] ignored downlink: {e}")
        if session:
            break
        log("[dev] no join-accept - check the device exists, keys match, and the gateway is registered")
    if not session:
        return 2
    log(f"[dev] joined, DevAddr={session.dev_addr:08x}")

    # ---- uplink loop
    t_start, sent, pending_ack, interval = time.monotonic(), 0, False, a.interval
    while a.count == 0 or sent < a.count:
        payload = fake_sensor(time.monotonic() - t_start)
        phy = lw.build_uplink(session, 1, payload, confirmed=a.confirmed, ack=pending_ack)
        pending_ack = False
        gw.push_uplink(phy, rssi=random.randint(-110, -70), snr=round(random.uniform(-5, 10), 1))
        log(f"[dev] uplink FCnt={session.f_cnt_up} fPort=1 payload={payload.hex()}")
        session.f_cnt_up += 1
        sent += 1

        # RX1/RX2 window: the bridge forwards downlinks almost immediately.
        deadline = time.monotonic() + session.rx_delay + 2
        while (phy := gw.wait_downlink(max(0.0, deadline - time.monotonic()))) is not None:
            try:
                dl = lw.parse_downlink(session, phy)
            except ValueError as e:
                log(f"[dev] ignored downlink: {e}")
                continue
            log(f"[dev] downlink FCnt={dl.f_cnt} port={dl.f_port} payload={dl.payload.hex() or '-'}"
                f"{' FOpts=' + dl.f_opts.hex() if dl.f_opts else ''}{' (ACK)' if dl.ack else ''}")
            pending_ack = dl.confirmed
            if dl.f_port == 2 and len(dl.payload) == 2:
                interval = max(60, struct.unpack(">H", dl.payload)[0])
                log(f"[dev] uplink interval set to {interval}s by downlink")

        if a.count == 0 or sent < a.count:
            time.sleep(max(0.0, interval - (session.rx_delay + 2)))

    gw.stop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
