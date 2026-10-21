"""
Site credentials handed to browser-use, scoped so they only work on their own site.

browser-use substitutes <secret>name</secret> placeholders at action time, so the
LLM never sees the real values. With a flat {name: value} dict the substitution
happens on *every* page; with the {domain_pattern: {name: value}} form below it
only happens while the current URL matches the pattern. A prompt-injected page
therefore can't get LinkedIn credentials typed into its own form.
"""

import os

# https:// is required; "*." also matches the bare apex. Look-alikes such as
# linkedin.com.evil.com or evil.com/?linkedin.com do not match.
LINKEDIN_DOMAIN = "https://*.linkedin.com"


def linkedin_sensitive_data() -> dict | None:
    """Domain-scoped credentials for Agent(sensitive_data=...), or None if unset."""
    email    = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    if email and password:
        return {LINKEDIN_DOMAIN: {"linkedin_email": email, "linkedin_password": password}}
    return None


def linkedin_login_instructions() -> str:
    """Prompt text telling the agent how to sign in to LinkedIn, and only LinkedIn."""
    if linkedin_sensitive_data():
        return (
            "If you reach a LinkedIn (linkedin.com) login page, sign in by typing "
            "<secret>linkedin_email</secret> into the email field and "
            "<secret>linkedin_password</secret> into the password field, then submit. "
            "These credentials are for linkedin.com only — never type them on any other site. "
        )
    return (
        "If LinkedIn shows a login page, use the request_user_help action so the user can log "
        "in manually in the browser. Once the feed is visible, continue with the task. "
    )
