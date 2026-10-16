#!/usr/bin/env python3
"""Provision a fresh ChirpStack with everything needed for a first end-to-end test.

Creates (or reuses, so it is safe to re-run):
  * a gateway             (your Pi + SX1302, or the virtual gateway)
  * a device profile      "devinci-node v1" - EU868, LoRaWAN 1.0.4, OTAA, JS codec
  * an application        "campus-test"
  * one OTAA device       random DevEUI/AppKey unless you pass them
  * optionally the InfluxDB integration (--influxdb), for Grafana dashboards

Prerequisite: in the ChirpStack UI (http://<host>:8080, admin/admin -> change it!)
go to "API keys" > "Add API key" and export it:

    export CHIRPSTACK_API_KEY=eyJ...
    python3 scripts/provision.py --gateway-eui 0016c001ff100001 --influxdb

Uses only the Python standard library. Talks to chirpstack-rest-api on 127.0.0.1:8090.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODEC = ROOT / "codecs" / "devinci-node-v1.js"
# MAC version -> (ChirpStack enum, matching Regional Parameters revision)
MAC_VERSIONS = {
    "1.0.2": ("LORAWAN_1_0_2", "B"),             # e.g. MKR WAN 1310 (Murata firmware)
    "1.0.3": ("LORAWAN_1_0_3", "A"),
    "1.0.4": ("LORAWAN_1_0_4", "RP002_1_0_3"),   # RadioLib (Heltec), virtual gateway
}


class Api:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, ok_conflict: bool = False):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Grpc-Metadata-Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if ok_conflict and e.code == 409:
                return None
            detail = e.read().decode(errors="replace")
            sys.exit(f"{method} {path} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            sys.exit(f"cannot reach {self.base}: {e.reason} (is chirpstack-rest-api running?)")


def find_by_name(api: Api, path: str, tenant_id: str, name: str) -> str | None:
    res = api.call("GET", f"{path}?tenant_id={tenant_id}&limit=100&search={urllib.parse.quote(name)}")
    for item in res.get("result", []):
        if item.get("name") == name:
            return item["id"]
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.getenv("CHIRPSTACK_REST", "http://127.0.0.1:8090"))
    p.add_argument("--api-key", default=os.getenv("CHIRPSTACK_API_KEY"))
    p.add_argument("--tenant-id", help="default: first tenant (the built-in 'ChirpStack' tenant)")
    p.add_argument("--gateway-eui", required=True, help="16 hex chars, as shown by your gateway")
    p.add_argument("--gateway-name", default="campus-parc-gw01")
    p.add_argument("--application", default="campus-test")
    p.add_argument("--device-name", default="node-01")
    p.add_argument("--dev-eui", help="default: random (locally administered)")
    p.add_argument("--join-eui", default="0000000000000000")
    p.add_argument("--app-key", help="default: random 128-bit key")
    p.add_argument("--mac-version", choices=sorted(MAC_VERSIONS), default="1.0.4",
                   help="LoRaWAN version the device implements (default 1.0.4)")
    p.add_argument("--influxdb", action="store_true", help="also configure the InfluxDB v2 integration")
    p.add_argument("--influxdb-url", default="http://influxdb:8086/api/v2/write",
                   help="as seen from the chirpstack container")
    a = p.parse_args()
    if not a.api_key:
        sys.exit("set CHIRPSTACK_API_KEY (ChirpStack UI > API keys > Add API key)")

    api = Api(a.api, a.api_key)
    gw_eui = a.gateway_eui.lower()
    # 0x02 in the first byte = "locally administered" EUI, so it can never clash with a vendor EUI.
    dev_eui = (a.dev_eui or "02" + secrets.token_hex(7)).lower()
    app_key = (a.app_key or secrets.token_hex(16)).lower()

    mac_enum, rp_rev = MAC_VERSIONS[a.mac_version]
    profile_name = f"devinci-node v1 (EU868, {a.mac_version}, OTAA)"
    tenant_id = a.tenant_id
    if not tenant_id:
        tenants = api.call("GET", "/api/tenants?limit=10").get("result", [])
        if not tenants:
            sys.exit("no tenant found; create one in the UI or pass --tenant-id")
        tenant_id = tenants[0]["id"]
    print(f"tenant          {tenant_id}")

    created = api.call("POST", "/api/gateways", {"gateway": {
        "gatewayId": gw_eui, "name": a.gateway_name, "tenantId": tenant_id,
        "description": "Created by scripts/provision.py", "statsInterval": 30,
    }}, ok_conflict=True)
    print(f"gateway         {gw_eui} ({'created' if created is not None else 'already existed'})")

    dp_id = find_by_name(api, "/api/device-profiles", tenant_id, profile_name)
    if not dp_id:
        dp_id = api.call("POST", "/api/device-profiles", {"deviceProfile": {
            "tenantId": tenant_id, "name": profile_name,
            "description": "Generic lab node, payload format in codecs/devinci-node-v1.js",
            "region": "EU868", "macVersion": mac_enum, "regParamsRevision": rp_rev,
            "adrAlgorithmId": "default", "supportsOtaa": True, "flushQueueOnActivate": True,
            "uplinkInterval": 3600,  # device shown as inactive after 1 h without uplink
            "payloadCodecRuntime": "JS", "payloadCodecScript": CODEC.read_text(),
        }})["id"]
        print(f"device profile  {dp_id} (created)")
    else:
        print(f"device profile  {dp_id} (reused)")

    app_id = find_by_name(api, "/api/applications", tenant_id, a.application)
    if not app_id:
        app_id = api.call("POST", "/api/applications", {"application": {
            "tenantId": tenant_id, "name": a.application,
            "description": "End-to-end test application (WP1)",
        }})["id"]
        print(f"application     {app_id} (created)")
    else:
        print(f"application     {app_id} (reused)")

    created = api.call("POST", "/api/devices", {"device": {
        "devEui": dev_eui, "name": a.device_name, "applicationId": app_id,
        "deviceProfileId": dp_id, "joinEui": a.join_eui,
    }}, ok_conflict=True)
    # LoRaWAN 1.0.x: the AppKey goes in the nwkKey field (see ChirpStack api/device.proto).
    keys = {"deviceKeys": {"devEui": dev_eui, "nwkKey": app_key, "appKey": "0" * 32}}
    if created is not None:
        api.call("POST", f"/api/devices/{dev_eui}/keys", keys)
    else:
        api.call("PUT", f"/api/devices/{dev_eui}/keys", keys)
    print(f"device          {dev_eui} ({'created' if created is not None else 'keys updated'})")

    if a.influxdb:
        token = os.getenv("INFLUXDB_TOKEN")
        if not token:
            sys.exit("--influxdb needs INFLUXDB_TOKEN in the environment (same value as in .env)")
        integ = {"integration": {
            "applicationId": app_id, "endpoint": a.influxdb_url, "version": "INFLUXDB_2",
            "token": token, "organization": os.getenv("INFLUXDB_ORG", "devinci"),
            "bucket": os.getenv("INFLUXDB_BUCKET", "lorawan"),
        }}
        if api.call("POST", f"/api/applications/{app_id}/integrations/influxdb", integ, ok_conflict=True) is None:
            api.call("PUT", f"/api/applications/{app_id}/integrations/influxdb", integ)
        print("influxdb        integration configured")

    key_c = ", ".join(f"0x{app_key[i:i + 2]}" for i in range(0, 32, 2))
    print(f"""
Done. Next steps
----------------
Virtual device (no hardware):
  python3 scripts/virtual_gateway.py --gateway-eui {gw_eui} \\
      --dev-eui {dev_eui} --app-key {app_key}

Heltec V3 - firmware/heltec-v3-otaa/include/secrets.h:
  #define LORAWAN_JOIN_EUI 0x{a.join_eui}
  #define LORAWAN_DEV_EUI  0x{dev_eui}
  #define LORAWAN_APP_KEY  {key_c}

MKR WAN 1310 - firmware/mkrwan1310-otaa/arduino_secrets.h (provision with --mac-version 1.0.2):
  #define SECRET_APP_EUI "{a.join_eui}"
  #define SECRET_APP_KEY "{app_key}"

Use a different DevEUI for each physical / virtual device (re-run with --device-name node-02).
""")


if __name__ == "__main__":
    main()
