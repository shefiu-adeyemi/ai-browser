"""
Ready-made playbooks, so a new user has something to run before they have recorded anything.

A template is built here, saved UNAPPROVED, and opened in the editor: the user sees every step and
approves it themselves, the same as for a recording. Nothing in a template is secret or personal.
"""

from typing import Callable

import playbook as pbk
from playbook import Check, Playbook, Source, Step, Variable


def _linkedin_profile() -> Playbook:
    return Playbook(
        name="Open a LinkedIn profile",
        description="Opens the LinkedIn profile you give it. Needs you to be signed in to LinkedIn in this browser.",
        variables=[Variable(name="profile_url", type="linkedin_url", label="Profile address",
                            description="For example https://www.linkedin.com/in/some-name")],
        steps=[Step(id="s1", kind="open", url="{{profile_url}}", check=Check(kind="url_host", hosts=["www.linkedin.com"]),
                    note="Open the profile")],
        source=Source(kind="manual", task="Template: open a LinkedIn profile"))


def _web_search() -> Playbook:
    return Playbook(
        name="Search the web",
        description="Searches DuckDuckGo for what you give it.",
        variables=[Variable(name="term", type="text", label="Search for")],
        steps=[Step(id="s1", kind="search", engine="duckduckgo", query="{{term}}", note="Search")],
        source=Source(kind="manual", task="Template: search the web"))


_TEMPLATES: dict[str, tuple[str, str, Callable[[], Playbook]]] = {
    "linkedin_profile": ("Open a LinkedIn profile", "One step, no AI. The inputs are the profile addresses.", _linkedin_profile),
    "web_search": ("Search the web", "One step, no AI. The input is what to search for.", _web_search),
}


def list_templates() -> list[dict]:
    return [{"id": tid, "name": name, "description": desc} for tid, (name, desc, _f) in _TEMPLATES.items()]


def build(template_id: str) -> Playbook:
    """A fresh, valid, unapproved playbook for this template. KeyError for an unknown id."""
    pb = pbk.refresh(_TEMPLATES[template_id][2]())
    problems = pbk.validate(pb)
    if problems:                                         # a template that fails its own rules is a bug, not user input
        raise RuntimeError(f"template {template_id!r} is invalid: {problems}")
    return pb
