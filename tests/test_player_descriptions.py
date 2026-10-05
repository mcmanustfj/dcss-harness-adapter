import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from dcss_harness.presentation import compact_observation, emit_observation


class DescriptionTests(unittest.TestCase):
    def snapshot(self):
        return {'player': {'hp': 10, 'hp_max': 20, 'mp': 3, 'doom': 0, 'contam': 0,
                           'doom_desc': 'A malevolent fate is gathering.',
                           'status': [{'light': 'Slow', 'text': 'slowed', 'desc': 'You are moving slowly.'}]},
                'messages': [], 'monsters': [], 'running': True, 'settled': True,
                'input_mode': 'command', 'more': False}

    def test_static_descriptions_omitted_and_dynamic_safety_state_retained(self):
        before = self.snapshot()
        after = copy.deepcopy(before)
        after['player'].update(hp=11, doom=5, contam=3)
        result = compact_observation(after, before)
        self.assertNotIn('player_descriptions', result)
        self.assertNotIn('doom_desc', result['player'])
        self.assertEqual(result['player']['status'], [{'light': 'Slow', 'text': 'slowed'}])
        for field in ('hp', 'hp_max', 'mp', 'doom', 'contam'):
            self.assertEqual(result['player'][field], after['player'][field])
        self.assertIn('doom_desc', after['player'])  # No source mutation.

    def test_full_state_and_changed_removed_descriptions_are_replacements(self):
        before = self.snapshot()
        full = compact_observation(before)
        self.assertEqual(full['player_descriptions'], {'doom_desc': before['player']['doom_desc'],
            'statuses': [{'index': 0, 'desc': before['player']['status'][0]['desc']}]})
        after = copy.deepcopy(before)
        after['player']['doom_desc'] = 'Changed explanation'
        delta = compact_observation(after, before)
        self.assertEqual(delta['player_descriptions']['doom_desc'], 'Changed explanation')
        after['player'].pop('doom_desc')
        after['player']['status'] = []
        self.assertEqual(compact_observation(after, before)['player_descriptions'], {})

    def test_status_reordering_expiration_and_duplicates_preserve_index_mapping(self):
        before = self.snapshot()
        before['player']['status'] += [{'light': 'Fly', 'desc': 'Flying'}, {'light': 'Fly', 'desc': 'Other flight'}]
        after = copy.deepcopy(before)
        after['player']['status'] = after['player']['status'][::-1][:-1]
        result = compact_observation(after, before)
        self.assertEqual(result['player_descriptions']['statuses'],
                         [{'index': 0, 'desc': 'Other flight'}, {'index': 1, 'desc': 'Flying'}])
        restored = copy.deepcopy(result['player'])
        for status in result['player_descriptions']['statuses']:
            restored['status'][status['index']]['desc'] = status['desc']
        restored['doom_desc'] = result['player_descriptions']['doom_desc']
        self.assertEqual(restored, after['player'])

    def test_new_stream_restart_full_and_old_cursor_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            runtime = session / 'runtime.json'
            runtime.write_text('{}')
            def emit(**options):
                output = io.StringIO()
                with redirect_stdout(output):
                    emit_observation(session, self.snapshot(), snapshot_tags=False, **options)
                return json.loads(output.getvalue())
            self.assertIn('player_descriptions', emit())
            self.assertNotIn('player_descriptions', emit())
            self.assertIn('player_descriptions', emit(full=True))
            self.assertIn('player_descriptions', emit(stream='review'))
            cache = session / 'observation-default.json'
            saved = json.loads(cache.read_text())
            saved.pop('description_format')
            cache.write_text(json.dumps(saved))
            self.assertIn('player_descriptions', emit())
            self.assertNotIn('player_descriptions', emit())
            runtime.write_text('{"pid":2}')
            self.assertIn('player_descriptions', emit())


if __name__ == '__main__':
    unittest.main()
