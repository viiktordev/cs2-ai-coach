import unittest

from cs2_coach.killfeed import KillFeedTracker


def row(kind="player.kill", fingerprint=0):
    return {"type": kind, "fingerprint": fingerprint, "attacker": "A", "victim": "B", "bbox": [1037, 67, 234, 27]}


class KillFeedTest(unittest.TestCase):
    def test_confirmation_movement_ocr_noise_and_dropout(self):
        tracker = KillFeedTracker()
        self.assertEqual(tracker.update([row()], 0, 0, {}), [])
        events = tracker.update([row()], 1, 300, {})
        self.assertEqual(events[0]["type"], "player.kill")
        changed = row(fingerprint=3)
        changed["bbox"][1] = 100
        changed["attacker"] = "OCR typo"
        self.assertEqual(tracker.update([changed], 2, 600, {}), [])
        tracker.update([], 3, 900, {})
        self.assertEqual(tracker.update([row()], 4, 1200, {}), [])

    def test_consecutive_confirmation_resets(self):
        tracker = KillFeedTracker()
        tracker.update([row()], 0, 0, {})
        tracker.update([], 1, 300, {})
        self.assertEqual(tracker.update([row()], 2, 600, {}), [])
        self.assertEqual(len(tracker.update([row()], 3, 900, {})), 1)

    def test_death_has_separate_identity_and_repeat_can_rearm(self):
        tracker = KillFeedTracker(confirmation_frames=1)
        self.assertEqual(tracker.update([row()], 0, 0, {})[0]["local_role"], "attacker")
        self.assertEqual(tracker.update([row("player.death")], 1, 300, {})[0]["local_role"], "victim")
        self.assertEqual(len(tracker.update([row()], 2, 13000, {})), 1)

    def test_two_identical_visible_rows_are_two_occurrences(self):
        tracker = KillFeedTracker()
        tracker.update([row(), row()], 0, 0, {})
        self.assertEqual(len(tracker.update([row(), row()], 1, 300, {})), 2)
        self.assertEqual(tracker.update([row()], 2, 600, {}), [])

    def test_neutral_to_local_updates_existing_event_without_counting_again(self):
        tracker = KillFeedTracker(confirmation_frames=1)
        initial = tracker.update([row('killfeed.kill')], 0, 0, {})[0]
        correction = tracker.update([row('player.kill')], 1, 300, {})[0]
        self.assertEqual(correction['type'], 'killfeed.updated')
        self.assertEqual(correction['classification'], 'player.kill')
        self.assertEqual(correction['event_id'], initial['event_id'])
        self.assertTrue(initial['present_at_start'])
        self.assertEqual(tracker.update([row('killfeed.kill')], 2, 600, {}), [])


if __name__ == "__main__":
    unittest.main()
