"""Real compressed-video HUD crops, including transparency and assist rows."""
import json
from pathlib import Path
import unittest
import cv2
import numpy as np
from cs2_coach.killfeed import KillFeedReader, KillFeedTracker
from cs2_coach.bomb import BombReader, BombTracker
from cs2_coach.vision import HudReader

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/video'


class Backend:
    cv2 = cv2
    ocr = None


class VideoRegressionTest(unittest.TestCase):
    def test_five_distinct_kills_survive_assists_fades_and_scene_changes(self):
        reader, tracker = KillFeedReader(Backend()), KillFeedTracker()
        events = []
        with np.load(FIXTURES/'feed-sequence.npz') as data:
            for key in sorted(data.files, key=int):
                frame = np.zeros((960, 1280, 3), np.uint8)
                crop = data[key]
                frame[57:57+crop.shape[0], 1280-crop.shape[1]:] = crop
                events.extend(tracker.update(reader.read(frame), int(key)*12, int(key)*200, {}))
        self.assertEqual([e['type'] for e in events if e['type'].startswith('player.')], ['player.kill']*5)
        self.assertEqual(len({e['event_id'] for e in events}), len(events))

    def test_bomb_template_rejects_clock_and_accepts_planted_icon(self):
        reader = BombReader(cv2, ROOT/'config/templates/bomb-planted.png')
        for n in (0, 15, 75, 134, 144, 176, 209, 245, 275):
            frame = np.zeros((960, 1280, 3), np.uint8)
            frame[:90, 603:678] = cv2.imread(str(FIXTURES/f'hud-{n}.png'))
            for scale in (1, .5):
                with self.subTest(frame=n, scale=scale):
                    image = cv2.resize(frame, None, fx=scale, fy=scale)
                    state = reader.read(image, '1:00' if n < 129 else None)['state']
                    self.assertEqual(state, 'unplanted' if n < 129 else 'planted')

    def test_score_ocr_on_changing_backgrounds(self):
        config = json.loads((ROOT/'config/hud.example.json').read_text(encoding='utf-8'))
        regions = [r for r in config['regions'] if r['name'] in ('ct_score', 't_score')]
        # OCR integration is optional when Tesseract is not installed.
        import os
        import shutil
        command = os.environ.get('TESSERACT_CMD') or shutil.which('tesseract')
        if not command:
            self.skipTest('Set TESSERACT_CMD to run real OCR regression')
        reader = HudReader(regions, command)
        for n in (0, 15, 75, 134, 144, 176, 209, 245, 275):
            frame = np.zeros((960, 1280, 3), np.uint8)
            frame[:90, 603:678] = cv2.imread(str(FIXTURES/f'hud-{n}.png'))
            with self.subTest(frame=n):
                self.assertEqual(reader.read(frame), {'ct_score': 3, 't_score': 2 if n==275 else 1})


class BombTrackerTest(unittest.TestCase):
    def test_confirmation_latching_dropout_and_rearm(self):
        tracker = BombTracker(2)
        events=[]
        states=['unplanted', 'unplanted', 'planted', None, 'planted', 'planted', None, 'planted', 'planted', 'unplanted', 'unplanted', 'planted', 'planted']
        for i,state in enumerate(states):
            events.extend(tracker.update({'state':state,'bbox':[0,0,1,1],'score':.9}, i, i*200, {}))
        self.assertEqual([e['type'] for e in events], ['bomb.planted', 'bomb.planted'])
        self.assertEqual(events[0]['first_observed_ms'],800)
        self.assertIsNone(events[0]['planter'])
        self.assertIsNone(events[0]['site'])

    def test_clip_starting_after_plant_is_state_not_new_plant(self):
        tracker=BombTracker(2)
        reading={'state':'planted','bbox':[0,0,1,1],'score':.9}
        tracker.update(reading,0,0,{})
        event=tracker.update(reading,1,200,{})[0]
        self.assertEqual(event['type'],'bomb.state')
        self.assertFalse(event['transition_observed'])
