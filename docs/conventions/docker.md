# Docker Conventions

Linked from [AGENTS.md](../../AGENTS.md).

- Production image: `ghcr.io/paulwaite87/atmos-gl:latest`
- `PYTHONPATH=/opt/project/src` in the container — see
  [Architecture & repository layout](architecture.md) for why only `src/` is importable
- Service-level env vars (API keys, DB credentials) live in `docker-compose.yml`
  under the relevant service, not in a global `.env` unless shared across services
- `AIS_API_KEY` and `OPENWEATHER_API_KEY` belong on the `data_collector` service
