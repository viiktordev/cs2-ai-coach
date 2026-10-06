import unittest

from cs2_coach.context import build_context
from cs2_coach.events import EventTracker


class ContextTest(unittest.TestCase):
    def test_context_uses_confirmed_values_and_team_relative_advantage(self):
        tracker = EventTracker(2)
        readings = {"ct_score": 3, "t_score": 4, "ct_alive": 1, "t_alive": 2, "side": "T", "health": 100}
        tracker.update(readings, 0, 0, {})
        self.assertIsNone(build_context(tracker.observations(0))["side"])
        tracker.update(readings, 1, 500, {})
        context = build_context(tracker.observations(500))
        self.assertEqual(context["round_number_estimate"], 8)
        self.assertEqual(context["alive_advantage"], 1)
        self.assertEqual(context["players_alive"], {"CT": 1, "T": 2})

    def test_zero_missing_and_stale_are_different(self):
        tracker = EventTracker(1)
        tracker.update({"ct_alive": 0, "health": 100}, 0, 0, {})
        self.assertEqual(build_context(tracker.observations(0))["players_alive"]["CT"], 0)
        tracker.update({"ct_alive": None, "health": None}, 1, 500, {})
        context = build_context(tracker.observations(500))
        self.assertIsNone(context["players_alive"]["CT"])
        self.assertEqual(context["observations"]["health"]["last_confirmed_value"], 100)
        self.assertEqual(context["observations"]["health"]["status"], "unavailable")
        tracker.update({"health": 100}, 2, 1000, {})
        self.assertEqual(tracker.observations(4000)["health"]["status"], "stale")

    def test_confirmation_preserves_first_observation_time(self):
        tracker = EventTracker(3)
        tracker.update({"health": 100}, 4, 1200, {})
        tracker.update({"health": 100}, 5, 1500, {})
        event = tracker.update({"health": 100}, 6, 1800, {})[0]
        self.assertEqual(event["first_observed_ms"], 1200)
        self.assertEqual(event["timestamp_ms"], 1800)
        self.assertEqual(event["first_frame_id"], 4)

    def test_unconfirmed_new_value_is_not_presented_as_current(self):
        tracker = EventTracker(2)
        tracker.update({"health": 100}, 0, 0, {})
        tracker.update({"health": 100}, 1, 500, {})
        tracker.update({"health": 80}, 2, 1000, {})
        observation = tracker.observations(1000)["health"]
        self.assertEqual(observation["status"], "pending")
        self.assertIsNone(observation["value"])
        self.assertEqual(observation["last_confirmed_value"], 100)
