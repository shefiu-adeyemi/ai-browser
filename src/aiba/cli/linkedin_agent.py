"""
LinkedIn AI Agent — persistent login session + menu of tasks.
Saves your browser session in its own profile folder (%LOCALAPPDATA%\\AIBrowserAgent\\profiles\\linkedin)
so you only log in once. The web UI keeps a separate profile; the two can't share one because
Chromium locks a profile to a single running browser.

Posting and sending are never automatic: every such click goes through the approval guard
(agent/action_guard.py), and "draft a post" stops before the Post button.

Usage:
  python cli/linkedin_agent.py
"""

import asyncio
import re
from aiba.storage import env as env_store
from aiba.storage import paths as app_paths
from aiba.agent.action_guard import GuardedTools, SAFETY_PROMPT
from aiba.server.approvals import ConsoleApprover
from browser_use import Agent
from browser_use.browser.session import BrowserSession
from aiba.llm.config import get_llm
from aiba.storage.credentials import linkedin_sensitive_data, linkedin_login_instructions


PROFILE_DIR = app_paths.LINKEDIN_PROFILE_DIR


# Only real member profiles: the old check accepted any linkedin.com path (feed, settings, messaging...).
PROFILE_URL = re.compile(r"https://www\.linkedin\.com/in/[A-Za-z0-9][A-Za-z0-9\-_%.]*/?")

login_prefix = linkedin_login_instructions
build_sensitive_data = linkedin_sensitive_data


# ---------------------------------------------------------------------------
# Task builders
# ---------------------------------------------------------------------------
def task_search_jobs(keyword: str, location: str) -> str:
    return (
        f"{login_prefix()}"
        f"Go to https://www.linkedin.com/jobs/. "
        f"Search for {keyword} jobs in {location}. "
        "Return the first 10 job titles, company names, and locations."
    )


def task_search_people(title: str, location: str) -> str:
    return (
        f"{login_prefix()}"
        f"Go to https://www.linkedin.com/search/results/people/ and search for "
        f"people with title {title} in {location}. "
        "Return the name, headline, and profile URL of the first 5 results."
    )


def task_send_connection(profile_url: str, note: str) -> str:
    if not PROFILE_URL.fullmatch(profile_url.strip()):
        raise ValueError(
            f"URL must be a profile like https://www.linkedin.com/in/<name>/ — got: '{profile_url}'"
        )
    profile_url = profile_url.strip()
    return (
        f"{login_prefix()}"
        f"Go to {profile_url}. "
        f"Click 'Connect' and add a personalised note with this text: {note}\n"
        "Click 'Send'. Confirm success or report any issue."
    )


def task_check_notifications() -> str:
    return (
        f"{login_prefix()}"
        "Go to https://www.linkedin.com/notifications/. "
        "Summarise the 5 most recent notifications."
    )


def task_draft_post(text: str) -> str:
    return (
        f"{login_prefix()}"
        "Go to https://www.linkedin.com/feed/. "
        "Click 'Start a post'. "
        f"Type exactly this text: {text}\n"
        "Do NOT click post — just confirm it is typed and ready, then stop."
    )


def task_research_company(handle: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9\-]+", handle):
        raise ValueError(
            f"Invalid company handle '{handle}': only letters, numbers, and hyphens allowed"
        )
    return (
        f"{login_prefix()}"
        f"Go to https://www.linkedin.com/company/{handle}/. "
        "Extract: company description, employee count, industry, "
        "and the text of the 3 most recent posts."
    )


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
def _ask(prompt: str, default: str = "") -> str:
    val = input(prompt).strip()
    if not val and default:
        return default
    if not val:
        raise ValueError(f"Input required for: {prompt.strip()}")
    return val


MENU = {
    "1": ("Search jobs",         lambda: task_search_jobs(
                                    _ask("  Job keyword: ", "Python developer"),
                                    _ask("  Location: ", "Nigeria"))),
    "2": ("Search people",       lambda: task_search_people(
                                    _ask("  Title: ", "AI Engineer"),
                                    _ask("  Location: ", "Nigeria"))),
    "3": ("Send connection",     lambda: task_send_connection(
                                    _ask("  LinkedIn profile URL (https://www.linkedin.com/in/...): "),
                                    _ask("  Connection note: "))),
    "4": ("Check notifications", lambda: task_check_notifications()),
    "5": ("Draft a post",        lambda: task_draft_post(
                                    _ask("  Post text: "))),
    "6": ("Research a company",  lambda: task_research_company(
                                    _ask("  Company handle (e.g. openai): "))),
    "7": ("Custom task",         lambda: _ask("  Describe your task: ")),
}


async def main():
    app_paths.migrate_legacy()
    env_store.apply_to_environ(app_paths.ENV_PATH)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    llm = get_llm()

    print("\n=== LinkedIn AI Agent (browser-use 0.13 + AI) ===\n")
    for key, (label, _) in MENU.items():
        print(f"  {key}. {label}")

    choice = input("\nChoose (1-7): ").strip()
    if choice not in MENU:
        print("Invalid choice.")
        return

    _, task_fn = MENU[choice]
    try:
        task = task_fn()
    except ValueError as e:
        print(f"[Error] {e}")
        return

    session = BrowserSession(headless=False, user_data_dir=str(PROFILE_DIR))
    try:
        print("\n[Agent] Starting...\n" + "-" * 60)
        sensitive = build_sensitive_data()
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            sensitive_data=sensitive,
            tools=GuardedTools(ConsoleApprover(), task=task,
                               secret_names={k for v in (sensitive or {}).values() for k in v}),
            extend_system_message=SAFETY_PROMPT,
            max_actions_per_step=5,
        )
        history = await agent.run(max_steps=40)
        result = history.final_result()
        print("\n[Agent] Done. Result:")
        print(result if result is not None else "(no final result returned)")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
