"""Visible HUD context; unknown values stay unknown."""
from pathlib import Path


class SideReader:
    """Match the central team emblem, independently of nicknames and HUD color.

    Only installed reference templates can produce a team classification.
    """

    def __init__(self, cv2, templates, base_dir):
        self.cv2 = cv2
        self.templates = {}
        for side, filename in templates.items():
            if side not in ("T", "CT"):
                raise ValueError("Side templates must be T or CT")
            path = Path(base_dir) / filename
            if path.is_file():
                image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    raise ValueError(f"Cannot load side template: {path}")
                self.templates[side] = cv2.resize(image, (32, 32))

    def crop(self, frame):
        height, width = frame.shape[:2]
        return frame[round(height*900/960):round(height*932/960), round(width*624/1280):round(width*656/1280)]

    def read(self, frame):
        cv2 = self.cv2
        crop = self.crop(frame)
        if crop.size == 0 or not self.templates:
            return None
        gray = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (32, 32))
        scores = sorted(((float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)[0, 0]), side) for side, template in self.templates.items()), reverse=True)
        if scores[0][0] < 0.82 or (len(scores) > 1 and scores[0][0]-scores[1][0] < 0.08):
            return None
        return scores[0][1]


def build_context(observations):
    def value(field):
        observation = observations.get(field, {})
        return observation.get("value") if observation.get("status") == "confirmed" else None

    ct_score, t_score = value("ct_score"), value("t_score")
    ct_alive, t_alive = value("ct_alive"), value("t_alive")
    side = value("side")
    advantage = None
    if side is not None and ct_alive is not None and t_alive is not None:
        advantage = ct_alive-t_alive if side == "CT" else t_alive-ct_alive
    return {
        "side": side,
        "score": {"CT": ct_score, "T": t_score},
        "round_number_estimate": ct_score+t_score+1 if ct_score is not None and t_score is not None else None,
        "round_number_method": "score_sum_plus_one",
        "round_timer": value("round_timer"),
        "players_alive": {"CT": ct_alive, "T": t_alive},
        "alive_advantage": advantage,
        "health": value("health"), "armor": value("armor"), "ammo": value("ammo"),
        "observations": observations,
    }
