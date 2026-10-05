import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dcss_harness.client import compatibility_error, guarded_policy_request
from dcss_harness.cli import main


# Public request schemas from before enemy debuffs and selective recovery.
LEGACY = {
    'combat': {'max_actions', 'max_seconds', 'min_hp_percent', 'max_threat', 'allow_status'},
    'recover': {'max_actions', 'max_seconds', 'allow_status', 'clear_statuses'},
    'wait-for': {'max_actions', 'max_seconds', 'monster_id', 'distance', 'min_hp_percent', 'max_threat'},
}


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session = Path(directory.name)
        self.inputs = []

    def legacy(self, session, message):
        # Round-trip the actual request payload and use the old daemon's
        # pre-action rejection behavior. Rejected policies send no game input.
        message = json.loads(json.dumps(message))
        operation, policy = message['op'], message.get('policy', {})
        if operation not in LEGACY:
            return {'error': 'Unknown operation'}
        if set(policy) - LEGACY[operation]:
            kind = 'combat' if operation == 'combat' else 'recovery'
            return {'error': f'Unknown {kind} policy option'}
        self.inputs.append(message)
        return {'ok': True}

    def invoke(self, args, handler=None):
        output = io.StringIO()
        with patch('sys.argv', ['crawl-agent', '--session-dir', str(self.session), '--stream', 'compat', *args]), \
                patch('dcss_harness.client.request', side_effect=handler or self.legacy) as request, redirect_stdout(output):
            code = main()
        return code, json.loads(output.getvalue()), request

    def test_unchanged_commands_work_with_old_daemon_and_one_request(self):
        cases = [ ['combat'], ['combat', '--max-threat', '0'], ['recover'],
                  ['recover', '--allow-status', 'Slow', '--clear-statuses'], ['wait-for', '--monster-id', '7'] ]
        for args in cases:
            with self.subTest(args=args):
                code, result, request = self.invoke(args)
                timing = result.pop('timing')
                self.assertEqual(timing['request_id'], request.call_args.args[1]['request_id'])
                self.assertNotIn('daemon_ms', timing)
                self.assertEqual((code, result), (0, {'ok': True}))
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0], self.session)
                policy = request.call_args.args[1]['policy']
                self.assertNotIn('allow_enemy_status', policy)
                self.assertNotIn('clear_status', policy)
                self.assertFalse(any(v is False or v == [] for v in policy.values()))
                if '--max-threat' in args:
                    self.assertEqual(policy['max_threat'], 0)

    def test_requested_new_features_get_restart_error_without_fallback(self):
        cases = [(['combat', '--allow-enemy-status', 'drain'], '--allow-enemy-status'),
                 (['recover', '--allow-status', 'Slow', '--clear-status', 'Slow'], '--clear-status'),
                 (['wait-for', '--monster-id', '7', '--allow-status', 'Fly'], '--allow-status')]
        for args, flag in cases:
            with self.subTest(args=args):
                code, result, request = self.invoke(args)
                self.assertEqual(code, 1)
                self.assertEqual(result['error_code'], 'restart_required')
                self.assertIn(flag, result['error'])
                self.assertIn('save/stop/start', result['error'])
                self.assertIn('Unknown', result['daemon_error'])
                request.assert_called_once()
                self.assertEqual(self.inputs, [])
                self.assertFalse((self.session / 'observation-compat.json').exists())

    def test_unsupported_command_and_other_errors_are_distinct(self):
        code, result, request = self.invoke(['combat'], lambda *args: {'error': 'Unknown operation'})
        self.assertEqual((code, result['error_code']), (1, 'restart_required'))
        request.assert_called_once()
        for message in ('Game is still updating; observe before acting', 'max_actions must be an integer from 1 to 32',
                        'Unknown recovery policy option: malformed', 'socket failure'):
            with self.subTest(message=message):
                code, result, request = self.invoke(['recover'], lambda *args: {'error': message})
                self.assertIn('request_id', result.pop('timing'))
                self.assertEqual((code, result), (1, {'error': message}))
                request.assert_called_once()
        response = {'player': {}, 'error': 'Unknown recovery policy option'}
        self.assertEqual(compatibility_error({'op': 'recover'}, response), response)
        response = {'error': 'Unknown operation'}
        self.assertEqual(compatibility_error({'op': 'act'}, response), response)

    def test_ambiguous_timeout_never_retries_or_claims_restart(self):
        def ambiguous(*args):
            self.inputs.append('may have reached engine')
            raise TimeoutError('Timed out after sending')
        code, result, request = self.invoke(['recover'], ambiguous)
        self.assertIn('request_id', result.pop('timing'))
        self.assertEqual((code, result), (1, {'error': 'Timed out after sending'}))
        request.assert_called_once()
        self.assertEqual(self.inputs, ['may have reached engine'])

    def test_local_invalid_policy_never_reaches_daemon(self):
        for args in (['combat', '--max-actions', '33'], ['recover', '--clear-status', 'Slow']):
            with self.subTest(args=args):
                code, result, request = self.invoke(args)
                self.assertEqual(code, 1)
                self.assertNotIn('error_code', result)
                request.assert_not_called()

    def test_supported_features_and_numeric_zero_are_preserved(self):
        def current(session, message):
            self.inputs.append(message)
            return {'ok': True}
        code, _, request = self.invoke(['combat', '--max-threat', '0', '--allow-enemy-status', 'drain'], current)
        self.assertEqual(code, 0)
        request.assert_called_once()
        self.assertEqual(self.inputs[-1]['policy']['allow_enemy_status'], ['drain'])
        self.assertEqual(self.inputs[-1]['policy']['max_threat'], 0)
        options = {'allow_status': [], 'clear_statuses': False, 'clear_status': [], 'max_seconds': 1}
        self.assertEqual(guarded_policy_request('recover', options), {'op': 'recover', 'policy': {'max_seconds': 1}})
        self.assertIn('clear_statuses', options)  # Caller-owned data was not mutated.


if __name__ == '__main__':
    unittest.main()
