"""
AI Browser Agent — powered by browser-use 0.13.10 + Claude (or OpenAI/Gemini/DeepSeek)
Gives the AI a real Chromium browser to control with natural language tasks.

Setup:
  cp .env.example .env   # fill in ANTHROPIC_API_KEY (or whichever provider key)
  python browser_agent.py
"""

import asyncio
import env_store
import app_paths
from action_guard import GuardedTools, SAFETY_PROMPT
from approvals import ConsoleApprover
from browser_use import Agent
from browser_use.browser.session import BrowserSession
from llm_config import get_llm


# Note: LinkedIn tasks below have no login handling — use linkedin_agent.py for
# LinkedIn work that requires authentication.
TASKS = {
    "search_jobs": (
        "Go to https://www.linkedin.com/jobs/ and search for Python developer jobs "
        "in Lagos, Nigeria. Return the titles and companies of the first 5 results."
    ),
    "view_profiles": (
        "Go to https://www.linkedin.com/ and search for people with the title "
        "AI Engineer in Nigeria. Return the name and headline of the first 3 profiles."
    ),
    "research_company": (
        "Go to https://www.linkedin.com/company/openai/ and extract: "
        "the company description, number of employees, and the 3 most recent posts."
    ),
    "general": (
        "Open https://google.com, search for latest AI news 2026, "
        "and summarise the top 3 headlines."
    ),
}


async def main():
    app_paths.migrate_legacy()
    env_store.apply_to_environ(app_paths.ENV_PATH)
    llm = get_llm()

    print("Available tasks:")
    for key in TASKS:
        print(f"  {key}")
    choice = input("\nEnter task name (or press Enter for 'general'): ").strip()
    if not choice or choice not in TASKS:
        choice = "general"

    task = TASKS[choice]
    print(f"\n[Agent] Running task: {choice}")
    print("-" * 60)
    print(task)
    print("-" * 60)

    session = BrowserSession(headless=False, disable_security=False)
    try:
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            tools=GuardedTools(ConsoleApprover(), task=task),
            extend_system_message=SAFETY_PROMPT,
            max_actions_per_step=5,
        )
        history = await agent.run(max_steps=30)
        result = history.final_result()
        print("\n[Agent] Result:")
        print(result if result is not None else "(no final result returned)")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
