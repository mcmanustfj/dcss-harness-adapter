import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dcss_harness.changes import read_changelog, record_start, report, safe_report, changelog, BASELINE
from dcss_harness.presentation import compact_observation
from dcss_harness.daemon import serve


def entry(number=1, scope='daemon', restart='required', status='complete'):
    return (f'## CA-{number:04d} — 2026-10-02 — Change {number}\n\n'
            f'Status: {status}\nScope: {scope}\nDaemon-restart: {restart}\n'
            f'Files: dcss_harness/game.py\n\nObservable change {number}.\n')


class ChangeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.session = self.root / 'session'
        self.session.mkdir()
        self.runtime = {'pid': 1, 'socket': '/tmp/session-one'}
        self.log = self.root / 'CHANGELOG.md'
        self.log.write_text(entry())
        self.initial = read_changelog(self.root)
        self.assertIsNone(self.initial['changelog_error'])
        record_start(self.session, self.runtime, self.initial, self.initial)

    def current(self):
        return report(self.session, self.runtime, read_changelog(self.root))

    def test_current_read_only_report_and_compact_advice(self):
        path = self.session / BASELINE
        before = path.read_bytes()
        result = self.current()
        self.assertEqual(result['status'], 'current')
        self.assertFalse(result['restart_recommended'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result['basis'], 'completed_changelog')
        self.assertNotIn('manifest', json.loads(before))
        self.assertNotIn('hash', before.decode())
        snapshot = {'adapter_changes': result, 'messages': []}
        self.assertNotIn('since_previous_start', compact_observation(snapshot, snapshot)['adapter_changes'])
        self.assertIn('since_previous_start', compact_observation(snapshot, snapshot, full=True)['adapter_changes'])

    def test_unfinished_code_generated_data_task_and_documentation_edits_are_ignored(self):
        for path in ('dcss_harness/game.py', 'data/new.json', 'docs/guide.md', 'task.md'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('Work in progress; even invalid Python is irrelevant')
            self.assertFalse(self.current()['restart_recommended'])
            target.unlink()
            self.assertFalse(self.current()['restart_recommended'])
        # The checker reads only the changelog, not any source/data contents.
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Do not hash files')):
            self.assertEqual(read_changelog(self.root), self.initial)

    def test_in_progress_entry_only_advises_restart_after_completion(self):
        self.log.write_text(entry(2, status='in-progress') + entry())
        self.assertEqual(self.current()['status'], 'current')
        self.log.write_text(entry(2) + entry())
        result = self.current()
        self.assertTrue(result['restart_recommended'])
        self.assertEqual([e['id'] for e in result['changes']], ['CA-0002'])
        self.assertIn('Observable change 2.', result['changes'][0]['summary'])
        self.assertIn('planned maintenance', result['restart_action'])

    def test_start_during_draft_does_not_acknowledge_the_future_completion(self):
        self.log.write_text(entry(2, status='in-progress') + entry())
        draft = read_changelog(self.root)
        record_start(self.session, self.runtime, draft, draft)
        self.log.write_text(entry(2) + entry())
        self.assertTrue(self.current()['restart_recommended'])

    def test_completed_entry_edits_and_order_changes_are_not_new_features(self):
        self.log.write_text(entry().replace('Observable change 1.', 'Corrected wording.'))
        self.assertEqual(self.current()['status'], 'current')
        self.log.write_text(entry(2) + entry())
        current = read_changelog(self.root)
        record_start(self.session, self.runtime, current, current)
        self.log.write_text(entry() + entry(2))
        self.assertFalse(self.current()['restart_recommended'])

    def test_client_viewer_and_documentation_entries_do_not_restart_daemon(self):
        for scope in ('client', 'viewer', 'documentation'):
            self.log.write_text(entry(2, scope, 'not-required') + entry())
            result = self.current()
            self.assertEqual(result['status'], 'changed')
            self.assertFalse(result['restart_recommended'])

    def test_mixed_entries_only_completed_restart_metadata_matters(self):
        self.log.write_text(entry(4, status='in-progress') + entry(3, 'client', 'not-required') + entry(2) + entry())
        result = self.current()
        self.assertTrue(result['restart_recommended'])
        self.assertEqual([e['id'] for e in result['changes']], ['CA-0003', 'CA-0002'])

    def test_sessions_and_restart_history_are_independent(self):
        self.log.write_text(entry(2) + entry())
        current = read_changelog(self.root)
        other = self.root / 'other'
        other.mkdir()
        record_start(other, {'pid': 2}, current, current)
        self.assertTrue(self.current()['restart_recommended'])
        self.assertFalse(report(other, {'pid': 2}, current)['restart_recommended'])
        record_start(self.session, {'pid': 3}, current, current)
        result = report(self.session, {'pid': 3}, current)
        self.assertFalse(result['restart_recommended'])
        self.assertTrue(result['since_previous_start']['restart_changes_present'])
        self.assertNotIn('restart_recommended', result['since_previous_start'])

    def test_missing_baseline_or_different_runtime_does_not_encourage_restart(self):
        result = report(self.session, {'pid': 99}, read_changelog(self.root))
        self.assertEqual(result['status'], 'baseline_unknown')
        self.assertIsNone(result['restart_recommended'])
        self.assertIsNone(result['restart_action'])
        self.assertIsNone(result['baseline_ids'])
        (self.session / BASELINE).unlink()
        self.assertIsNone(self.current()['restart_recommended'])

    def test_completed_ids_removed_or_reverted_report_uncertainty(self):
        self.log.write_text(entry(2))
        result = self.current()
        self.assertEqual(result['status'], 'history_changed')
        self.assertIsNone(result['restart_recommended'])
        self.assertEqual(result['missing_ids'], ['CA-0001'])

    def test_completion_during_startup_reports_uncertainty_without_restart_nudge(self):
        self.log.write_text(entry(2) + entry())
        current = read_changelog(self.root)
        record_start(self.session, self.runtime, self.initial, current)
        result = self.current()
        self.assertEqual(result['status'], 'startup_uncertain')
        self.assertIsNone(result['restart_recommended'])

    def test_draft_or_wording_edits_during_startup_do_not_invalidate_baseline(self):
        self.log.write_text(entry(2, status='in-progress') + entry().replace('Observable', 'Described'))
        record_start(self.session, self.runtime, self.initial, read_changelog(self.root))
        self.assertEqual(self.current()['status'], 'current')

    def test_failed_attach_preserves_previous_successful_start(self):
        saved = (self.session / BASELINE).read_bytes()
        with patch('dcss_harness.daemon.Game') as game, patch('dcss_harness.daemon.signal.signal'), \
                patch('dcss_harness.daemon.record_adapter_start') as record:
            game.return_value.attach.side_effect = RuntimeError('startup failed')
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                serve(self.session, {'name': 'test'})
            record.assert_not_called()
        self.assertEqual((self.session / BASELINE).read_bytes(), saved)

    def test_legacy_startup_changelog_ids_are_reused_but_hashes_are_ignored(self):
        path = self.session / BASELINE
        legacy = {'runtime': self.runtime, 'stable': True, 'started_at': 100,
                  'manifest': {'format': 1, 'id': 'obsolete', 'files': {'bad': 'ignored'},
                               'entries': [{'id': 'CA-0001', 'entry_hash': 'ignored'}]},
                  'since_previous_start': {'changed_files': ['obsolete']}}
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        self.assertEqual(self.current()['status'], 'current')
        self.log.write_text(entry(2) + entry())
        result = self.current()
        self.assertTrue(result['restart_recommended'])
        self.assertEqual(result['baseline_ids'], ['CA-0001'])
        self.assertNotIn('since_previous_start', result)
        self.assertEqual(path.read_bytes(), before)

    def test_bad_metadata_and_missing_changelog_never_fall_back_to_hashes(self):
        for bad in ('not a changelog', entry() + entry(), entry().replace('Status: complete', '')):
            self.log.write_text(bad)
            result = self.current()
            self.assertEqual(result['status'], 'check_incomplete')
            self.assertIsNone(result['restart_recommended'])
        self.log.unlink()
        self.assertIsNone(self.current()['restart_recommended'])
        self.log.write_text(entry())
        for invalid in ('broken', json.dumps({'runtime': self.runtime, 'stable': True, 'format': 2,
                'changelog_baseline': {'format': 2, 'completed_ids': [None]}})):
            (self.session / BASELINE).write_text(invalid)
            self.assertIsNone(safe_report(self.session, self.runtime, read_changelog(self.root))['restart_recommended'])

    def test_changelog_metadata_is_strict(self):
        for source in ('', entry().replace('Scope: daemon', 'Scope: mystery'),
                       entry().replace('Status: complete', 'Status: ready'), entry() + entry()):
            with self.assertRaises(ValueError):
                changelog(source)


if __name__ == '__main__':
    unittest.main()
