"""DeepSeek's most common slip (sending one action's arguments without the wrapper) is put right.
The shapes below are the real ones from a failed LinkedIn run (agent_error.log).  No browser, no network.
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from browser_use.agent.views import AgentOutput

from aiba.llm import deepseek as deepseek_chat
from aiba.agent.action_guard import GuardedTools
from pydantic import ValidationError

_ACTIONS = GuardedTools(approver=None).registry.create_action_model()
CAREFUL = AgentOutput.type_with_custom_actions(_ACTIONS)
FLASH = AgentOutput.type_with_custom_actions_flash_mode(_ACTIONS)


def action_of(output) -> dict:
    return output.action[0].model_dump(exclude_unset=True)


class RepairsTheWrapper(unittest.TestCase):
    def test_the_shapes_from_the_real_run(self):
        cases = [
            ({"url": "https://www.linkedin.com/in/jane-doe/", "new_tab": False},
             {"navigate": {"url": "https://www.linkedin.com/in/jane-doe/", "new_tab": False}}),
            ({"query": "site:linkedin.com/in Jane Doe Acme Advisory", "engine": "duckduckgo"},
             {"search": {"query": "site:linkedin.com/in Jane Doe Acme Advisory", "engine": "duckduckgo"}}),
            ({"file_name": "todo.md", "content": "# Task\n- read profile"},
             {"write_file": {"file_name": "todo.md", "content": "# Task\n- read profile"}}),
        ]
        for model in (CAREFUL, FLASH):
            for sent, want in cases:
                with self.subTest(model=model.__name__, sent=list(sent)):
                    out = model.model_validate(deepseek_chat.repair_output(sent, model))
                    self.assertEqual(action_of(out), want)

    def test_other_parts_of_the_answer_are_kept(self):
        sent = {"memory": "on the profile page", "url": "https://example.com", "new_tab": True}
        out = CAREFUL.model_validate(deepseek_chat.repair_output(sent, CAREFUL))
        self.assertEqual(out.memory, "on the profile page")
        self.assertEqual(action_of(out), {"navigate": {"url": "https://example.com", "new_tab": True}})

    def test_one_action_sent_without_the_list_is_wrapped(self):
        sent = {"memory": "m", "action": {"navigate": {"url": "https://example.com", "new_tab": False}}}
        out = CAREFUL.model_validate(deepseek_chat.repair_output(sent, CAREFUL))
        self.assertEqual(action_of(out), {"navigate": {"url": "https://example.com", "new_tab": False}})

    def test_a_correct_answer_is_left_exactly_alone(self):
        good = {"memory": "m", "action": [{"navigate": {"url": "https://example.com", "new_tab": False}}]}
        self.assertEqual(deepseek_chat.repair_output(good, CAREFUL), good)


class DoesNotGuess(unittest.TestCase):
    def test_arguments_no_action_takes_are_left_to_fail(self):
        sent = {"url": "https://example.com", "colour": "red"}
        self.assertEqual(deepseek_chat.repair_output(sent, CAREFUL), sent)
        with self.assertRaises(ValidationError):
            CAREFUL.model_validate(deepseek_chat.repair_output(sent, CAREFUL))

    def test_missing_a_required_argument_is_left_to_fail(self):
        sent = {"new_tab": True}                       # navigate needs a url
        self.assertEqual(deepseek_chat.repair_output(sent, CAREFUL), sent)

    def test_arguments_that_fit_several_actions_are_not_guessed(self):
        for sent in ({"index": 3}, {"query": "x"}, {"text": "hi", "index": 2}):      # click/input..., search/extract, input/dropdown
            self.assertEqual(deepseek_chat.repair_output(sent, CAREFUL), sent, sent)

    def test_arguments_only_one_action_takes_are_placed(self):
        out = CAREFUL.model_validate(deepseek_chat.repair_output({"query": "names", "already_collected": []}, CAREFUL))
        self.assertEqual(list(action_of(out)), ["extract"])
        out = CAREFUL.model_validate(deepseek_chat.repair_output({"pattern": "Doe", "max_results": 25}, CAREFUL))
        self.assertEqual(list(action_of(out)), ["search_page"])

    def test_not_a_dict_is_untouched(self):
        self.assertEqual(deepseek_chat.repair_output("text", CAREFUL), "text")
        self.assertEqual(deepseek_chat.repair_output(None, CAREFUL), None)


class ThroughTheClient(unittest.IsolatedAsyncioTestCase):
    """The whole path: the model's function call comes back flat and the step still succeeds."""

    async def test_a_flat_answer_becomes_a_valid_step(self):
        flat = json.dumps({"url": "https://www.linkedin.com/search/results/people/", "new_tab": False})
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments=flat))]))])
        create = mock.AsyncMock(return_value=reply)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm = deepseek_chat.RepairingDeepSeek(model="deepseek-v4-pro", api_key="sk-test-123456")
        with mock.patch.object(deepseek_chat.RepairingDeepSeek, "_client", return_value=client):
            result = await llm.ainvoke([], output_format=CAREFUL)
        self.assertIsInstance(result.completion, CAREFUL)
        self.assertEqual(action_of(result.completion)["navigate"]["url"], "https://www.linkedin.com/search/results/people/")
        sent_tool = create.call_args.kwargs["tools"][0]["function"]
        self.assertEqual(sent_tool["name"], "AgentOutput")          # the model is still asked for the same thing

    async def test_a_text_call_is_untouched(self):
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hello", tool_calls=None))])
        create = mock.AsyncMock(return_value=reply)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm = deepseek_chat.RepairingDeepSeek(model="deepseek-v4-pro", api_key="sk-test-123456")
        with mock.patch.object(deepseek_chat.RepairingDeepSeek, "_client", return_value=client):
            result = await llm.ainvoke([])
        self.assertEqual(result.completion, "hello")


class BuiltForTheApp(unittest.TestCase):
    def test_deepseek_uses_it(self):
        from aiba.llm import config as llm_config
        with mock.patch.dict(_os.environ, {"DEEPSEEK_API_KEY": "sk-test-123456"}):
            self.assertIsInstance(llm_config.build_llm("deepseek"), deepseek_chat.RepairingDeepSeek)


if __name__ == "__main__":
    unittest.main()
