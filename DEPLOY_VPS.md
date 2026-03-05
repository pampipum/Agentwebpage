# Deploy on VPS (FastAPI + Cloudflare Tunnel)

## 1) Install Python deps
```bash
cd /opt/attikonlab/Agentwebpage
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2) Run locally
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Test:
- `http://127.0.0.1:8090/health`
- `http://127.0.0.1:8090/`

## 3) systemd service
```bash
sudo cp deploy/nai-one.service /etc/systemd/system/nai-one.service
sudo systemctl daemon-reload
sudo systemctl enable --now nai-one
sudo systemctl status nai-one
```

## 4) Cloudflare Tunnel route
Point your tunnel ingress to `http://127.0.0.1:8090` for `www.attikonlab.uk`.

Example ingress snippet:
```yaml
ingress:
  - hostname: www.attikonlab.uk
    service: http://127.0.0.1:8090
  - service: http_status:404
```

## 5) Optional lead forwarding
Set `LEAD_WEBHOOK_URL` in `.env` and restart service:
```bash
sudo systemctl restart nai-one
```
