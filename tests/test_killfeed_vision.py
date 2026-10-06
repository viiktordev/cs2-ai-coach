"""Regression coverage using kill-feed rows from the supplied screenshots."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from cs2_coach.killfeed import KillFeedReader, KillFeedTracker


class Backend:
    cv2 = cv2
    ocr = None  # Default visual detection must not call OCR.


def sample(kind):
    frame = np.zeros((960, 1280, 3), dtype=np.uint8)
    if kind == "death":
        frame[:] = (20, 20, 65)
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / f"{kind}-row.png"))
    if crop is None:
        raise AssertionError("Missing kill-feed fixture")
    height, width = crop.shape[:2]
    frame[67:67+height, 1271-width:1271] = crop
    return frame


class KillFeedVisionTest(unittest.TestCase):
    def setUp(self):
        self.reader = KillFeedReader(Backend())

    def test_brightness_changes_and_no_ocr(self):
        for kind in ("kill", "death"):
            for factor in (1, 0.8, 0.6):
                with self.subTest(kind=kind, factor=factor):
                    frame = (sample(kind).astype(float)*factor).astype(np.uint8)
                    rows = self.reader.read(frame)
                    self.assertEqual([row["type"] for row in rows], [f"player.{kind}"])
                    self.assertIsNone(rows[0]["attacker"])

    def test_names_without_team_colors_do_not_block_detection(self):
        frame = sample("kill")
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        text = cv2.inRange(hsv, (15, 45, 60), (125, 255, 255))
        frame[text != 0] = (220, 220, 220)
        self.assertEqual([r["type"] for r in self.reader.read(frame)], ["player.kill"])

    def test_scaled_frame(self):
        for kind in ("kill", "death"):
            rows = self.reader.read(cv2.resize(sample(kind), (640, 480)))
            self.assertEqual([row["type"] for row in rows], [f"player.{kind}"])

    def test_red_rectangle_without_text_is_rejected(self):
        frame = np.zeros((960, 1280, 3), dtype=np.uint8)
        cv2.rectangle(frame, (1037, 67), (1270, 93), (0, 0, 225), 1)
        self.assertEqual(self.reader.read(frame), [])

    def test_death_notification_requires_death_view(self):
        frame = sample("death")
        frame[100:] = 0
        rows = self.reader.read(frame)
        self.assertEqual([row["type"] for row in rows], ["killfeed.kill"])

    def test_darker_repeated_row_is_not_a_new_event(self):
        tracker = KillFeedTracker()
        observed = []
        for index, factor in enumerate((1, 1, 0.8, 0.6, 1)):
            frame = (sample("kill").astype(float)*factor).astype(np.uint8)
            observed.extend(tracker.update(self.reader.read(frame), index, index*300, {}))
        self.assertEqual([event["type"] for event in observed], ["player.kill"])

    def test_live_kill_then_death_with_changing_background(self):
        tracker = KillFeedTracker()
        events = []
        # Saved feed crops, combined with the death-view context recorded by
        # the live diagnostics. This does not reconstruct the full game scene.
        for index in (73, 75, 77, 79, 85, 87, 89, 91):
            frame = np.zeros((960, 1280, 3), dtype=np.uint8)
            if index in (85, 87):
                frame[:] = (20, 20, 65)
            crop = cv2.imread(str(Path(__file__).parent / "fixtures" / f"live-feed-{index}.png"))
            self.assertIsNotNone(crop)
            frame[57:57+crop.shape[0], 640:] = crop
            rows = self.reader.read(frame)
            events.extend(tracker.update(rows, index, index*400, {}))
        self.assertEqual([event["type"] for event in events], ["player.kill", "player.death"])

    def test_third_party_feed_does_not_become_a_local_kill(self):
        tracker = KillFeedTracker()
        events = []
        for index in (56, 58, 60, 62, 64, 66, 68, 70, 72):
            frame = np.zeros((960, 1280, 3), dtype=np.uint8)
            crop = cv2.imread(str(Path(__file__).parent / "fixtures" / f"neutral-feed-{index}.png"))
            self.assertIsNotNone(crop)
            frame[57:57+crop.shape[0], 640:] = crop
            events.extend(tracker.update(self.reader.read(frame), index, index*400, {}))
        self.assertEqual([event["type"] for event in events], ["killfeed.kill"])
        self.assertIsNone(events[0]["local_role"])
        self.assertEqual(events[0]["attacker_team"], "T")
        self.assertEqual(events[0]["victim_team"], "CT")
