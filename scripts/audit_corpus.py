#!/usr/bin/env python3
"""Read-only structural/source-alignment audit; no scraping or cloud writes."""
import argparse
import collections
import json
import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def text(value, empty=False):
    return isinstance(value, str) and (empty or bool(value))


def count_valid(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, float) and math.isfinite(value) and value >= 0


def row_valid(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def video_id_from_url(url):
    """Extract only unambiguous public video identifiers; never resolve short URLs."""
    if not text(url):
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        parts = [p for p in parsed.path.split('/') if p]
        if host == 'youtu.be' and parts:
            return parts[0]
        if host == 'youtube.com' or host.endswith('.youtube.com'):
            if parts and parts[0] in ('shorts', 'embed', 'live') and len(parts) > 1:
                return parts[1]
            return parse_qs(parsed.query).get('v', [None])[0]
        patterns = [('tiktok.com', 'video'), ('instagram.com', 'reel'),
                    ('instagram.com', 'reels'), ('instagram.com', 'p'),
                    ('facebook.com', 'reel'), ('snapchat.com', 'spotlight'),
                    ('x.com', 'status'), ('twitter.com', 'status')]
        for domain, marker in patterns:
            if host == domain or host.endswith('.' + domain):
                if marker in parts and parts.index(marker) + 1 < len(parts):
                    return parts[parts.index(marker) + 1]
    except (ValueError, TypeError):
        return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('metrics', 'comments', 'coverage', 'inventory', 'output'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--expected-videos', type=int)
    parser.add_argument('--required-metrics', nargs='+', metavar='FIELD',
                        help='Required metric keys; space-separated and/or comma-separated')
    args = parser.parse_args(argv)
    if not (args.metrics or args.comments):
        parser.error('Provide --metrics and/or --comments')
    if bool(args.comments) != bool(args.coverage):
        parser.error('--comments and --coverage must be provided together')
    if args.expected_videos is not None and args.expected_videos < 0:
        parser.error('--expected-videos must be nonnegative')
    if args.required_metrics and not args.metrics:
        parser.error('--required-metrics requires --metrics')
    inputs = [path for path in (args.metrics, args.comments, args.coverage, args.inventory) if path]
    if args.output:
        try:
            same_input = args.output.resolve() in [path.resolve() for path in inputs]
            if args.output.exists():
                same_input = same_input or any(path.exists() and os.path.samefile(args.output, path)
                                                for path in inputs)
            if same_input:
                parser.error('--output must not overwrite an input file')
        except OSError as error:
            parser.error(str(error))
    required = list(dict.fromkeys(part.strip() for token in (args.required_metrics or [])
                                 for part in token.split(',') if part.strip()))
    if args.required_metrics and not required:
        parser.error('--required-metrics requires nonempty field names')
    errors, warnings, counts = [], [], {}

    def load(path, label):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f'{label}: cannot read JSON: {error}')
            return None
        if not isinstance(data, list) or any(not isinstance(rec, dict) for rec in data):
            errors.append(f'{label}: expected a JSON array of objects')
            return None
        return data

    def index_rows(records, label, field=None):
        indexed = {}
        for i, rec in enumerate(records):
            row = rec.get(field) if field else rec.get('source_row', rec.get('row'))
            if not row_valid(row):
                errors.append(f'{label}[{i}]: invalid {field or "row/source_row"}')
                continue
            if row in indexed:
                errors.append(f'{label}: duplicate source row {row}')
            indexed[row] = rec
        if args.expected_videos is not None and len(indexed) != args.expected_videos:
            errors.append(f'{label}: {len(indexed)} videos, expected {args.expected_videos}')
        return indexed

    def inventory_url(rec):
        return rec.get('source_url', rec.get('url'))

    def check_source(rec, label, expected=None):
        for field in ('source_url', 'platform'):
            if not text(rec.get(field)):
                errors.append(f'{label}: missing/string-invalid {field}')
        for field in ('video_id', 'author_id'):
            if field in rec and rec[field] is not None and not isinstance(rec[field], str):
                errors.append(f'{label}: {field} must be a string or null')
        canonical = rec.get('canonical_url') or rec.get('resolved_url')
        vid = rec.get('video_id')
        extracted = video_id_from_url(canonical)
        if text(vid) and extracted is not None and vid != extracted:
            errors.append(f'{label}: video_id differs from canonical/resolved URL')
        if expected:
            for field in ('source_url', 'platform'):
                value = inventory_url(expected) if field == 'source_url' else expected.get(field)
                if rec.get(field) != value:
                    errors.append(f'{label}: {field} differs from inventory')
            expected_vid = expected.get('video_id') or video_id_from_url(
                expected.get('canonical_url') or expected.get('resolved_url') or inventory_url(expected))
            if expected_vid and text(vid) and vid != expected_vid:
                errors.append(f'{label}: video_id differs from inventory')
            if expected_vid and extracted and extracted != expected_vid:
                errors.append(f'{label}: canonical/resolved URL video differs from inventory')

    inventory = load(args.inventory, 'inventory') if args.inventory else None
    inv_index = index_rows(inventory, 'inventory') if inventory is not None else None
    if inv_index is not None:
        counts['inventory_videos'] = len(inventory)
        for row, rec in inv_index.items():
            if not text(inventory_url(rec)) or not text(rec.get('platform')):
                errors.append(f'inventory row {row}: missing source_url/url or platform')
            if rec.get('video_id') is not None and not isinstance(rec['video_id'], str):
                errors.append(f'inventory row {row}: video_id must be a string or null')

    def match_population(index, label):
        if inv_index is not None and set(index) != set(inv_index):
            errors.append(f'{label}: video population differs from inventory')

    metric_index = None
    if args.metrics:
        records = load(args.metrics, 'metrics')
        if records is not None:
            metric_index = index_rows(records, 'metrics', 'row')
            match_population(metric_index, 'metrics')
            counts.update(metrics_videos=len(records), metric_unknowns=0, metric_confirmed_zeroes=0)
            for i, rec in enumerate(records):
                row = rec.get('row')
                expected = inv_index.get(row) if inv_index is not None and row_valid(row) else None
                check_source(rec, f'metrics[{i}]', expected)
                for field in ('retrieved_at', 'status'):
                    if not text(rec.get(field)):
                        errors.append(f'metrics[{i}]: missing/string-invalid {field}')
                vals = rec.get('metrics')
                if not isinstance(vals, dict) or not vals:
                    errors.append(f'metrics[{i}]: missing metric object')
                    continue
                reasons = rec.get('metric_reasons') or {}
                if not isinstance(reasons, dict):
                    errors.append(f'metrics[{i}]: metric_reasons must be an object')
                    reasons = {}
                for field in required:
                    if field not in vals:
                        errors.append(f'metrics[{i}]: missing required metric {field}')
                for field, value in vals.items():
                    if value is None:
                        counts['metric_unknowns'] += 1
                        if not rec.get('notes') and not reasons.get(field):
                            warnings.append(f'metrics[{i}].{field}: unknown without reason/notes')
                    elif not count_valid(value):
                        errors.append(f'metrics[{i}].{field}: invalid count/sentinel {value!r}')
                    elif value == 0:
                        counts['metric_confirmed_zeroes'] += 1

    if args.comments:
        comments = load(args.comments, 'comments')
        coverage = load(args.coverage, 'coverage')
        if comments is not None and coverage is not None:
            cov_index = index_rows(coverage, 'coverage', 'source_row')
            match_population(cov_index, 'coverage')
            counts.update(comment_records=len(comments), coverage_videos=len(coverage))
            platform_counts = collections.Counter()
            keys, record_ids, source_ids = set(), set(), set()
            graphs, record_lookup = {}, {}
            collected, tops, replies = collections.Counter(), collections.Counter(), collections.Counter()
            for i, rec in enumerate(comments):
                label = f'comments[{i}]'
                platform = rec.get('platform')
                if text(platform):
                    platform_counts[platform] += 1
                for field, seen in [('unique_key', keys), ('record_id', record_ids)]:
                    value = rec.get(field)
                    if not text(value):
                        errors.append(f'{label}: missing/string-invalid {field}')
                    elif value in seen:
                        errors.append(f'{label}: duplicate {field} {value}')
                    else:
                        seen.add(value)
                for field in ('comment_id', 'parent_id'):
                    if not text(rec.get(field), empty=field == 'parent_id'):
                        errors.append(f'{label}: {field} must be a string')
                if not text(rec.get('video_id')):
                    errors.append(f'{label}: video_id must be a nonempty string')
                row, cid, pid = rec.get('source_row'), rec.get('comment_id'), rec.get('parent_id')
                if not row_valid(row):
                    errors.append(f'{label}: invalid source_row')
                    continue
                expected = inv_index.get(row) if inv_index is not None else None
                check_source(rec, label, expected)
                collected[row] += 1
                if text(pid):
                    replies[row] += 1
                elif pid == '':
                    tops[row] += 1
                cov = cov_index.get(row)
                if cov is None:
                    errors.append(f'{label}: video absent from coverage')
                else:
                    for field in ('source_url', 'platform'):
                        if rec.get(field) != cov.get(field):
                            errors.append(f'{label}: {field} differs from coverage')
                    cov_vid = cov.get('video_id') or video_id_from_url(cov.get('canonical_url'))
                    if cov_vid and rec.get('video_id') != cov_vid:
                        errors.append(f'{label}: video_id differs from coverage')
                if text(cid):
                    identity = (row, cid)
                    if identity in source_ids:
                        errors.append(f'{label}: duplicate source/comment ID {identity!r}')
                    source_ids.add(identity)
                    record_lookup[identity] = rec
                    if isinstance(pid, str):
                        graphs[identity] = (row, pid) if pid else None
                    if pid == cid:
                        errors.append(f'{label}: self-parent reply')
                    vid = rec.get('video_id')
                    key = rec.get('unique_key')
                    # Historical URL-based keys remain supported without accepting arbitrary keys.
                    allowed = {f'{platform}:{vid}:{cid}'}
                    for field in ('source_url', 'canonical_url'):
                        if text(rec.get(field)):
                            allowed.add(f'{rec[field]}:{cid}')
                    if text(key) and key not in allowed:
                        errors.append(f'{label}: unique_key does not match platform/video/comment or source URL')
                original = rec.get('text_original')
                if 'media_only' in rec and not isinstance(rec['media_only'], bool):
                    errors.append(f'{label}: media_only must be a boolean')
                if not isinstance(original, str):
                    errors.append(f'{label}: original text must be a string')
                elif original and (not text(rec.get('text_en')) or not text(rec.get('text_zh'))):
                    errors.append(f'{label}: missing/string-invalid translation')
                elif not original and not rec.get('media_only'):
                    warnings.append(f'{label}: blank original without media-only flag')
                if 'likes' not in rec:
                    warnings.append(f'{label}: likes absent; preserve explicit null for unknown')
                elif rec['likes'] is not None and not count_valid(rec['likes']):
                    errors.append(f'{label}: invalid likes')
                if rec.get('comment_type') in ('reply', 'top_level'):
                    expected_type = 'reply' if pid else 'top_level'
                    if rec['comment_type'] != expected_type:
                        errors.append(f'{label}: comment_type conflicts with parent_id')
                if pid == '' and rec.get('parent_record_id'):
                    errors.append(f'{label}: top-level comment has parent_record_id')
            for identity, parent in graphs.items():
                if parent is not None and parent not in source_ids:
                    errors.append(f'comment {identity!r}: orphan parent {parent[1]}')
                rec = record_lookup[identity]
                if parent in record_lookup and rec.get('parent_record_id'):
                    if rec['parent_record_id'] != record_lookup[parent].get('record_id'):
                        errors.append(f'comment {identity!r}: parent_record_id mismatch')
            # Iterative walk handles deep threads without a recursion limit.
            finished = set()
            for start in graphs:
                if start in finished:
                    continue
                path, positions, node = [], {}, start
                while node is not None and node in graphs and node not in finished:
                    if node in positions:
                        cycle = path[positions[node]:]
                        if len(cycle) > 1:
                            errors.append('Reply cycle: ' + ' -> '.join(str(x) for x in cycle))
                        break
                    positions[node] = len(path)
                    path.append(node)
                    node = graphs[node]
                finished.update(path)
            for row, cov in cov_index.items():
                check_source(cov, f'coverage row {row}', inv_index.get(row) if inv_index is not None else None)
                for field, measured in [('unique_records', collected[row]), ('top_level', tops[row]), ('replies', replies[row])]:
                    if not count_valid(cov.get(field)) or cov[field] != measured:
                        errors.append(f'coverage row {row}: {field} differs from corpus')
                if not text(cov.get('raw_status')):
                    errors.append(f'coverage row {row}: missing/string-invalid status')
                if cov.get('raw_status') == 'verified_zero_public_comments' and collected[row] != 0:
                    errors.append(f'coverage row {row}: public-zero status conflicts with records')
                if metric_index is not None:
                    metric = metric_index.get(row)
                    if metric is None or metric.get('source_url') != cov.get('source_url') or metric.get('platform') != cov.get('platform'):
                        errors.append(f'coverage row {row}: metric inventory URL/row/platform mismatch')
            if metric_index is not None and set(metric_index) != set(cov_index):
                errors.append('Metrics and Coverage video populations differ')
            counts['platform_records'] = dict(platform_counts)
            counts['zero_record_videos'] = [row for row in cov_index if not collected[row]]
            counts['zero_record_video_states'] = {str(row): cov_index[row].get('raw_status') for row in counts['zero_record_videos']}
            counts['coverage_states'] = dict(collections.Counter(rec.get('raw_status') if text(rec.get('raw_status')) else '<invalid>' for rec in coverage))
            counts['top_level_records'] = sum(tops.values())
            counts['reply_records'] = sum(replies.values())

    result = dict(status='passed' if not errors else 'failed', counts=counts, errors=errors,
                  warnings=warnings, scope='Offline audit; not live completeness, translation accuracy or cloud QA')
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + '\n', encoding='utf-8')
        except OSError as error:
            result['status'] = 'failed'
            result['errors'].append(f'output: cannot write report: {error}')
            rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        # Last-resort input/OS error boundary, never emit a traceback or a false pass.
        print(json.dumps({'status': 'failed', 'errors': [f'Input/OS error: {error}'], 'warnings': [],
                          'scope': 'Offline audit; not live completeness or semantic QA'}, ensure_ascii=False))
        raise SystemExit(1)
