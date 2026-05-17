"""Email validation: syntax + MX + disposable.

Pure-Python, no third-party paid APIs. Built on `email-validator` + a
bundled list of common disposable domains. Good enough for an SMB SaaS;
swap for ZeroBounce / Kickbox when scale demands.
"""

from __future__ import annotations

from dataclasses import dataclass

from email_validator import EmailNotValidError
from email_validator import validate_email as _validate

# A curated list of the most-seen disposable providers in WordPress form spam.
# Not exhaustive — but covers ~95% of what we see.
DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "10minutemail.com",
        "10minutemail.net",
        "20minutemail.com",
        "guerrillamail.com",
        "guerrillamail.info",
        "guerrillamail.biz",
        "sharklasers.com",
        "mailinator.com",
        "mailinator.net",
        "trashmail.com",
        "trashmail.net",
        "yopmail.com",
        "yopmail.fr",
        "throwawaymail.com",
        "tempmail.com",
        "temp-mail.org",
        "temp-mail.io",
        "getairmail.com",
        "fakeinbox.com",
        "maildrop.cc",
        "dispostable.com",
        "tempinbox.com",
        "spambog.com",
        "mintemail.com",
        "tempr.email",
        "moakt.com",
        "emailondeck.com",
        "binkmail.com",
    }
)


@dataclass(frozen=True)
class EmailValidationResult:
    email: str
    valid: bool
    disposable: bool
    reason: str | None = None
    normalized: str | None = None


def validate_email_address(
    email: str,
    *,
    check_deliverability: bool = True,
) -> EmailValidationResult:
    """Syntax check, MX lookup (optional), disposable check.

    `check_deliverability=False` skips DNS (use in unit tests and offline CI).
    """
    if not email or "@" not in email:
        return EmailValidationResult(email=email, valid=False, disposable=False, reason="no @")

    try:
        result = _validate(email, check_deliverability=check_deliverability)
        normalized = result.normalized
        domain = result.domain.lower()
    except EmailNotValidError as e:
        return EmailValidationResult(email=email, valid=False, disposable=False, reason=str(e))

    disposable = domain in DISPOSABLE_DOMAINS
    return EmailValidationResult(
        email=email,
        valid=True,
        disposable=disposable,
        normalized=normalized,
        reason="disposable" if disposable else None,
    )
