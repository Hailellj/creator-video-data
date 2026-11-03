#!/usr/bin/env python3
"""Prepare a project-specific plan and resumable evidence journal; no network calls."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLATFORMS = {'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube',
             'facebook': 'Facebook', 'snapchat': 'Snapchat', 'x': 'X', 'twitter': 'X'}
ROUTES = {
    'TikTok': {'metrics': 'clockworks/tiktok-scraper', 'comments': 'datadoping/tiktok-comment-reply-scraper'},
    'Instagram': {'metrics': 'apify/instagram-scraper', 'comments': 'quota-checked Actor or current logged-in browser'},
    'YouTube': {'metrics': 'streamers/youtube-scraper', 'comments': 'streamers/youtube-comments-scraper'},
    'Facebook': {'metrics': 'exact Reel Actor + public page exception', 'comments': 'apify/facebook-comments-scraper'},
    'Snapchat': {'metrics': 'exact original public page or explained Story boundary', 'comments': 'Spotlight initial public JSON; pagination unverified'},
    'X': {'metrics': 'xquik/x-tweet-scraper', 'comments': 'fresh zero precheck; nonzero retrieval requires validation'},
}
STATES = {'pending', 'submitted', 'running', 'succeeded', 'verified', 'failed', 'blocked', 'partial'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic(path, data):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def prepare(args):
    config = read(args.config)
    if not isinstance(config, dict) or not isinstance(config.get('inventory'), str):
        raise ValueError('Config must contain an inventory file path')
    source = Path(config['inventory']).expanduser()
    if not source.is_absolute():
        source = args.config.resolve().parent / source
    raw = read(source)
    if not isinstance(raw, list) or not raw:
        raise ValueError('Inventory must be a nonempty JSON array')
    kinds = config.get('tasks', ['metrics', 'comments'])
    if not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds) or any(k not in ('metrics', 'comments') for k in kinds):
        raise ValueError('tasks must be a unique nonempty list of metrics and/or comments')
    mode = config.get('collection_mode', 'full')
    if mode not in ('full', 'incremental'):
        raise ValueError('collection_mode must be full or incremental')
    timezone = config.get('timezone', 'UTC')
    ZoneInfo(timezone)
    if 'requested_metrics' in config and (not isinstance(config['requested_metrics'], list) or not config['requested_metrics'] or any(not isinstance(x, str) or not x for x in config['requested_metrics'])):
        raise ValueError('requested_metrics must be a nonempty list of field names')
    inventory, rows = [], set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'inventory[{i}] must be an object')
        row = item.get('source_row', item.get('row'))
        url = item.get('source_url', item.get('url'))
        platform = PLATFORMS.get(str(item.get('platform', '')).lower())
        if not isinstance(row, int) or isinstance(row, bool) or row < 1 or row in rows:
            raise ValueError(f'inventory[{i}] has invalid/duplicate source row')
        if not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https') or not urlparse(url).netloc or url != url.strip():
            raise ValueError(f'inventory[{i}] must contain an exact HTTP(S) source URL without surrounding spaces')
        if platform is None:
            raise ValueError(f'inventory[{i}] has unsupported/missing platform')
        video_id = item.get('video_id', '')
        if not isinstance(video_id, str):
            raise ValueError(f'inventory[{i}].video_id must be a string')
        canonical = item.get('canonical_url', item.get('resolved_url', '')) or ''
        if not isinstance(canonical, str) or (canonical and (urlparse(canonical).scheme not in ('http', 'https') or not urlparse(canonical).netloc)):
            raise ValueError(f'inventory[{i}].canonical_url must be an HTTP(S) URL or empty')
        rows.add(row)
        inventory.append({**item, 'source_row': row, 'source_url': url, 'platform': platform,
                          'canonical_url': canonical, 'video_id': video_id})
    inventory.sort(key=lambda x: x['source_row'])
    phases = [{'id': 'preflight', 'depends_on': [], 'purpose': 'Verify current template, exact IDs, quotas/schema and writable target'}]
    groups = []
    for kind in kinds:
        for platform in dict.fromkeys(x['platform'] for x in inventory):
            phase = f'collect.{kind}.{platform}'
            items = [x for x in inventory if x['platform'] == platform]
            phases.append({'id': phase, 'depends_on': ['preflight'], 'purpose': ROUTES[platform][kind]})
            groups.append({'phase': phase, 'platform': platform, 'kind': kind, 'suggested_route': ROUTES[platform][kind],
                           'source_rows': [x['source_row'] for x in items],
                           'urls': list(dict.fromkeys(x['source_url'] for x in items)),
                           'input_status': 'requires current schema/quota/reply validation; not Actor-ready input'})
    collector_ids = [g['phase'] for g in groups]
    phases.append({'id': 'normalize', 'depends_on': collector_ids, 'purpose': 'Reconcile exact source population and audit normalized data'})
    last = 'normalize'
    if 'comments' in kinds and config.get('analysis', True):
        phases.append({'id': 'analysis', 'depends_on': ['normalize'], 'purpose': 'Context-aware translations, coding and evidence-linked findings'})
        last = 'analysis'
    phases.append({'id': 'payload', 'depends_on': [last], 'purpose': 'Build typed target payload; compare latest baseline'})
    phases.append({'id': 'publish', 'depends_on': ['payload'], 'purpose': 'Authorized API/browser write with one persistence sample and checkpoints'})
    phases.append({'id': 'verify', 'depends_on': ['publish'], 'purpose': 'Reload cloud; audit values/types/formulas/link targets and requested ownership'})
    run = {'version': 2, 'created_at_utc': now(), 'config': config,
           'inventory_source': str(source.resolve()), 'config_hash': digest(config),
           'inventory_hash': digest(inventory), 'video_count': len(inventory),
           'phases': phases, 'groups': groups, 'collection_mode': mode, 'timezone': timezone,
           'collection_date': None, 'scope': 'Offline plan; route suggestions are historical, not fresh service verification'}
    if args.directory.exists():
        raise ValueError('Task directory already exists; use status/resume, or a new directory')
    args.directory.mkdir(parents=True)
    atomic(args.directory / 'inventory.json', inventory)
    atomic(args.directory / 'task.json', run)
    return {'status': 'prepared', 'directory': str(args.directory.resolve()), 'video_count': len(inventory),
            'groups': groups, 'unresolved_video_id_rows': [x['source_row'] for x in inventory if not x['video_id']],
            'next': 'Validate current IDs/template/schema/quota and record preflight evidence; no jobs have started'}


def load_task(directory):
    task = read(directory / 'task.json')
    inventory = read(directory / 'inventory.json')
    if digest(inventory) != task['inventory_hash']:
        raise ValueError('Prepared inventory changed; create a new task instead of resuming this snapshot')
    if digest(task['config']) != task['config_hash']:
        raise ValueError('Prepared config changed; create a new task instead of resuming this snapshot')
    states = {x['id']: {'status': 'pending', 'generation': 0} for x in task['phases']}
    journal = directory / 'events.jsonl'
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            event = json.loads(line)
            if event['phase'] not in states:
                raise ValueError('Journal contains an unknown phase')
            old = states[event['phase']]
            definition = next(x for x in task['phases'] if x['id'] == event['phase'])
            deps = {name: states[name]['generation'] for name in definition['depends_on']}
            if 'dependency_generations' not in event:
                event['dependency_generations'] = deps
            if 'generation' not in event:
                changed = any(old.get(key) != event.get(key) for key in ('status', 'evidence', 'run_id', 'dependency_generations'))
                event['generation'] = old['generation'] + int(changed)
            states[event['phase']] = {**old, **event}
    return task, states


def effective_states(task, states):
    """A changed upstream generation invalidates all dependent completed results."""
    done = {'succeeded', 'verified', 'partial'}
    effective, stale = {}, []
    for phase in task['phases']:
        name = phase['id']
        state = states[name]
        invalid = [parent for parent in phase['depends_on']
                   if effective[parent] not in done or
                   state.get('dependency_generations', {}).get(parent) != states[parent]['generation']]
        if state['status'] != 'pending' and invalid:
            effective[name] = 'stale'
            stale.append({'phase': name, 'upstream': invalid, 'stored_status': state['status'],
                          'action': 'Rebuild/reverify after dependencies are current; inspect any existing active job first'})
        else:
            effective[name] = state['status']
    return effective, stale


def record(args):
    task, states = load_task(args.directory)
    if args.phase not in states:
        raise ValueError('Unknown phase; use status to inspect the plan')
    old = states[args.phase]
    if old['status'] in ('submitted', 'running') and args.status == 'pending':
        raise ValueError('An active job cannot be reset to pending; inspect and record its terminal state')
    if args.status in ('submitted', 'running') and not (args.run_id or old.get('run_id')):
        raise ValueError('A running/submitted external job requires its run/session ID')
    is_new_run = bool(old.get('run_id') and args.run_id and old['run_id'] != args.run_id)
    if args.new_attempt:
        if not args.phase.startswith('collect.') or old['status'] not in ('succeeded', 'verified', 'failed', 'partial') or not args.note:
            raise ValueError('A new attempt requires a terminal collector state and a reason; inspect active runs first')
    if is_new_run and not args.new_attempt:
        raise ValueError('New run ID requires --new-attempt after the existing run has ended')
    if args.new_attempt and (not is_new_run or args.status not in ('submitted', 'running')):
        raise ValueError('A new attempt must start a distinct recorded run ID as submitted/running')
    if old['status'] in ('succeeded', 'verified') and args.status in ('pending', 'submitted', 'running') and not args.new_attempt:
        raise ValueError('Do not reset a completed phase; use a justified new collector attempt or new task')
    if args.status == 'partial' and not args.phase.startswith('collect.'):
        raise ValueError('Partial is a collection coverage boundary, not a completed analysis/publication/verification')
    evidence = []
    for path in args.evidence:
        if not path.is_file():
            raise ValueError(f'Evidence file does not exist: {path}')
        evidence.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if args.status in ('succeeded', 'verified', 'partial') and not evidence:
        raise ValueError('Successful/verified/partial phases require saved evidence')
    if args.status in ('failed', 'blocked', 'partial') and not args.note:
        raise ValueError('Failure/blocked/partial requires an explicit boundary or next-action note')
    definition = next(x for x in task['phases'] if x['id'] == args.phase)
    effective, _ = effective_states(task, states)
    if args.status in ('submitted', 'running', 'succeeded', 'verified', 'partial'):
        unmet = [p for p in definition['depends_on'] if effective[p] not in ('succeeded', 'verified', 'partial')]
        if unmet:
            raise ValueError('Unfinished dependencies: ' + ', '.join(unmet))
    if args.phase == 'verify' and args.status == 'succeeded':
        raise ValueError('Final verification must be recorded as verified with readback evidence')
    stamp = now()
    started = stamp if args.new_attempt else old.get('started_at_utc', stamp)
    event = {'phase': args.phase, 'status': args.status, 'at_utc': stamp, 'note': args.note,
             'evidence': evidence, 'run_id': args.run_id or old.get('run_id'),
             'dataset_id': args.dataset_id or (None if args.new_attempt else old.get('dataset_id')),
             'attempt': old.get('attempt', 1) + (1 if args.new_attempt else 0), 'started_at_utc': started}
    event['dependency_generations'] = {parent: states[parent]['generation'] for parent in definition['depends_on']}
    changed = any(old.get(key) != event.get(key) for key in ('status', 'evidence', 'run_id', 'dependency_generations'))
    event['generation'] = old['generation'] + int(changed)
    if args.status in ('succeeded', 'verified', 'failed', 'blocked', 'partial'):
        event['elapsed_seconds'] = round((dt.datetime.fromisoformat(stamp) - dt.datetime.fromisoformat(started)).total_seconds(), 3)
    if args.actual_cost_usd is not None:
        if args.actual_cost_usd < 0 or not math.isfinite(args.actual_cost_usd):
            raise ValueError('Actual cost must be a finite nonnegative number; omit when unknown')
        event['actual_cost_usd'] = args.actual_cost_usd
    with (args.directory / 'events.jsonl').open('a', encoding='utf-8') as file:
        file.write(json.dumps(event, ensure_ascii=False) + '\n')
    return {'status': 'recorded', 'event': event}


def status(args):
    task, states = load_task(args.directory)
    done = {'succeeded', 'verified', 'partial'}
    effective, stale = effective_states(task, states)
    active = [{**v, 'phase': k, 'action': 'Inspect existing run/session; do not resubmit'}
              for k, v in states.items() if v['status'] in ('submitted', 'running')]
    ready = [x['id'] for x in task['phases'] if effective[x['id']] in ('pending', 'stale')
             and states[x['id']]['status'] not in ('submitted', 'running')
             and all(effective[p] in done for p in x['depends_on'])]
    integrity = []
    for phase, state in states.items():
        for artifact in state.get('evidence', []):
            path = Path(artifact['path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                integrity.append({'phase': phase, 'path': str(path), 'error': 'Recorded evidence missing or changed'})
    return {'status': 'complete' if effective['verify'] == 'verified' and all(v in done for v in effective.values()) and not active and not integrity else 'in_progress',
            'collection_mode': task['collection_mode'], 'video_count': task['video_count'], 'states': states,
            'active_jobs': active, 'ready_phases': ready,
            'effective_states': effective, 'stale_phases': stale,
            'boundaries': [v for v in states.values() if v['status'] in ('partial', 'blocked', 'failed')],
            'evidence_integrity_errors': integrity,
            'scope': 'Journal reflects submitted evidence; it does not independently verify the cloud'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--config', type=Path, required=True)
    prep.add_argument('--directory', type=Path, required=True)
    rec = sub.add_parser('record')
    rec.add_argument('--directory', type=Path, required=True)
    rec.add_argument('--phase', required=True)
    rec.add_argument('--status', choices=sorted(STATES), required=True)
    rec.add_argument('--run-id')
    rec.add_argument('--dataset-id')
    rec.add_argument('--new-attempt', action='store_true')
    rec.add_argument('--actual-cost-usd', type=float)
    rec.add_argument('--evidence', type=Path, action='append', default=[])
    rec.add_argument('--note', default='')
    for cmd in ('status', 'resume'):
        action = sub.add_parser(cmd)
        action.add_argument('--directory', type=Path, required=True)
    args = p.parse_args()
    try:
        result = prepare(args) if args.command == 'prepare' else record(args) if args.command == 'record' else status(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ZoneInfoNotFoundError, AttributeError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
