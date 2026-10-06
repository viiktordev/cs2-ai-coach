import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from cs2_coach.coaching import CoachingStream, FIELDS, convert
from cs2_coach import cli


def hud(field, value, timestamp=400):
    return {'type': 'hud.initial', 'field': field, 'value': value, 'timestamp_ms': timestamp}


def kill(kind='player.kill', identity='killfeed-1', timestamp=1000, **extra):
    return {
        'type': kind, 'event_id': identity, 'timestamp_ms': timestamp,
        'attacker_team': 'T', 'victim_team': 'CT',
        'context': {'observations': {field: {'status': 'confirmed'} for field in FIELDS}},
        **extra,
    }


class CoachingTest(unittest.TestCase):
    def test_groups_simultaneous_changes_preserves_zero_and_emits_deltas(self):
        stream = CoachingStream()
        first = stream.encode([hud('health', 100), hud('ct_score', 3), hud('t_score', 0)])
        self.assertEqual(first, {'t': .4, 'state': {'hp': 100, 'score': [3, 0]}})
        self.assertIsNone(stream.encode([hud('health', 100, 600)]))
        self.assertEqual(stream.encode([hud('t_score', 1, 800)]), {'t': .8, 'state': {'score': [3, 1]}})

    def test_uncertainty_does_not_present_last_known_health_as_current(self):
        stream = CoachingStream()
        stream.encode([hud('health', 100)])
        event = kill()
        event['context']['observations']['health']['status'] = 'pending'
        event['context']['observations']['ammo']['status'] = 'unavailable'
        event['context']['observations']['ct_alive']['status'] = 'stale'
        record = stream.encode([event])
        self.assertEqual(record['unknown'], ['hp', 'ammo', 'alive.CT'])
        self.assertNotIn('state', record)
        recovered = stream.encode([kill(timestamp=1200, identity='killfeed-2')])
        self.assertEqual(recovered['unknown'], [])

    def test_classification_update_replaces_count_and_preserves_preexisting(self):
        stream = CoachingStream()
        stream.encode([kill('killfeed.kill')])
        record = stream.encode([kill('killfeed.updated', timestamp=1200, classification='player.kill')])
        self.assertEqual(record['events'][0]['as'], 'kill')
        stream.encode([kill(identity='old', timestamp=1400, present_at_start=True)])
        counts = stream.finish()['detected']
        self.assertEqual(counts['kills'], 1)
        self.assertEqual(counts['unattributed_kills'], 0)
        self.assertEqual(counts['preexisting_feed'], 1)

    def test_bomb_state_is_not_a_plant_and_unknown_identity_is_omitted(self):
        stream = CoachingStream()
        record = stream.encode([{'type': 'bomb.state', 'timestamp_ms': 0, 'state': 'planted', 'planter': None, 'site': None}])
        self.assertFalse(record['events'][0]['transition_observed'])
        self.assertNotIn('planter', record['events'][0])
        stream.encode([{'type': 'bomb.planted', 'timestamp_ms': 26200, 'first_observed_ms': 25800}])
        self.assertEqual(stream.finish()['detected']['plants'], 1)
        self.assertEqual(stream.finish()['detected']['existing_bomb_states'], 1)

    def test_conversion_preserves_all_clock_ammo_changes_and_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'raw.jsonl', Path(directory)/'coach.jsonl'
            events = [hud('health',100),hud('ammo',30),hud('round_timer','1:41'),
                      hud('ammo',29,600),hud('round_timer','1:40',1200),kill(timestamp=1400)]
            raw = '\n'.join(json.dumps(e) for e in events)
            source.write_text(raw, encoding='utf-8')
            convert(source, target)
            records = [json.loads(s) for s in target.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[0]['schema'], 'cs2.coach.v1')
            self.assertEqual(records[1]['state'], {'hp':100,'ammo':30,'clock':'1:41'})
            self.assertEqual(records[2]['state'], {'ammo':29})
            self.assertEqual(records[3]['state'], {'clock':'1:40'})
            self.assertEqual(records[-1]['end'], 'end_of_log')
            self.assertEqual(source.read_text(encoding='utf-8'), raw)
            with self.assertRaises(ValueError):
                convert(source, source)

    def test_rejects_out_of_order_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'raw', Path(directory)/'out'
            source.write_text('\n'.join(json.dumps(e) for e in [hud('ammo',30,1000),hud('ammo',29,800)]))
            with self.assertRaises(ValueError):
                convert(source, target)

    def test_cli_defaults_to_coach_and_can_preserve_raw_in_parallel(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = folder/'config.json'
            config.write_text(json.dumps({'confirmation_frames':1,'regions':[
                {'name':'health','rect':[0,0,.1,.1],'pattern':'[0-9]+'}]}))
            output, raw = folder/'coach.jsonl', folder/'raw.jsonl'
            backend = MagicMock()
            backend.read.side_effect = [{'health':100},{'health':54}]
            frame = MagicMock()
            frame.shape = (960,1280,3)
            args = ['cs2-events','--config',str(config),'--output',str(output),'--raw-output',str(raw)]
            with patch('sys.argv',args), patch.object(cli,'HudReader',return_value=backend), \
                 patch.object(cli,'SideReader') as side, \
                 patch.object(cli,'frames',return_value=iter([(0,0,frame),(1,200,frame)])):
                side.return_value.read.return_value = 'T'
                self.assertEqual(cli.main(),0)
            compact = [json.loads(s) for s in output.read_text().splitlines()]
            full = [json.loads(s) for s in raw.read_text().splitlines()]
            self.assertEqual(compact[0]['detectors'], ['hud'])
            self.assertEqual(compact[1]['state'], {'hp':100,'side':'T'})
            self.assertEqual(compact[2]['state'], {'hp':54})
            self.assertEqual(compact[-1]['end'],'complete')
            self.assertEqual(len(full),3)
            self.assertIn('observations',full[-1]['context'])

    def test_cli_raw_mode_preserves_original_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'config.json'
            config.write_text(json.dumps({'confirmation_frames':1,'regions':[
                {'name':'health','rect':[0,0,.1,.1],'pattern':'[0-9]+'}]}))
            backend, frame = MagicMock(), MagicMock()
            backend.read.return_value = {'health':100}
            frame.shape = (960,1280,3)
            stdout = io.StringIO()
            with patch('sys.argv',['cs2-events','--format','raw','--config',str(config)]), \
                 patch.object(cli,'HudReader',return_value=backend), patch.object(cli,'SideReader') as side, \
                 patch.object(cli,'frames',return_value=iter([(0,0,frame)])), contextlib.redirect_stdout(stdout):
                side.return_value.read.return_value = None
                self.assertEqual(cli.main(),0)
            event = json.loads(stdout.getvalue())
            self.assertEqual(event['type'],'hud.initial')
            self.assertIn('context',event)
