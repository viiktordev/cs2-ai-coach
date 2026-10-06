from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Pending:
    value: object
    count: int
    first_timestamp_ms: float
    first_frame_id: int


class EventTracker:
    """Confirm consecutive observations; missing readings never erase state."""

    def __init__(self, confirmation_frames=3):
        if confirmation_frames < 1:
            raise ValueError("confirmation_frames must be positive")
        self.confirmation_frames = confirmation_frames
        self.state = {}
        self.pending = {}
        self.sequence = 0
        self.latest = {}
        self.last_confirmed_ms = {}

    def observations(self, timestamp_ms, stale_after_ms=2500):
        result = {}
        for field in self.latest:
            last = self.last_confirmed_ms.get(field)
            status = "unavailable"
            if self.latest[field] is not None:
                status = "pending" if field in self.pending or field not in self.state else "confirmed"
                if status == "confirmed" and (last is None or timestamp_ms-last > stale_after_ms):
                    status = "stale"
            result[field] = {"status": status, "value": self.state.get(field) if status == "confirmed" else None, "last_confirmed_value": self.state.get(field), "last_confirmed_ms": last}
        return result

    def update(self, readings, frame_id, timestamp_ms, source):
        events = []
        for field, value in readings.items():
            self.latest[field] = value
            if value is None or (field in self.state and self.state[field] == value):
                self.pending.pop(field, None)
                if value is not None:
                    self.last_confirmed_ms[field] = timestamp_ms
                continue
            pending = self.pending.get(field)
            count = pending.count + 1 if pending and pending.value == value else 1
            first_timestamp = pending.first_timestamp_ms if count > 1 else timestamp_ms
            first_frame = pending.first_frame_id if count > 1 else frame_id
            self.pending[field] = Pending(value, count, first_timestamp, first_frame)
            if count < self.confirmation_frames:
                continue
            previous = self.state.get(field)
            initial = field not in self.state
            self.state[field] = value
            self.last_confirmed_ms[field] = timestamp_ms
            self.pending.pop(field, None)
            self.sequence += 1
            events.append({
                "schema_version": "1.0", "sequence": self.sequence,
                "type": "hud.initial" if initial else "hud.changed",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "timestamp_ms": round(timestamp_ms, 3), "frame_id": frame_id,
                "first_observed_ms": round(first_timestamp, 3), "first_frame_id": first_frame,
                "source": source, "field": field,
                "previous": previous, "value": value,
                "evidence": {"method": "ocr", "consecutive_readings": count},
            })
        return events
