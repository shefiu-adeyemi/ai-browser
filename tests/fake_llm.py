"""
A scripted stand-in for a real LLM, so the real Agent can be driven end to end in tests
without spending API credits. Each call to ainvoke() pops the next scripted step.

A step is one of:
  {"navigate": "http://..."}         open a URL
  {"wait": 5}                        wait N seconds (use with stop/cancel tests)
  {"click": 3} / {"input": (3,"x")}  element actions by index
  {"click": "send"} / {"input": ("q","x")}  ...or by the element's id attribute: browser-use numbers
                                     elements by DOM node, so the number changes between pages and runs
  {"done": "final answer"}           finish the task
  {"raise": "message"}               the LLM call itself fails (API down, bad key ...)
  {"hang": True}                     never answers (a stuck request)
"""

import asyncio
import re
from typing import Any

from browser_use.llm.views import ChatInvokeCompletion


def _action(step: dict) -> dict:
    if "navigate" in step:
        return {"navigate": {"url": step["navigate"], "new_tab": False}}
    if "wait" in step:
        return {"wait": {"seconds": step["wait"]}}
    if "click" in step:
        return {"click": {"index": step["click"]}}
    if "input" in step:
        i, text = step["input"]
        return {"input": {"index": i, "text": text, "clear": True}}
    if "done" in step:
        return {"done": {"text": step["done"], "success": step.get("success", True)}}
    raise ValueError(f"unknown scripted step: {step}")


def _index_of(element_id: str, prompt: str) -> int:
    """The number browser-use gave the element with this id attribute, read from the page listing in
    the prompt (the LAST match: the system prompt has a few made-up examples before the real page)."""
    pattern = r"\[(\d+)\]<[^>" + chr(10) + r"]*?\bid=" + re.escape(element_id) + r"\b"
    found = re.findall(pattern, prompt)
    if not found:
        raise ValueError(f"no element with id={element_id!r} in the page the model was shown")
    return int(found[-1])


def _resolve_ids(step: dict, prompt: str) -> dict:
    step = dict(step)
    if isinstance(step.get("click"), str):
        step["click"] = _index_of(step["click"], prompt)
    if isinstance(step.get("input"), (tuple, list)) and isinstance(step["input"][0], str):
        step["input"] = (_index_of(step["input"][0], prompt), step["input"][1])
    return step


def _message_text(message: Any) -> str:
    """The FULL text of a message. (str() of a message with several content parts uses pydantic's
    repr, which truncates long strings with '...' and would hide most of the prompt.)"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return chr(10).join(p if isinstance(p, str) else (getattr(p, "text", "") or "") for p in content)
    return str(content)


class FakeLLM:
    model = "fake-llm"
    _verified_api_keys = True

    def __init__(self, steps: list[dict], judge: list[dict] | None = None):
        self.steps = list(steps)
        # Verdicts for browser-use's judge, consumed in order; when empty the judge passes.
        # e.g. {"verdict": False, "failure_reason": "missed the price", "reasoning": "..."}
        self.judge = list(judge or [])
        self.calls = 0              # calls that asked for the next agent step
        self.judge_calls = 0
        self.prompts: list[str] = []   # the full prompt of every agent step (what it remembered)
        self.first_prompt = ""      # everything the model was shown on its first step

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return "fake-llm"

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(self, messages: list, output_format: Any = None, **kwargs: Any):
        if output_format is None:                       # plain-text calls
            return ChatInvokeCompletion(completion="ok", usage=None)
        if getattr(output_format, "__name__", "") == "JudgementResult":     # browser-use's judge
            self.judge_calls += 1
            verdict = self.judge.pop(0) if self.judge else {"verdict": True, "reasoning": "fake judge: ok"}
            if "raise" in verdict:                      # the judge's own call fails (API error ...)
                raise RuntimeError(verdict["raise"])
            return ChatInvokeCompletion(completion=output_format.model_validate(verdict), usage=None)
        self.calls += 1
        prompt = chr(10).join(_message_text(m) for m in messages)
        self.prompts.append(prompt)
        if self.calls == 1:
            self.first_prompt = prompt
        step = self.steps.pop(0) if self.steps else {"done": "(script exhausted)"}
        if "raise" in step:
            raise RuntimeError(step["raise"])
        if "hang" in step:
            await asyncio.sleep(3600)
        step = _resolve_ids(step, prompt)
        payload = {
            "thinking": "scripted",
            "evaluation_previous_goal": "n/a",
            "memory": "scripted",
            "next_goal": "scripted",
            "action": [_action(step)],
        }
        return ChatInvokeCompletion(completion=output_format.model_validate(payload), usage=None)
