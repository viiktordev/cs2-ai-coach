"""Observe the planted-C4 HUD icon; disappearance alone has no known outcome."""
from datetime import datetime, timezone
from pathlib import Path


class BombReader:
    def __init__(self, cv2, template_path):
        self.cv2 = cv2
        self.template = cv2.imread(str(Path(template_path)), cv2.IMREAD_GRAYSCALE)
        if self.template is None or cv2.countNonZero(self.template) < 10:
            raise ValueError(f"Invalid planted-bomb template: {template_path}")

    def read(self, frame, round_timer=None):
        cv2 = self.cv2
        height, width = frame.shape[:2]
        x, y = round(603*width/1280), 0
        right, bottom = round(678*width/1280), round(34*height/960)
        crop = frame[y:bottom, x:right]
        crop = cv2.resize(crop, (75, 34))
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 100, 50), (10, 255, 255)) | cv2.inRange(hsv, (170, 100, 50), (179, 255, 255))
        score = 0.0
        if cv2.countNonZero(mask) >= 10:
            padded = cv2.copyMakeBorder(mask, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0)
            score = float(cv2.matchTemplate(padded, self.template, cv2.TM_CCOEFF_NORMED).max())
        return {'state': 'planted' if score >= .65 else ('unplanted' if round_timer else None),
                'score': round(score, 4), 'bbox': [x, y, right-x, bottom-y]}


class BombTracker:
    def __init__(self, confirmation_frames=3):
        self.confirmation_frames = confirmation_frames
        self.pending = None
        self.count = 0
        self.state = None
        self.first = None

    def update(self, reading, frame_id, timestamp_ms, source):
        value = reading['state']
        if value is None:
            self.pending, self.count = None, 0
            return []
        if value != self.pending:
            self.pending, self.count = value, 0
            self.first = timestamp_ms, frame_id
        self.count += 1
        if self.count < self.confirmation_frames or value == self.state:
            return []
        previous, self.state = self.state, value
        if value != 'planted':
            return []  # A returning clock rearms; it does not prove defuse/explosion.
        return [{
            'schema_version': '1.0',
            'type': 'bomb.planted' if previous == 'unplanted' else 'bomb.state',
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'timestamp_ms': round(timestamp_ms, 3), 'frame_id': frame_id,
            'first_observed_ms': round(self.first[0], 3), 'first_frame_id': self.first[1],
            'source': source, 'state': 'planted', 'planter': None, 'site': None,
            'transition_observed': previous == 'unplanted',
            'evidence': {'method': 'planted_bomb_icon_template', 'bbox': reading['bbox'],
                         'match_score': reading['score'], 'consecutive_readings': self.count},
        }]
