# Health Checks

CaddyBuddy exposes unauthenticated liveness and readiness endpoints for container and service monitoring.

## Liveness

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
```

The endpoint returns HTTP `200` when the web process can serve requests.

## Readiness

```bash
curl --fail http://127.0.0.1:8000/api/v1/ready
```

Readiness returns HTTP `200` only when:

- runtime status can be evaluated;
- onboarding is complete;
- the configured Caddy Admin API is reachable.

It returns HTTP `503` while one of these conditions is not satisfied.

## Build information

```bash
curl http://127.0.0.1:8000/api/v1/build-info
```

This endpoint reports the application version and build metadata and can be used to confirm a deployment upgrade.
