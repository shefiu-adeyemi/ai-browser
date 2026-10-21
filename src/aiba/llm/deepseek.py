"""
DeepSeek, with its most common slip in the shape of an answer put right before it is checked.

browser-use asks DeepSeek to answer through one function call whose arguments must look like
    {"memory": "...", "action": [{"navigate": {"url": "...", "new_tab": false}}]}
DeepSeek V4 very often sends only the inside of the action instead:
    {"url": "...", "new_tab": false}            or    {"query": "...", "engine": "duckduckgo"}
The library rejects that ("action: Field required, url: Extra inputs are not permitted"), the step is wasted,
and five wasted steps in a row stop the whole run. In a real LinkedIn run about nine of eleven steps were lost
this way, and every one of them said exactly what it meant to do.

repair_output() puts the missing wrapper back, but ONLY when the arguments belong to exactly one action, so
it never guesses between two (arguments like just `index` or `query` fit several). Anything it cannot place is left alone and fails as before.
"""

import typing
from functools import lru_cache
from typing import Any, Optional

from browser_use.llm.deepseek.chat import ChatDeepSeek


def _args_of(annotation: Any) -> tuple:
    return typing.get_args(annotation)


@lru_cache(maxsize=16)
def _actions(model: type) -> dict[str, tuple[frozenset, frozenset]]:
    """{action name: (every argument it takes, the arguments it requires)} for an AgentOutput class."""
    field = model.model_fields.get("action")
    if field is None:
        return {}
    items = _args_of(field.annotation)
    if not items:
        return {}
    root = getattr(items[0], "model_fields", {}).get("root")
    variants = _args_of(root.annotation) if root is not None else (items[0],)
    found: dict[str, tuple[frozenset, frozenset]] = {}
    for variant in variants:
        for name, f in getattr(variant, "model_fields", {}).items():
            params = f.annotation if hasattr(f.annotation, "model_fields") else next(
                (a for a in _args_of(f.annotation) if hasattr(a, "model_fields")), None)
            if params is None:
                continue
            found[name] = (frozenset(params.model_fields),
                           frozenset(k for k, v in params.model_fields.items() if v.is_required()))
    return found


def repair_output(obj: Any, model: type) -> Any:
    """The answer, with a bare action's arguments wrapped as {"action": [{name: arguments}]}. Unchanged if it can't tell."""
    if not isinstance(obj, dict):
        return obj
    top = set(getattr(model, "model_fields", {}))
    if isinstance(obj.get("action"), dict):                    # one action sent instead of a list of one
        return {**obj, "action": [obj["action"]]}
    if "action" in obj:
        return obj
    leftover = {k: v for k, v in obj.items() if k not in top}
    if not leftover:
        return obj
    keys = set(leftover)
    fits = [(name, params) for name, params in _actions(model).items()
            if keys <= params[0] and params[1] <= keys]
    if not fits:
        return obj
    if len(fits) != 1:
        return obj                                              # two or more actions could take these: don't guess
    kept = {k: v for k, v in obj.items() if k in top}
    return {**kept, "action": [{fits[0][0]: leftover}]}


def _repairing(output_format: type) -> type:
    """A twin of the output class (same name and schema) whose model_validate repairs the answer first."""
    def model_validate(cls, obj, *args, **kwargs):
        return output_format.model_validate(repair_output(obj, output_format), *args, **kwargs)
    return type(output_format.__name__, (output_format,), {"model_validate": classmethod(model_validate),
                                                           "__module__": output_format.__module__})


class RepairingDeepSeek(ChatDeepSeek):
    async def ainvoke(self, messages, output_format: Optional[type] = None, tools=None, stop=None, **kwargs):
        if output_format is not None and "action" in getattr(output_format, "model_fields", {}):
            output_format = _repairing(output_format)
        return await super().ainvoke(messages, output_format, tools=tools, stop=stop, **kwargs)
