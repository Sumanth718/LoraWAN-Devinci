"""End-to-end test of scripts/virtual_gateway.py against a tiny fake network server that
speaks the Semtech UDP protocol (stand-in for chirpstack-gateway-bridge + ChirpStack).

Covers: PULL_DATA/PULL_ACK, PUSH_DATA/PUSH_ACK, OTAA join over PULL_RESP, TX_ACK,
uplink MIC + decryption, codec payload format, downlink reception, DevNonce persistence.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lorawan_crypto as lw  # noqa: E402
from test_lorawan_crypto import make_join_accept  # noqa: E402

APP_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
DEV_EUI, JOIN_EUI, GW_EUI = "0200000000000001", "0000000000000000", "0016c001ff100001"
DEV_ADDR = 0x26011BDA


class FakeNetworkServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.pull_addr = None
        self.session = None
        self.uplinks, self.dev_nonces, self.tx_acks, self.stats = [], [], 0, 0
        self.stop = threading.Event()

    def send_downlink(self, phy: bytes):
        txpk = {"imme": False, "tmst": 0, "freq": 869.525, "rfch": 0, "powe": 14, "modu": "LORA",
                "datr": "SF12BW125", "codr": "4/5", "ipol": True, "size": len(phy),
                "data": base64.b64encode(phy).decode()}
        self.sock.sendto(struct.pack(">BHB", 2, 0xBEEF, 3) + json.dumps({"txpk": txpk}).encode(), self.pull_addr)

    def run(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            ver, token, ident = struct.unpack(">BHB", data[:4])
            assert ver == 2 and data[4:12] == bytes.fromhex(GW_EUI)
            if ident == 2:  # PULL_DATA
                self.pull_addr = addr
                self.sock.sendto(struct.pack(">BHB", 2, token, 4), addr)
            elif ident == 5:  # TX_ACK
                self.tx_acks += 1
            elif ident == 0:  # PUSH_DATA
                self.sock.sendto(struct.pack(">BHB", 2, token, 1), addr)
                body = json.loads(data[12:])
                self.stats += "stat" in body
                for rx in body.get("rxpk", []):
                    self.handle(base64.b64decode(rx["data"]))

    def handle(self, phy: bytes):
        mtype = phy[0] >> 5
        if mtype == lw.MTYPE_JOIN_REQUEST:
            assert lw.aes_cmac(APP_KEY, phy[:-4])[:4] == phy[-4:], "join-request MIC"
            dev_nonce = struct.unpack("<H", phy[17:19])[0]
            self.dev_nonces.append(dev_nonce)
            ja = make_join_accept(APP_KEY, 1 + len(self.dev_nonces), "000000", DEV_ADDR, rx_delay=1,
                                  cflist=bytes(16))
            self.session = lw.parse_join_accept(ja, APP_KEY, dev_nonce)  # server-side copy of keys
            self.send_downlink(ja)
        elif mtype in (lw.MTYPE_UNCONFIRMED_UP, lw.MTYPE_CONFIRMED_UP):
            s = self.session
            f_cnt = struct.unpack("<H", phy[6:8])[0]
            assert lw._mic(s.nwk_s_key, lw.UP, s.dev_addr, f_cnt, phy[:-4]) == phy[-4:], "uplink MIC"
            f_port, enc = phy[8], phy[9:-4]
            self.uplinks.append((f_cnt, f_port, lw._crypt_payload(s.app_s_key, lw.UP, s.dev_addr, f_cnt, enc)))
            if len(self.uplinks) == 2:  # answer the last uplink with a port-2 downlink
                msg = (bytes([lw.MTYPE_UNCONFIRMED_DOWN << 5]) + struct.pack("<IBH", s.dev_addr, 0, 0)
                       + bytes([2]) + lw._crypt_payload(s.app_s_key, lw.DOWN, s.dev_addr, 0, b"\x01\x2c"))
                self.send_downlink(msg + lw._mic(s.nwk_s_key, lw.DOWN, s.dev_addr, 0, msg))


class VirtualGatewayE2E(unittest.TestCase):
    def run_sim(self, ns, state):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "virtual_gateway.py"), "--port", str(ns.port),
             "--gateway-eui", GW_EUI, "--dev-eui", DEV_EUI, "--join-eui", JOIN_EUI,
             "--app-key", APP_KEY.hex(), "--count", "2", "--interval", "3", "--state-file", state],
            capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONUNBUFFERED": "1"})

    def test_join_uplinks_downlink(self):
        ns = FakeNetworkServer()
        ns.start()
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state.json")
            r = self.run_sim(ns, state)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(ns.dev_nonces, [0])
            self.assertEqual([(c, p) for c, p, _ in ns.uplinks], [(0, 1), (1, 1)])
            ver, temp, rh, mv = struct.unpack(">BhBH", ns.uplinks[0][2])
            self.assertEqual(ver, 1)
            self.assertTrue(1500 < temp < 2600 and 60 < rh < 110 and 3000 <= mv <= 4100)
            self.assertIn("joined, DevAddr=26011bda", r.stdout)
            self.assertIn("port=2 payload=012c", r.stdout)
            self.assertIn("interval set to 300s", r.stdout)
            self.assertGreaterEqual(ns.tx_acks, 2)  # join-accept + data downlink
            self.assertGreaterEqual(ns.stats, 1)

            # A second run must not re-use the DevNonce (ChirpStack would reject it).
            ns.uplinks.clear()
            r = self.run_sim(ns, state)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(ns.dev_nonces, [0, 1])
        ns.stop.set()
        ns.sock.close()


if __name__ == "__main__":
    unittest.main()
