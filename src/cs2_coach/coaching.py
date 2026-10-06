"""Compact, streaming observations for coaching; no OCR or LLM required."""
import argparse
from collections import Counter
from itertools import groupby
import json
import math
from pathlib import Path


FIELDS = {
    'health': 'hp', 'armor': 'armor', 'ammo': 'ammo', 'side': 'side',
    'round_timer': 'clock', 'ct_score': 'score.CT', 't_score': 'score.T',
    'ct_alive': 'alive.CT', 't_alive': 'alive.T',
}
TYPES = {'player.kill': 'kill', 'player.death': 'death',
         'killfeed.kill': 'feed_kill', 'killfeed.updated': 'feed_update'}


def seconds(milliseconds):
    return round(milliseconds / 1000, 3)


def dumps(record):
    return json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class CoachingStream:
    """State deltas are historical observations, not a continuously valid HUD.

    Feed identities are retained so a later classification update changes the
    summary rather than incrementing another kill. No tactical claims are inferred.
    """

    def __init__(self):
        self.state = {}
        self.feed = {}
        self.plants = 0
        self.existing_bombs = 0
        self.last_timestamp = 0
        self.anonymous_id = 0

    @staticmethod
    def header(source=None, detectors=None):
        result = {
            'schema': 'cs2.coach.v1', 'time_unit': 's',
            'time_basis': 'confirmation', 'team_order': ['CT', 'T'],
            'event_team_order': ['attacker', 'victim'],
            'state_semantics': 'last_confirmed_deltas',
            'unknown_semantics': 'unconfirmed_fields_at_event',
            'evidence': 'visual_detections_not_ground_truth',
        }
        if source:
            result['source'] = source.get('kind', 'unknown')
        if detectors is not None:
            result['detectors'] = detectors
        return result

    def _state_change(self, field, value, changes):
        if field in self.state and self.state[field] == value:
            return
        self.state[field] = value
        name = FIELDS.get(field, field)
        if '.' in name and field in FIELDS:
            group, _ = name.split('.')
            prefix = 'score' if group == 'score' else 'alive'
            changes[group] = [self.state.get(f'ct_{prefix}'), self.state.get(f't_{prefix}')]
        else:
            changes[name] = value

    @staticmethod
    def _unknown(event):
        observations = event.get('context', {}).get('observations', {})
        return [name for field, name in FIELDS.items()
                if observations.get(field, {}).get('status') != 'confirmed']

    def _feed_event(self, event):
        identity = event.get('event_id')
        if identity is None:
            self.anonymous_id += 1
            identity = f"untracked-{self.anonymous_id}"
        kind = event.get('classification', event['type'])
        previous = self.feed.get(identity)
        preexisting = event.get('present_at_start', previous['preexisting'] if previous else False)
        self.feed[identity] = {'kind': kind, 'preexisting': preexisting}
        result = {'type': TYPES[event['type']], 'id': identity}
        if event['type'] == 'killfeed.updated':
            result['as'] = TYPES.get(kind, kind)
        teams = [event.get('attacker_team'), event.get('victim_team')]
        if any(team is not None for team in teams):
            result['teams'] = teams  # Attacker, victim; distinct from score's CT,T order.
        if preexisting:
            result['preexisting'] = True
        names = [event.get('attacker_ocr'), event.get('victim_ocr')]
        if any(name is not None for name in names):
            result['names_unverified'] = names
        return result

    def encode(self, events):
        """Encode all raw events from one frame as a single compact record."""
        if not events:
            return None
        timestamp = events[0]['timestamp_ms']
        if any(event['timestamp_ms'] != timestamp for event in events):
            raise ValueError('CoachingStream.encode expects one timestamp per batch')
        self.last_timestamp = timestamp
        changes, actions, unknown = {}, [], []
        for event in events:
            kind = event['type']
            if kind in ('hud.initial', 'hud.changed'):
                self._state_change(event['field'], event['value'], changes)
                continue
            if kind in TYPES:
                action = self._feed_event(event)
            elif kind in ('bomb.planted', 'bomb.state'):
                if kind == 'bomb.planted':
                    self.plants += 1
                else:
                    self.existing_bombs += 1
                action = {'type': kind, 'state': event.get('state', 'planted')}
                if kind == 'bomb.state':
                    action['transition_observed'] = event.get('transition_observed', False)
                for field in ('planter', 'site'):
                    if event.get(field) is not None:
                        action[field] = event[field]
            else:
                # Future event types must not silently disappear.
                excluded = {'schema_version', 'sequence', 'source', 'observed_at',
                            'frame_id', 'timestamp_ms', 'context', 'evidence',
                            'first_observed_ms', 'first_frame_id'}
                action = {key: value for key, value in event.items() if key not in excluded}
            first = event.get('first_observed_ms', timestamp)
            if first != timestamp:
                action['seen'] = seconds(first)
            actions.append(action)
            for field in self._unknown(event):
                if field not in unknown:
                    unknown.append(field)
        if not changes and not actions:
            return None
        result = {'t': seconds(timestamp)}
        if changes:
            result['state'] = changes
        if actions:
            result['events'] = actions
            # Always present at action boundaries, including an explicit empty list.
            result['unknown'] = unknown
        return result

    def finish(self, timestamp_ms=None, status='complete'):
        counts = Counter(item['kind'] for item in self.feed.values() if not item['preexisting'])
        result = {
            't': seconds(self.last_timestamp if timestamp_ms is None else timestamp_ms),
            'end': status,
            'detected': {
                'kills': counts['player.kill'], 'deaths': counts['player.death'],
                'unattributed_kills': counts['killfeed.kill'],
                'preexisting_feed': sum(item['preexisting'] for item in self.feed.values()),
                'plants': self.plants,
            },
        }
        if self.existing_bombs:
            result['detected']['existing_bomb_states'] = self.existing_bombs
        return result


def read_raw(path):
    previous = -math.inf
    with path.open(encoding='utf-8-sig') as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or 'type' not in event or 'timestamp_ms' not in event:
                raise ValueError(f'Line {line_number}: expected a raw event, not compact output')
            timestamp = event['timestamp_ms']
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < previous:
                raise ValueError(f'Line {line_number}: timestamps must be finite and ordered')
            previous = timestamp
            yield event


def convert(input_path, output_path):
    input_path, output_path = Path(input_path), Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError('Input and output must be different files')
    encoder = CoachingStream()
    with output_path.open('w', encoding='utf-8') as output:
        started = False
        for _, group in groupby(read_raw(input_path), key=lambda event: event['timestamp_ms']):
            batch = list(group)
            if not started:
                header = encoder.header(batch[0].get('source'))
                header['coverage'] = 'raw_events_only'
                print(dumps(header), file=output)
                started = True
            record = encoder.encode(batch)
            if record:
                print(dumps(record), file=output)
        if not started:
            print(dumps(encoder.header()), file=output)
        print(dumps(encoder.finish(status='end_of_log')), file=output)


def main():
    parser = argparse.ArgumentParser(description='Compact existing raw JSONL for coaching, without rerunning OCR')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        convert(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Error: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
