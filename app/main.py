import json
import os
import re
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
LLM_API_KEY = os.getenv("NAI_LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("NAI_LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_MODEL = os.getenv("NAI_LLM_MODEL", "openai/gpt-4o-mini")
LLM_TIMEOUT = float(os.getenv("NAI_LLM_TIMEOUT", "30"))

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

    reservation_mail_match = re.search(r"mailto:([^\"'>]*reserv[^\"'>]*)", lower)
    pdf_link_count = len(re.findall(r"href=[\"'][^\"']+\\.pdf[\"']", lower))
    language_hint_count = len(
        set(re.findall(r"/(deutsch|english|italiano|francais|français|espanol|español)/", lower))
    )
    cta_links_count = len(re.findall(r"<a\\b", lower))

    checks = {
        "has_title": "<title>" in lower,
        "has_meta_description": "name=\"description\"" in lower or "name='description'" in lower,
        "has_form": "<form" in lower,
        "has_mailto": "mailto:" in lower,
        "has_tel": "tel:" in lower,
        "has_booking_intent": any(k in lower for k in ["book", "reservation", "appointment", "quote", "schedule"]),
        "has_local_business_schema": "localbusiness" in lower or "restaurant" in lower and "application/ld+json" in lower,
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

    if not (checks["has_mailto"] or checks["has_tel"]):
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

    if reservation_mail_match and not checks["has_form"]:
        score -= 8
        reservation_mail = reservation_mail_match.group(1)
        findings.append(
            f"Reservations appear email-driven (evidence: {reservation_mail}), which can slow response and drop conversions."
        )
        quick_wins.append("Add instant reservation request flow with auto-confirmation and operator queue.")

    if pdf_link_count > 0:
        score -= 6
        findings.append(f"Detected {pdf_link_count} PDF link(s); document-based flows often add friction on mobile.")
        quick_wins.append("Convert high-value PDFs (menu/intake) into mobile-native interactive pages.")

    if language_hint_count <= 1:
        score -= 5
        findings.append("Limited multi-language signals detected; tourist conversion may be constrained.")
        quick_wins.append("Add a lightweight multilingual entry layer for key booking/intake flows.")

    if cta_links_count < 8:
        score -= 4
        findings.append("Low CTA/link density detected on page surface.")
        quick_wins.append("Add clear CTA hierarchy (Book / Call / Message) above the fold.")

    if not checks["has_local_business_schema"]:
        score -= 4
        findings.append("No clear LocalBusiness structured data signal detected.")
        quick_wins.append("Implement LocalBusiness schema for better local visibility and trust signals.")

    if not findings:
        findings.append("No major structural issues in the surface scan; workflow-level audit recommended.")
        quick_wins.append("Run operational workflow audit for response time and handoff automation.")

    score = max(25, min(100, score))

    return {
        "score": score,
        "summary": "Nai One completed an evidence-based surface diagnostic and prioritized high-leverage automation actions.",
        "findings": findings,
        "quickWins": quick_wins,
        "analysisType": "surface-html-heuristic-v2",
    }


def extract_visible_text(html: str, max_chars: int = 9000) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def parse_json_from_text(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\\s*", "", raw)
        raw = re.sub(r"\\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\\{.*\\}", raw, flags=re.S)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def normalize_to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("issue") or item.get("name") or "").strip()
                detail = str(item.get("detail") or item.get("description") or "").strip()
                status = str(item.get("status") or "").strip()
                combined = " - ".join([part for part in [title, detail] if part])
                if status:
                    combined = f"[{status}] {combined}" if combined else f"[{status}]"
                if combined:
                    out.append(combined)
            else:
                text = str(item).strip()
                if text:
                    out.append(text)
        return out

    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


async def generate_llm_report(
    url: str,
    heuristic_report: dict[str, Any],
    page_text: str,
) -> dict[str, Any] | None:
    if not LLM_API_KEY:
        return None

    system_prompt = """
You are Nai One, an AI business operations and automation auditor developed by Attikon Lab.
You are not a website SEO scanner. You produce operational intelligence for business owners.

Core rule:
- Website signals are only the starting point.
- Reconstruct the likely end-to-end operating model of the business.
- Diagnose revenue leakage and operational friction.
- Propose automation opportunities in business-outcome language.
- Never mention implementation tools/vendors (no Zapier, Airtable, HubSpot, etc).

Signal confidence:
- Classify inferred signals as CONFIRMED, LIKELY, UNKNOWN, or MISSING.
- Never fabricate unavailable facts.

Return ONLY valid JSON with this exact shape:
{
  "executiveSummary": "string",
  "businessOperationalModel": {
    "businessType": "string",
    "revenueModel": "string",
    "customerTypes": ["string"],
    "demandChannels": ["string"],
    "workflowStages": ["string"]
  },
  "operationalFriction": [
    {
      "title": "string",
      "signalStatus": "CONFIRMED|LIKELY|UNKNOWN|MISSING",
      "evidence": "string",
      "operationalImpact": "string",
      "businessImpact": "string",
      "automationOpportunity": "string",
      "impact": "High|Medium|Low",
      "difficulty": "Easy|Medium|Advanced"
    }
  ],
  "automationOpportunities": [
    {
      "title": "string",
      "impact": "High|Medium|Low",
      "difficulty": "Easy|Medium|Advanced",
      "description": "string",
      "expectedImpact": "string"
    }
  ],
  "scoreBreakdown": {
    "demandCapture": 0,
    "customerCommunication": 0,
    "operationalAutomation": 0,
    "dataInfrastructure": 0,
    "overall": 0
  },
  "strategicInsight": "string",
  "revenueExpansionOpportunities": ["string"],
  "implementationRoadmap": [
    {"phase":"Phase 1","title":"string","goal":"string"}
  ],
  "quickWins": ["string"]
}
"""

    user_prompt = {
        "url": url,
        "heuristicReport": heuristic_report,
        "visiblePageTextExcerpt": page_text,
        "instruction": "Produce an operations-first mini consulting report with explicit business impact and ROI direction.",
    }

    payload = {
        "model": LLM_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=True)},
        ],
    }

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://attikonlab.uk",
        "X-Title": "Attikon Lab Nai One",
    }

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT, verify=TLS_VERIFY) as client:
            res = await client.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload)
            res.raise_for_status()
            data = res.json()
    except Exception:
        return None

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = parse_json_from_text(str(content))
    if not parsed:
        return None

    llm_score = (
        (parsed.get("scoreBreakdown") or {}).get("overall")
        if isinstance(parsed.get("scoreBreakdown"), dict)
        else None
    )
    try:
        llm_score = int(llm_score)
    except (TypeError, ValueError):
        llm_score = heuristic_report.get("score", 0)

    llm_score = max(0, min(100, llm_score))

    leakage = normalize_to_list(parsed.get("operationalFriction"))
    quick_wins = normalize_to_list(parsed.get("quickWins"))
    roadmap = normalize_to_list(parsed.get("implementationRoadmap"))
    opportunities = normalize_to_list(parsed.get("automationOpportunities"))
    revenue_expansion = normalize_to_list(parsed.get("revenueExpansionOpportunities"))
    strategic_insight = str(parsed.get("strategicInsight") or "").strip()
    score_breakdown = parsed.get("scoreBreakdown") if isinstance(parsed.get("scoreBreakdown"), dict) else {}
    op_model = parsed.get("businessOperationalModel") if isinstance(parsed.get("businessOperationalModel"), dict) else {}

    return {
        "score": llm_score,
        "summary": str(parsed.get("executiveSummary") or heuristic_report.get("summary") or "").strip(),
        "findings": leakage[:8] or heuristic_report.get("findings", []),
        "quickWins": quick_wins[:6] or heuristic_report.get("quickWins", []),
        "roadmap": roadmap[:6],
        "opportunities": opportunities[:8],
        "strategicInsight": strategic_insight,
        "revenueExpansion": revenue_expansion[:6],
        "scoreBreakdown": score_breakdown,
        "operationalModel": op_model,
        "analysisType": "surface-html-heuristic-v2+llm",
        "llmModel": LLM_MODEL,
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
    visible_text = extract_visible_text(html)
    llm_report = await generate_llm_report(normalized, report, visible_text)
    if llm_report:
        report = llm_report
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
