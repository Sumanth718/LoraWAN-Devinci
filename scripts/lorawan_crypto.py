"""Minimal LoRaWAN 1.0.x frame building / parsing (spec: LoRaWAN L2 1.0.4, chapters 4-6).

Kept dependency-light and readable on purpose: it is used by the virtual gateway
and doubles as teaching material for how OTAA, MIC and payload encryption work.
Only needs the `cryptography` package.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MTYPE_JOIN_REQUEST = 0
MTYPE_JOIN_ACCEPT = 1
MTYPE_UNCONFIRMED_UP = 2
MTYPE_UNCONFIRMED_DOWN = 3
MTYPE_CONFIRMED_UP = 4
MTYPE_CONFIRMED_DOWN = 5

UP, DOWN = 0, 1


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def aes_cmac(key: bytes, data: bytes) -> bytes:
    c = cmac.CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def eui_le(eui_hex: str) -> bytes:
    """EUIs are written MSB-first by humans but sent LSB-first on air."""
    return bytes.fromhex(eui_hex)[::-1]


# ---------------------------------------------------------------- OTAA

def build_join_request(join_eui: str, dev_eui: str, dev_nonce: int, app_key: bytes) -> bytes:
    msg = bytes([MTYPE_JOIN_REQUEST << 5]) + eui_le(join_eui) + eui_le(dev_eui) + struct.pack("<H", dev_nonce)
    return msg + aes_cmac(app_key, msg)[:4]


@dataclass
class Session:
    dev_addr: int
    nwk_s_key: bytes
    app_s_key: bytes
    f_cnt_up: int = 0
    f_cnt_down: int = 0
    rx1_dr_offset: int = 0
    rx2_dr: int = 0
    rx_delay: int = 1


def parse_join_accept(phy: bytes, app_key: bytes, dev_nonce: int) -> Session:
    """Decrypt + verify a 1.0.x Join-Accept and derive the session keys."""
    if len(phy) not in (17, 33) or phy[0] >> 5 != MTYPE_JOIN_ACCEPT:
        raise ValueError(f"not a join-accept ({len(phy)} bytes, MHDR {phy[0]:#04x})")
    # The network server *decrypts* with AES so the device only needs AES *encrypt*.
    plain = aes_ecb_encrypt(app_key, phy[1:])
    body, mic = plain[:-4], plain[-4:]
    if aes_cmac(app_key, phy[:1] + body)[:4] != mic:
        raise ValueError("join-accept MIC mismatch (wrong AppKey?)")

    join_nonce, net_id = body[0:3], body[3:6]
    dev_addr = struct.unpack("<I", body[6:10])[0]
    dl_settings, rx_delay = body[10], body[11]

    def derive(prefix: int) -> bytes:
        block = bytes([prefix]) + join_nonce + net_id + struct.pack("<H", dev_nonce)
        return aes_ecb_encrypt(app_key, block.ljust(16, b"\x00"))

    return Session(
        dev_addr=dev_addr,
        nwk_s_key=derive(0x01),
        app_s_key=derive(0x02),
        rx1_dr_offset=(dl_settings >> 4) & 0x07,
        rx2_dr=dl_settings & 0x0F,
        rx_delay=rx_delay or 1,
    )


# ---------------------------------------------------------------- data frames

def _crypt_payload(key: bytes, direction: int, dev_addr: int, f_cnt: int, payload: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(payload), 16):
        a = bytes([0x01, 0, 0, 0, 0, direction]) + struct.pack("<II", dev_addr, f_cnt) + bytes([0, i // 16 + 1])
        s = aes_ecb_encrypt(key, a)
        out += bytes(p ^ k for p, k in zip(payload[i:i + 16], s))
    return bytes(out)


def _mic(nwk_s_key: bytes, direction: int, dev_addr: int, f_cnt: int, msg: bytes) -> bytes:
    b0 = bytes([0x49, 0, 0, 0, 0, direction]) + struct.pack("<II", dev_addr, f_cnt) + bytes([0, len(msg)])
    return aes_cmac(nwk_s_key, b0 + msg)[:4]


def build_uplink(s: Session, f_port: int, payload: bytes, confirmed: bool = False,
                 ack: bool = False, adr: bool = False, f_opts: bytes = b"") -> bytes:
    """f_opts = MAC commands piggy-backed in the header (max 15 bytes, sent in clear in 1.0.x)."""
    if len(f_opts) > 15:
        raise ValueError("FOpts max 15 bytes")
    mtype = MTYPE_CONFIRMED_UP if confirmed else MTYPE_UNCONFIRMED_UP
    f_ctrl = (0x80 if adr else 0) | (0x20 if ack else 0) | len(f_opts)
    key = s.nwk_s_key if f_port == 0 else s.app_s_key
    msg = (bytes([mtype << 5]) + struct.pack("<IBH", s.dev_addr, f_ctrl, s.f_cnt_up & 0xFFFF) + f_opts
           + bytes([f_port]) + _crypt_payload(key, UP, s.dev_addr, s.f_cnt_up, payload))
    return msg + _mic(s.nwk_s_key, UP, s.dev_addr, s.f_cnt_up, msg)


@dataclass
class Downlink:
    confirmed: bool
    ack: bool
    f_cnt: int
    f_opts: bytes
    f_port: int | None
    payload: bytes


def parse_downlink(s: Session, phy: bytes) -> Downlink:
    mtype = phy[0] >> 5
    if mtype not in (MTYPE_UNCONFIRMED_DOWN, MTYPE_CONFIRMED_DOWN):
        raise ValueError(f"not a data downlink (MType {mtype})")
    msg, mic = phy[:-4], phy[-4:]
    dev_addr, f_ctrl, f_cnt16 = struct.unpack("<IBH", msg[1:8])
    if dev_addr != s.dev_addr:
        raise ValueError(f"downlink for another device ({dev_addr:08x})")
    # Rebuild the 32-bit counter from the 16 bits on air.
    f_cnt = (s.f_cnt_down & 0xFFFF0000) | f_cnt16
    if f_cnt < s.f_cnt_down:
        f_cnt += 0x10000
    if _mic(s.nwk_s_key, DOWN, dev_addr, f_cnt, msg) != mic:
        raise ValueError("downlink MIC mismatch")
    fo_len = f_ctrl & 0x0F
    f_opts = msg[8:8 + fo_len]
    rest = msg[8 + fo_len:]
    f_port, payload = None, b""
    if rest:
        f_port = rest[0]
        key = s.nwk_s_key if f_port == 0 else s.app_s_key
        payload = _crypt_payload(key, DOWN, dev_addr, f_cnt, rest[1:])
    s.f_cnt_down = f_cnt + 1
    return Downlink(mtype == MTYPE_CONFIRMED_DOWN, bool(f_ctrl & 0x20), f_cnt, f_opts, f_port, payload)
