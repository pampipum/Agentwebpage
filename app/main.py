import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("NAI_DB_PATH", BASE_DIR / "data" / "nai_one.db"))
WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL", "").strip()
TLS_VERIFY = os.getenv("NAI_TLS_VERIFY", "false").strip().lower() in {"1", "true", "yes"}

app = FastAPI(title="Nai One API", version="1.0.0")


class ScanRequest(BaseModel):
    businessUrl: str
    name: str
    email: EmailStr


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                business_url TEXT NOT NULL,
                score INTEGER NOT NULL,
                report_json TEXT NOT NULL,
                user_agent TEXT,
                ip_hint TEXT
            )
            """
        )
        conn.commit()


def normalize_url(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
      return None

    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"

    try:
        parsed = urlparse(raw)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return raw


def build_report(html: str, url: str) -> dict[str, Any]:
    lower = html.lower()
    findings: list[str] = []
    quick_wins: list[str] = []
    score = 100

    checks = {
        "has_title": "<title>" in lower,
        "has_meta_description": "name=\"description\"" in lower or "name='description'" in lower,
        "has_form": "<form" in lower,
        "has_contact_action": "mailto:" in lower or "tel:" in lower,
        "has_booking_intent": any(k in lower for k in ["book", "reservation", "appointment", "quote", "schedule"]),
    }

    if not checks["has_title"]:
        score -= 12
        findings.append("Missing clear page title signal for discoverability.")
        quick_wins.append("Add intent-driven title tags on primary service pages.")

    if not checks["has_meta_description"]:
        score -= 10
        findings.append("Meta description appears missing or weak.")
        quick_wins.append("Write conversion-focused meta descriptions to improve qualified clicks.")

    if not checks["has_form"]:
        score -= 18
        findings.append("No visible lead intake form detected.")
        quick_wins.append("Deploy a short intake form with immediate auto-response.")

    if not checks["has_contact_action"]:
        score -= 15
        findings.append("No one-tap contact action (email/phone) detected.")
        quick_wins.append("Add direct contact actions in hero and footer for low-friction conversion.")

    if not checks["has_booking_intent"]:
        score -= 10
        findings.append("No booking or quote intent flow detected.")
        quick_wins.append("Add quote/booking CTA with structured pre-qualification fields.")

    if url.startswith("http://"):
        score -= 10
        findings.append("Website uses HTTP instead of HTTPS.")
        quick_wins.append("Force HTTPS across all pages to improve trust and conversion confidence.")

    if not findings:
        findings.append("No major structural issues in the surface scan; workflow-level audit recommended.")
        quick_wins.append("Run operational workflow audit for response time and handoff automation.")

    score = max(25, min(100, score))

    return {
        "score": score,
        "summary": "Nai One completed a surface diagnostic and prioritized high-leverage automation actions.",
        "findings": findings,
        "quickWins": quick_wins,
    }


def save_lead(payload: ScanRequest, report: dict[str, Any], request: Request) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO leads (
                created_at, name, email, business_url, score, report_json, user_agent, ip_hint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                payload.name.strip(),
                payload.email,
                payload.businessUrl.strip(),
                int(report.get("score", 0)),
                json.dumps(report, ensure_ascii=True),
                request.headers.get("user-agent", ""),
                request.headers.get("cf-connecting-ip", "") or (request.client.host if request.client else ""),
            ),
        )
        conn.commit()


async def send_webhook(payload: dict[str, Any]) -> None:
    if not WEBHOOK_URL:
        return

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=TLS_VERIFY) as client:
            await client.post(WEBHOOK_URL, json=payload)
    except Exception:
        pass


@app.on_event("startup")
def startup_event() -> None:
    ensure_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.post("/api/scan")
async def scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
    normalized = normalize_url(payload.businessUrl)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid business URL")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=12.0,
            verify=TLS_VERIFY,
        ) as client:
            response = await client.get(
                normalized,
                headers={"User-Agent": "NaiOneDiagnosticBot/1.0 (+https://attikonlab.uk)"},
            )
            html = response.text
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Unable to fetch business URL") from exc

    report = build_report(html, normalized)
    payload.businessUrl = normalized

    save_lead(payload, report, request)

    webhook_payload = {
        "source": "attikonlab-nai-one-scan",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lead": {
            "name": payload.name,
            "email": payload.email,
            "businessUrl": payload.businessUrl,
        },
        "report": report,
    }
    await send_webhook(webhook_payload)

    return {"ok": True, "report": report}


if (BASE_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")

if (BASE_DIR / "styles").exists():
    app.mount("/styles", StaticFiles(directory=BASE_DIR / "styles"), name="styles")

if (BASE_DIR / "scripts").exists():
    app.mount("/scripts", StaticFiles(directory=BASE_DIR / "scripts"), name="scripts")
