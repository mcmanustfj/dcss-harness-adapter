"""Read-only completed-changelog checks; never inspect or hash adapter files."""

from pathlib import Path
import json
import re
import time


CHANGELOG = 'CHANGELOG.md'
BASELINE = 'adapter-startup.json'


def changelog(source):
    entries, seen = [], set()
    chunks = re.split(r'^## (CA-\d{4}) — (\d{4}-\d{2}-\d{2}) — (.+)\n', source, flags=re.M)
    if len(chunks) == 1:
        raise ValueError('No structured changelog entries')
    for i in range(1, len(chunks), 4):
        change_id, date, title, body = chunks[i:i + 4]
        metadata = {}
        for key in ('Status', 'Scope', 'Daemon-restart', 'Files'):
            matches = re.findall(r'^' + key + r': (.+)$', body, re.M)
            if len(matches) != 1:
                raise ValueError(f'{change_id}: missing or duplicate {key}')
            metadata[key] = matches[0]
        scopes = metadata['Scope'].split(', ')
        if (change_id in seen or not set(scopes) <= {'daemon', 'client', 'viewer', 'documentation'}
                or metadata['Status'] not in ('complete', 'in-progress')
                or metadata['Daemon-restart'] not in ('required', 'not-required')):
            raise ValueError(f'{change_id}: invalid or duplicate metadata')
        seen.add(change_id)
        entries.append({'id': change_id, 'date': date, 'title': title,
                        'status': metadata['Status'], 'scope': scopes,
                        'daemon_restart': metadata['Daemon-restart'],
                        'files': metadata['Files'].split(', '),
                        'summary': body.strip().split('\n\n', 1)[-1].strip()})
    return entries


def read_changelog(root):
    try:
        entries = [e for e in changelog((Path(root) / CHANGELOG).read_text())
                   if e['status'] == 'complete']
        error = None
    except (OSError, ValueError) as exc:
        entries, error = [], str(exc)
    return {'format': 2, 'completed_ids': sorted(e['id'] for e in entries),
            'entries': entries, 'changelog_error': error}


def baseline_ids(loaded):
    if not isinstance(loaded, dict) or loaded.get('format') != 2:
        return None
    ids = loaded.get('completed_ids')
    if (not isinstance(ids, list) or any(not isinstance(i, str) or
            not re.fullmatch(r'CA-\d{4}', i) for i in ids) or len(ids) != len(set(ids))):
        return None
    return set(ids)


def startup_baseline(record):
    if not record or not record.get('stable'):
        return None
    if record.get('format') == 2:
        return record.get('changelog_baseline')
    # Older successful starts already froze changelog entries alongside hashes.
    # Reuse only those historical IDs, never the hashes or today's file contents.
    legacy = record.get('manifest')
    if isinstance(legacy, dict) and legacy.get('format') == 1 and not legacy.get('changelog_error'):
        entries = legacy.get('entries')
        if isinstance(entries, list) and entries and all(isinstance(e, dict) and 'id' in e for e in entries):
            return {'format': 2, 'completed_ids': [e['id'] for e in entries
                    if e.get('status', 'complete') == 'complete']}
    return None


def compare(loaded, current):
    old = baseline_ids(loaded)
    present = set(current['completed_ids'])
    result = {'basis': 'completed_changelog', 'baseline_ids': sorted(old) if old is not None else None,
              'current_ids': sorted(present), 'changes': [],
              'changelog_error': current['changelog_error']}
    if current['changelog_error']:
        result.update(status='check_incomplete', restart_recommended=None,
                      reason='Changelog unavailable or invalid; restart need is unknown.')
    elif old is None:
        result.update(status='baseline_unknown', restart_recommended=None,
                      reason='No matching startup changelog baseline; no restart recommendation.',
                      available_changes=current['entries'][:5])
    elif old - present:
        result.update(status='history_changed', restart_recommended=None,
                      reason='Previously completed changelog IDs are missing; restart need is unknown.',
                      missing_ids=sorted(old - present))
    else:
        changes = [e for e in current['entries'] if e['id'] not in old]
        restart = any(e['daemon_restart'] == 'required' for e in changes)
        result.update(status='changed' if changes else 'current', restart_recommended=restart,
                      changes=changes,
                      reason=('Completed changelog entries need a daemon restart to load; defer to planned maintenance.' if restart else
                              'New completed entries do not require a game-adapter restart.' if changes else
                              'No newly completed changelog entries since startup.'))
    return result


def read_baseline(session):
    try:
        data = json.loads((Path(session) / BASELINE).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def record_start(session, runtime, initial, current):
    """Persist completed IDs only after successful attach; no source inspection."""
    previous = read_baseline(session)
    stable = (initial['completed_ids'] == current['completed_ids']
              and not initial['changelog_error'] and not current['changelog_error'])
    history = compare(startup_baseline(previous), current)
    history['restart_changes_present'] = history.pop('restart_recommended')
    history.pop('reason', None)
    history.pop('available_changes', None)
    history['note'] = 'Changes since the previous start; current restart advice is outside this history.'
    record = {'format': 2, 'runtime': runtime, 'started_at': time.time(), 'stable': stable,
              'changelog_baseline': {'format': 2, 'completed_ids': initial['completed_ids']},
              'since_previous_start': history}
    path = Path(session) / BASELINE
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record))
    temporary.replace(path)
    return record


def report(session, runtime, current):
    record = read_baseline(session)
    matches = record and record.get('runtime') == runtime
    result = compare(startup_baseline(record) if matches else None, current)
    if matches:
        result['started_at'] = record.get('started_at')
        # Old histories describe file comparisons; do not keep emitting those.
        if record.get('format') == 2:
            result['since_previous_start'] = record.get('since_previous_start')
        if not record.get('stable'):
            result.update(status='startup_uncertain', restart_recommended=None,
                          reason='No stable startup changelog baseline; no restart recommendation.')
    result['changelog'] = CHANGELOG
    result['restart_action'] = ('At a planned maintenance pause, coordinate save/stop/start with the controller '
                                'using the same session, character name and options. Do not restart merely '
                                'because tasks changed. No automatic update or restart.') if result['restart_recommended'] else None
    return result


def compact_report(result):
    return {key: result.get(key) for key in ('status', 'restart_recommended', 'reason')}


def safe_report(session, runtime, current):
    try:
        return report(session, runtime, current)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'status': 'check_failed', 'restart_recommended': None,
                'reason': f'Could not compare startup changelog metadata: {exc}',
                'basis': 'completed_changelog'}
