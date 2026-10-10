"""Runtime configuration read from the environment (no secrets or personal data in code)."""
from __future__ import annotations

import os

from ajp import __version__

# Shown in the User-Agent until the operator sets AJP_CONTACT_EMAIL. ".invalid" can never resolve.
PLACEHOLDER_CONTACT = "contact-not-configured@example.invalid"


def contact_email() -> str:
    return os.environ.get("AJP_CONTACT_EMAIL") or PLACEHOLDER_CONTACT


def user_agent(component: str = "ajp-research-bot") -> str:
    """Browser-compatible prefix (some Serbian hosts reject bare library UAs) that still
    names the project, its purpose and a contact (D5)."""
    return (f"Mozilla/5.0 (compatible; {component}/{__version__}; "
            f"non-commercial academic job aggregator; contact: {contact_email()})")
