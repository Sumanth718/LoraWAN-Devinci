"""Checks scripts/lorawan_crypto.py against the test vectors of ChirpStack's own
LoRaWAN library (lrwn crate: src/phy_payload.rs doc-tests and src/keys.rs tests).
If these pass, the virtual device computes exactly what the network server expects.

    python3 -m unittest discover -s tests
"""
import struct
import sys
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lorawan_crypto as lw  # noqa: E402


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def make_join_accept(app_key, join_nonce, net_id_be, dev_addr, dl_settings=0, rx_delay=0, cflist=b""):
    """Server side of a 1.0.x join-accept (what ChirpStack does)."""
    mhdr = bytes([lw.MTYPE_JOIN_ACCEPT << 5])
    body = (join_nonce.to_bytes(3, "little") + bytes.fromhex(net_id_be)[::-1]
            + struct.pack("<I", dev_addr) + bytes([dl_settings, rx_delay]) + cflist)
    mic = lw.aes_cmac(app_key, mhdr + body)[:4]
    return mhdr + aes_ecb_decrypt(app_key, body + mic)


KEY = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")


class ChirpStackVectors(unittest.TestCase):
    def test_join_request(self):
        phy = lw.build_join_request("0101010101010101", "0202020202020202", 771, KEY)
        self.assertEqual(phy.hex(), "00" + "01" * 8 + "02" * 8 + "0303" + "09b97b32")

    def test_join_accept_decrypt_and_mic(self):
        phy = bytes([0x20, 0x23, 0xcf, 0x33, 0x54, 0x89, 0xaa, 0xe3, 0x18,
                     0x3c, 0x0b, 0xe0, 0xba, 0xa8, 0xde, 0xe5, 0xf3])
        s = lw.parse_join_accept(phy, KEY, dev_nonce=258)
        self.assertEqual(s.dev_addr, 0x01020304)

    def test_join_accept_wrong_key_rejected(self):
        phy = make_join_accept(KEY, 1, "000000", 0x26000001)
        with self.assertRaises(ValueError):
            lw.parse_join_accept(phy, bytes(16), dev_nonce=0)

    def test_session_key_derivation_1_0(self):
        nwk_key = bytes([1, 2, 3, 4, 5, 6, 7, 8] * 2)
        phy = make_join_accept(nwk_key, join_nonce=65536, net_id_be="010203", dev_addr=0x01020304)
        s = lw.parse_join_accept(phy, nwk_key, dev_nonce=258)
        self.assertEqual(list(s.nwk_s_key),
                         [223, 83, 195, 95, 48, 52, 204, 206, 208, 255, 53, 76, 112, 222, 4, 223])
        self.assertEqual(list(s.app_s_key),
                         [146, 123, 156, 145, 17, 131, 207, 254, 76, 178, 255, 75, 117, 84, 95, 109])

    def test_join_accept_with_cflist(self):
        cflist = bytes.fromhex("184f84e85684b85e84886684") + bytes(3) + b"\x00"  # EU868 extra channels
        phy = make_join_accept(KEY, 7, "000000", 0x26001234, dl_settings=0x00, rx_delay=1, cflist=cflist)
        self.assertEqual(len(phy), 33)
        self.assertEqual(lw.parse_join_accept(phy, KEY, 5).dev_addr, 0x26001234)

    def test_confirmed_uplink_1_0(self):
        s = lw.Session(dev_addr=0x01020304, nwk_s_key=KEY,
                       app_s_key=bytes.fromhex("100f0e0d0c0b0a090807060504030201"))
        phy = lw.build_uplink(s, 10, bytes([1, 2, 3, 4]), confirmed=True, f_opts=bytes([0x06, 0x73, 0x07]))
        self.assertEqual(list(phy), [0x80, 0x04, 0x03, 0x02, 0x01, 0x03, 0x00, 0x00, 0x06, 0x73, 0x07,
                                     0x0a, 0xe2, 0x64, 0xd4, 0xf7, 0xe1, 0x17, 0xd2, 0xc0])

    def test_downlink_roundtrip(self):
        s = lw.Session(dev_addr=0x26011234, nwk_s_key=KEY, app_s_key=bytes(range(16)))
        # Build a downlink the way the server would: same algorithm, Dir=1.
        payload = struct.pack(">H", 300)
        msg = (bytes([lw.MTYPE_CONFIRMED_DOWN << 5]) + struct.pack("<IBH", s.dev_addr, 0x20, 0)
               + bytes([2]) + lw._crypt_payload(s.app_s_key, lw.DOWN, s.dev_addr, 0, payload))
        phy = msg + lw._mic(s.nwk_s_key, lw.DOWN, s.dev_addr, 0, msg)
        dl = lw.parse_downlink(s, phy)
        self.assertEqual((dl.f_port, dl.payload, dl.confirmed, dl.ack), (2, payload, True, True))
        self.assertEqual(s.f_cnt_down, 1)
        with self.assertRaises(ValueError):  # tampered frame
            lw.parse_downlink(s, phy[:-1] + bytes([phy[-1] ^ 1]))


if __name__ == "__main__":
    unittest.main()
