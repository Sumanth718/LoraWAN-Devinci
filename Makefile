.PHONY: up up-all down logs test env

env:            ## create .env with random secrets (only if missing)
	@test -f .env || ( cp .env.example .env && \
	  for v in POSTGRES_PASSWORD CHIRPSTACK_API_SECRET INFLUXDB_ADMIN_PASSWORD INFLUXDB_TOKEN GRAFANA_ADMIN_PASSWORD; do \
	    s=$$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-32); \
	    sed -i.bak "s|^$$v=CHANGE_ME|$$v=$$s|" .env; done; rm -f .env.bak; echo ".env created" )

up: env         ## core LoRaWAN stack
	docker compose up -d

up-all: env     ## core + InfluxDB/Grafana
	docker compose --profile dashboards up -d

down:
	docker compose --profile dashboards --profile public down

logs:
	docker compose logs -f --tail=100 chirpstack chirpstack-gateway-bridge

test:           ## offline tests (no Docker needed)
	python3 -m unittest discover -s tests -v
	node tests/test_codec.js
