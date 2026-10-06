import unittest
from cs2_coach.events import EventTracker
from cs2_coach.vision import parse_reading, validate_config


class EventsTest(unittest.TestCase):
    def test_confirmation_missing_and_change(self):
        tracker = EventTracker(2)
        def update(value):
            return tracker.update({"health": value}, 0, 0, {"kind": "video"})
        self.assertEqual(update(100), [])
        self.assertEqual(update(None), [])
        self.assertEqual(update(100), [])
        self.assertEqual(update(100)[0]["type"], "hud.initial")
        self.assertEqual(update(100), [])
        self.assertEqual(update(80), [])
        event = update(80)[0]
        self.assertEqual((event["previous"], event["value"], event["sequence"]), (100, 80, 2))

    def test_reject_noise_and_out_of_range(self):
        region = {"pattern": "[0-9]{1,3}", "minimum": 0, "maximum": 100}
        self.assertEqual(parse_reading(" 80\n", region), 80)
        self.assertIsNone(parse_reading("800", region))
        self.assertIsNone(parse_reading("8O", region))

    def test_invalid_rectangle(self):
        with self.assertRaises(ValueError):
            validate_config({"regions": [{"name": "hp", "rect": [0.9, 0, 0.2, 1], "pattern": ".*"}]})


if __name__ == "__main__":
    unittest.main()
