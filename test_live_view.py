"""Unit tests for live_view.LiveView using a fake CDP client (no browser).  python -m unittest -v"""
import asyncio
import unittest
from types import SimpleNamespace

from live_view import LiveView


class FakeClient:
    """Records every CDP call LiveView makes."""
    def __init__(self):
        self.calls = []
        rec = lambda name: (lambda params=None, session_id=None: self._rec(name, params, session_id))
        self.send = SimpleNamespace(
            Input=SimpleNamespace(dispatchMouseEvent=rec("mouse"), dispatchKeyEvent=rec("key")),
            Page=SimpleNamespace(screencastFrameAck=rec("ack")),
        )

    async def _rec(self, name, params, sid):
        self.calls.append((name, params, sid))


def make(broadcast=None):
    lv = LiveView(lambda: None, broadcast or (lambda m: asyncio.sleep(0)), lambda: True)
    lv._client, lv._sid = FakeClient(), "SID"
    return lv


def calls(lv, kind):
    return [p for n, p, _ in lv._client.calls if n == kind]


class InputMapping(unittest.IsolatedAsyncioTestCase):
    async def test_click_sets_and_clears_buttons(self):
        lv = make()
        for ev in ("mousePressed", "mouseMoved", "mouseReleased"):
            await lv.dispatch_input({"kind": "mouse", "event": ev, "x": 10, "y": 20, "button": "left"})
        p = calls(lv, "mouse")
        self.assertEqual([c["type"] for c in p], ["mousePressed", "mouseMoved", "mouseReleased"])
        self.assertEqual([c["buttons"] for c in p], [1, 1, 0])   # drag state tracked, then released
        self.assertEqual(p[0]["clickCount"], 1)

    async def test_wheel_carries_deltas(self):
        lv = make()
        await lv.dispatch_input({"kind": "mouse", "event": "mouseWheel", "x": 1, "y": 2, "deltaX": 3, "deltaY": 400})
        self.assertEqual((calls(lv, "mouse")[0]["deltaX"], calls(lv, "mouse")[0]["deltaY"]), (3, 400))

    async def test_enter_sends_char_so_forms_submit(self):
        lv = make()
        await lv.dispatch_input({"kind": "key", "event": "keyDown", "key": "Enter", "keyCode": 13})
        self.assertEqual([c["type"] for c in calls(lv, "key")], ["rawKeyDown", "char"])
        self.assertEqual(calls(lv, "key")[1]["text"], "\r")

    async def test_ctrl_a_is_a_command_not_text(self):
        lv = make()
        await lv.dispatch_input({"kind": "key", "event": "keyDown", "key": "a", "keyCode": 65, "modifiers": 2})
        await lv.dispatch_input({"kind": "key", "event": "char", "key": "a", "text": "a", "modifiers": 2})
        k = calls(lv, "key")
        self.assertEqual(k[0]["commands"], ["selectAll"])
        self.assertEqual(len(k), 1)                              # the stray 'a' char was suppressed

    async def test_plain_char_types(self):
        lv = make()
        await lv.dispatch_input({"kind": "key", "event": "char", "key": "q", "text": "q"})
        self.assertEqual(calls(lv, "key")[0], {"type": "char", "text": "q", "modifiers": 0})

    async def test_garbage_never_raises_or_sends(self):
        lv = make()
        for bad in ({}, {"kind": "zzz"}, {"kind": "mouse", "event": "nope"}, {"kind": "key"},
                    {"kind": "mouse", "event": "mouseMoved", "x": "NaN", "y": None}):
            await lv.dispatch_input(bad)
        self.assertEqual([c for c in lv._client.calls if c[0] == "key"], [])
        moved = calls(lv, "mouse")
        self.assertTrue(all(c["x"] == 0.0 and c["y"] == 0.0 for c in moved))   # sanitised, not NaN

    async def test_no_target_no_send(self):
        lv = make(); lv._sid = None
        await lv.dispatch_input({"kind": "mouse", "event": "mousePressed", "x": 1, "y": 1})
        self.assertEqual(lv._client.calls, [])


class Queueing(unittest.IsolatedAsyncioTestCase):
    async def test_ordered_and_moves_coalesced(self):
        lv = make()
        w = asyncio.create_task(lv._input_worker())
        move = lambda x: {"kind": "mouse", "event": "mouseMoved", "x": x, "y": 0}
        for m in (move(1), move(2), move(3)):
            lv.enqueue_input(m)
        lv.enqueue_input({"kind": "mouse", "event": "mousePressed", "x": 3, "y": 0, "button": "left"})
        lv.enqueue_input({"kind": "mouse", "event": "mouseReleased", "x": 3, "y": 0, "button": "left"})
        await asyncio.sleep(0.05); w.cancel()
        types = [c["type"] for c in calls(lv, "mouse")]
        self.assertEqual(types, ["mouseMoved", "mousePressed", "mouseReleased"])   # order kept, 3 moves -> 1
        self.assertEqual(calls(lv, "mouse")[0]["x"], 3)                            # the latest move survived

    async def test_queue_is_bounded(self):
        lv = make()
        for i in range(1000):
            lv.enqueue_input({"kind": "key", "event": "keyUp", "key": "a"})
        self.assertLessEqual(len(lv._inputs), 256)


class Frames(unittest.IsolatedAsyncioTestCase):
    async def test_frame_becomes_message_and_is_acked(self):
        lv = make()
        lv._on_frame({"data": "QUJD", "sessionId": 7, "metadata": {"deviceWidth": 1920, "deviceHeight": 1080}}, "SID")
        await asyncio.sleep(0)   # let the ack task run
        self.assertEqual(lv._pending, {"type": "screenshot", "data": "QUJD", "w": 1920, "h": 1080})
        self.assertEqual(calls(lv, "ack"), [{"sessionId": 7}])

    async def test_frames_from_other_targets_ignored(self):
        lv = make()
        lv._on_frame({"data": "x", "sessionId": 1, "metadata": {}}, "OTHER")
        self.assertIsNone(lv._pending)

    async def test_newest_frame_wins_and_sender_survives_errors(self):
        got, boom = [], {"n": 0}
        async def bc(m):
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("client blew up")
            got.append(m["data"])
        lv = make(bc)
        s = asyncio.create_task(lv._sender())
        for d in ("a", "b", "c"):       # three frames before the sender runs: only the last is sent
            lv._on_frame({"data": d, "sessionId": 1, "metadata": {}}, "SID")
        await asyncio.sleep(0.1)
        lv._on_frame({"data": "d", "sessionId": 2, "metadata": {}}, "SID")   # after the first send raised
        await asyncio.sleep(0.1); s.cancel()
        self.assertEqual(got, ["d"])     # 'c' hit the exception; sender kept running and delivered 'd'


if __name__ == "__main__":
    unittest.main()
