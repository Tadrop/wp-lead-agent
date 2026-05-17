"""Light lead enrichment.

The goal is "useful context for the auto-response", NOT building a profile
service. We:

  * extract the email domain
  * guess `company` from the domain if missing
  * tag personal-email domains (gmail, yahoo, ...) so the drafter knows
    not to assume the user is at a company

For real web research you'd add an Apollo / Clearbit / Hunter call here.
That's intentionally a single function call so it can be swapped without
touching the worker.
"""

from __future__ import annotations

from typing import Any

from ..models import Lead

PERSONAL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "icloud.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "me.com",
        "mac.com",
    }
)


def _company_from_domain(domain: str) -> str | None:
    # "acme-corp.com" → "Acme Corp"
    if not domain or domain in PERSONAL_DOMAINS:
        return None
    root = domain.split(".")[0]
    if not root:
        return None
    words = root.replace("-", " ").replace("_", " ").split()
    return " ".join(w.capitalize() for w in words) or None


def enrich_lead(lead: Lead) -> dict[str, Any]:
    """Return an enrichment dict that the worker can merge onto `lead.enrichment`."""
    domain = str(lead.email).split("@", 1)[-1].lower()
    info: dict[str, Any] = {
        "email_domain": domain,
        "is_personal_email": domain in PERSONAL_DOMAINS,
    }
    if not lead.company:
        guessed = _company_from_domain(domain)
        if guessed:
            info["company_guess"] = guessed
    return info
