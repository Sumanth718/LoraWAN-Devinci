# Security notes: prototype posture and hardening backlog (seed for WP3)

This list records what the WP1 prototype deliberately leaves open, so the WP3 risk analysis
starts from facts rather than from a blank page. Each line is a candidate risk with its mitigation.

## What LoRaWAN already protects

- **Payload confidentiality, end to end:** AES-128 with the AppSKey, between the device and the
  application server. Gateways and the network server can't read application data.
- **Frame integrity and authenticity:** a MIC with the NwkSKey. Forged or modified frames are dropped.
- **Replay protection:** frame counters. Never enable "skip frame-counter check" outside debugging.
- **OTAA:** session keys are derived per join. Root keys (AppKey) never travel over the air.

## Open in the prototype → mitigation before production

| # | Item | Risk | Mitigation |
|---|---|---|---|
| 1 | ChirpStack default login `admin` / `admin` | Full takeover | Change it at first login; create per-student users per tenant |
| 2 | Semtech UDP (port 1700) is unauthenticated and in clear | Gateway impersonation, metadata sniffing, uplink/downlink injection at gateway level (payloads stay encrypted) | Gateway VLAN + firewall allow-list of gateway IPs; production gateways on Basics Station `wss://` with TLS client certs, or the ChirpStack MQTT forwarder over TLS |
| 3 | Mosquitto anonymous, plain MQTT | Anyone reaching 1883 reads decoded data and can inject gateway commands | Already bound to 127.0.0.1. Before exposing: password file + ACLs (per-application topics), TLS on 8883 |
| 4 | Web UI on HTTP :8080 | Credential sniffing | `public` profile (Caddy, HTTPS), then `CHIRPSTACK_UI_BIND=127.0.0.1` |
| 5 | Secrets in `.env` and `secrets.h` | Leak through Git | Both are git-ignored; one AppKey per device; rotate a key by re-provisioning the device |
| 6 | Device root keys stored in Postgres in clear | DB dump = all keys | Restrict DB access; encrypted backups; a join server / HSM if ever required |
| 7 | No backups | Data and provisioning loss | Nightly `pg_dump` + volume snapshot; test the restore |
| 8 | Image tags `:4`, `:2`, `:7` float | Unreviewed updates | Pin digests for production; monthly update window |
| 9 | Student devices = untrusted hardware | Flooding, duty-cycle abuse, rogue firmware | Per-device enable/disable in ChirpStack, per-tenant quotas, uplink-rate alerting |
| 10 | Logs | No audit trail | Ship container logs to the DSI's SIEM/syslog; ChirpStack event logs per device |
| 11 | Gateway OS (OpenWrt / Raspberry Pi OS) | Outdated firmware, SSH exposure | Admin access from management VLAN only, key-based SSH, update policy |
| 12 | Meshtastic network (WP4) | Shares the 868 MHz band; default channel keys are public | Private channel with its own PSK; separate from LoRaWAN infrastructure |
