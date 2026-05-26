# Systemd units

## `omnia-orchestrator.service`

Manages the V2 orchestrator daemon on the production VPS. Replaces the manual
`uv run uvicorn ...` invocation that doesn't survive a reboot.

### Install (one-shot, on the VPS)

```bash
sudo cp /opt/omnia/infra/systemd/omnia-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omnia-orchestrator
```

### Verify

```bash
sudo systemctl status omnia-orchestrator
curl -sS http://127.0.0.1:8003/health
journalctl -u omnia-orchestrator -n 50 --no-pager
```

### Update (after pulling new orchestrator code)

```bash
# If pyproject.toml changed (new deps), sync them first:
sudo -u i48ptgvnis bash -c 'cd /opt/omnia/apps/orchestrator && uv sync'
sudo systemctl restart omnia-orchestrator
```

### Prerequisites the unit assumes

- User `i48ptgvnis` exists, owns `/opt/omnia/` and `/opt/omnia-runtime/`, and
  is in the `docker` group (so the Docker socket works without sudo).
- `uv` installed at `/home/i48ptgvnis/.local/bin/uv` (per the user's PATH).
- Sudoers rules for `nginx -t`, `systemctl reload nginx`, and `acme.sh` live
  in `/etc/sudoers.d/omnia-orchestrator` (see `docs/08-vps-setup.md`).
- Redis 7 is up on `redis://127.0.0.1:6379/0` (hibernate activity bus).
  Without Redis the orchestrator still works in degraded mode — hibernate
  will still hibernate at threshold but without ingress-side activity input.
