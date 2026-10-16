"""Runs scripts/provision.py against a mock chirpstack-rest-api and checks the calls it makes
(paths and field names follow chirpstack/api/proto/api/*.proto)."""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALLS = []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        CALLS.append((self.command, self.path, body, self.headers.get("Grpc-Metadata-Authorization")))
        p = self.path.split("?")[0]
        if p == "/api/tenants":
            return self._reply(200, {"totalCount": 1, "result": [{"id": "t-1", "name": "ChirpStack"}]})
        if self.command == "GET":
            return self._reply(200, {"totalCount": 0, "result": []})
        if p == "/api/gateways":
            return self._reply(409, {"code": 6, "message": "already exists"})
        if p == "/api/device-profiles":
            return self._reply(200, {"id": "dp-1"})
        if p == "/api/applications":
            return self._reply(200, {"id": "app-1"})
        return self._reply(200, {})

    do_GET = do_POST = do_PUT = _handle


class ProvisionTest(unittest.TestCase):
    def test_flow(self):
        srv = HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {**os.environ, "CHIRPSTACK_API_KEY": "tok", "INFLUXDB_TOKEN": "itok"}
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "provision.py"),
                            "--api", f"http://127.0.0.1:{srv.server_port}", "--gateway-eui", "0016C001FF100001",
                            "--dev-eui", "0200000000000001", "--app-key", "2b7e151628aed2a6abf7158809cf4f3c",
                            "--influxdb"], capture_output=True, text=True, env=env, timeout=30)
        srv.shutdown()
        srv.server_close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        by_path = {(m, p.split("?")[0]): b for m, p, b, _ in CALLS}
        self.assertTrue(all(auth == "Bearer tok" for *_, auth in CALLS))
        self.assertEqual(by_path[("POST", "/api/gateways")]["gateway"]["gatewayId"], "0016c001ff100001")
        dp = by_path[("POST", "/api/device-profiles")]["deviceProfile"]
        self.assertEqual((dp["region"], dp["macVersion"], dp["payloadCodecRuntime"]),
                         ("EU868", "LORAWAN_1_0_4", "JS"))
        self.assertIn("function decodeUplink", dp["payloadCodecScript"])
        dev = by_path[("POST", "/api/devices")]["device"]
        self.assertEqual((dev["applicationId"], dev["deviceProfileId"]), ("app-1", "dp-1"))
        keys = by_path[("POST", "/api/devices/0200000000000001/keys")]["deviceKeys"]
        self.assertEqual(keys["nwkKey"], "2b7e151628aed2a6abf7158809cf4f3c")
        influx = by_path[("POST", "/api/applications/app-1/integrations/influxdb")]["integration"]
        self.assertEqual((influx["version"], influx["token"]), ("INFLUXDB_2", "itok"))
        self.assertIn("already existed", r.stdout)
        self.assertIn("LORAWAN_APP_KEY  0x2b, 0x7e", r.stdout)


if __name__ == "__main__":
    unittest.main()
