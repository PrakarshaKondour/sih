"""
Mock public alert service (spec section 10). Generates alert *content*
only — never sends a real SMS/IVR unless SMS_PROVIDER_API_KEY and
IVR_PROVIDER_API_KEY are set in the environment, which they are not in
this repo (no keys are hardcoded, per spec). Cell Broadcast is documented
as a future production integration, not implemented here.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

SMS_CONFIGURED = bool(os.environ.get("SMS_PROVIDER_API_KEY"))
IVR_CONFIGURED = bool(os.environ.get("IVR_PROVIDER_API_KEY"))
WEBHOOK_URL = os.environ.get("HYDROLOOP_ALERT_WEBHOOK_URL")


@dataclass
class AlertContent:
    locality: str
    expected_depth_m: float
    expected_time_minute: int
    severity: str
    alt_route_summary: str


TEMPLATES = {
    "en": ("HYDROLOOP ALERT: {severity} flood risk expected in {locality} in "
           "~{expected_time_minute} min (depth ~{expected_depth_m}m). "
           "Avoid the area. Alt route: {alt_route_summary}."),
    "te": ("HYDROLOOP హెచ్చరిక: {locality}లో సుమారు {expected_time_minute} "
           "నిమిషాల్లో {severity} స్థాయి వరద ప్రమాదం (లోతు ~{expected_depth_m}మీ). "
           "ఆ ప్రాంతాన్ని నివారించండి. ప్రత్యామ్నాయ మార్గం: {alt_route_summary}."),
}
TELUGU_SEVERITY = {
    "LOW": "తక్కువ",
    "MODERATE": "మధ్యస్థ",
    "HIGH": "అధిక",
    "CRITICAL": "తీవ్ర",
}


def render_alert(content: AlertContent, lang: str = "en") -> str:
    template = TEMPLATES.get(lang, TEMPLATES["en"])
    values = content.__dict__.copy()
    if lang == "te":
        values["severity"] = TELUGU_SEVERITY.get(content.severity.upper(), content.severity)
    return template.format(**values)


def dispatch_alert(content: AlertContent, channel: str = "sms", langs: tuple[str, ...] = ("en", "te")) -> dict:
    """Render bilingual alerts and optionally POST them to a prototype webhook."""
    messages = {lang: render_alert(content, lang) for lang in langs}
    if channel == "webhook":
        if not WEBHOOK_URL:
            return {"channel": channel, "messages": messages, "dispatched": False,
                    "mode": "MOCK (no webhook configured)"}
        try:
            import requests
            response = requests.post(
                WEBHOOK_URL,
                json={"messages": messages, "locality": content.locality,
                      "severity": content.severity,
                      "expected_time_minute": content.expected_time_minute},
                timeout=5,
            )
            response.raise_for_status()
            return {"channel": channel, "messages": messages, "dispatched": True,
                    "mode": "WEBHOOK", "status_code": response.status_code}
        except Exception as exc:
            return {"channel": channel, "messages": messages, "dispatched": False,
                    "mode": "WEBHOOK_FAILED", "error": str(exc)}
    configured = SMS_CONFIGURED if channel == "sms" else IVR_CONFIGURED
    return {
        "channel": channel,
        "messages": messages,
        "dispatched": False if not configured else True,
        "mode": "LIVE" if configured else "MOCK (no provider credentials configured)",
    }
