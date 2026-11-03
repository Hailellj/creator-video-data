#!/usr/bin/env python3
"""Merge normalized comments offline, retaining stable IDs and snapshot boundaries.

This accepts normalized JSON arrays, not raw platform Actor responses. It never
fetches data or writes to a cloud service.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


PLATFORMS = {x.casefold(): x for x in
             ('TikTok', 'Instagram', 'YouTube', 'Facebook', 'Snapchat', 'X')}
TRANSLATION_FIELDS = ('text_en', 'text_zh', 'translation_note')
SEMANTIC_FIELDS = ('feedback_scope', 'feedback_scope_zh', 'semantic_target',
                   'is_direct_current_phone', 'sentiment', 'intent', 'intent_zh',
                   'themes_original', 'topics', 'original_language', 'coding_note')
CONTEXT_FIELDS = ('wave', 'product_context', 'video_context', 'product_id',
                  'model', 'creator', 'source_url', 'canonical_url', 'author',
                  'author_id', 'is_creator', 'media_only', 'original_display',
                  'media_metadata', 'attachments')
PROVENANCE_FIELDS = {'run_id', 'dataset_id', 'raw_source', 'retrieval_method',
                     'collector_source_url', '_reconcile', 'comments_sheet_row',
                     'parent_record_id', 'legacy_unique_key'}


class InputError(ValueError):
    pass


def read_array(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
        raise InputError(f'{path}: expected a normalized JSON array of objects')
    return data


def identity(record, label):
    for field in ('platform', 'video_id', 'comment_id'):
        if not isinstance(record.get(field), str) or not record[field]:
            raise InputError(f'{label}: {field} must be a nonempty string')
    if not isinstance(record.get('parent_id', ''), str):
        raise InputError(f'{label}: parent_id must be a string')
    if not isinstance(record.get('text_original'), str):
        raise InputError(f'{label}: text_original must be a string')
    if record.get('record_id') is not None and not isinstance(record['record_id'], str):
        raise InputError(f'{label}: record_id must be a string')
    platform = PLATFORMS.get(record['platform'].casefold(), record['platform'])
    key = ':'.join((platform, record['video_id'], record['comment_id']))
    legacy_keys = {record[f] + ':' + record['comment_id']
                   for f in ('source_url', 'canonical_url', 'collector_source_url', 'resolved_url')
                   if isinstance(record.get(f), str) and record[f]}
    if record.get('unique_key') and record['unique_key'] != key and record['unique_key'] not in legacy_keys:
        raise InputError(f'{label}: unique_key does not match platform/video/comment ID or a verified source URL key')
    return key


def timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def content(record):
    return {k: v for k, v in record.items() if k not in PROVENANCE_FIELDS}


def parent_key(record):
    parent = record.get('parent_id', '')
    if not parent:
        return ''
    platform = PLATFORMS.get(record['platform'].casefold(), record['platform'])
    return ':'.join((platform, record['video_id'], parent))


def contexts(records):
    """Conservative exact contexts, including every available ancestor's text."""
    cache, active = {}, set()

    def visit(key):
        if key in cache:
            return cache[key]
        if key in active:
            raise InputError(f'parent cycle involving {key}')
        active.add(key)
        rec = records[key]
        pkey = parent_key(rec)
        parent_context = None
        complete = True
        if pkey:
            if pkey == key:
                raise InputError(f'self-parent comment {key}')
            if pkey not in records:
                complete = False
            else:
                parent_context, complete = visit(pkey)
        ctx = {'text_original': rec['text_original'], 'parent_key': pkey,
               'parent_context': parent_context,
               'video_and_author': {f: rec.get(f) for f in CONTEXT_FIELDS}}
        fingerprint = hashlib.sha256(json.dumps(ctx, ensure_ascii=False,
                                               sort_keys=True).encode('utf-8')).hexdigest()
        active.remove(key)
        cache[key] = (fingerprint, complete)
        return cache[key]

    for key in records:
        visit(key)
    return cache


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reconcile(current_files, previous_file=None, registry_file=None, mode='full'):
    warnings, resolutions, inputs, previous, current = [], [], [], {}, {}
    legacy_migrations = set()
    origins = {}
    duplicates = 0
    if previous_file:
        for i, rec in enumerate(read_array(previous_file)):
            key = identity(rec, f'previous[{i}]')
            if key in previous:
                raise InputError(f'previous contains duplicate identity {key}')
            normalized = copy.deepcopy(rec)
            if rec.get('unique_key') and rec['unique_key'] != key:
                normalized['legacy_unique_key'] = rec['unique_key']
                legacy_migrations.add(key)
            normalized['unique_key'] = key
            previous[key] = normalized
    registry = {}
    if registry_file and registry_file.exists():
        reg = json.loads(registry_file.read_text(encoding='utf-8'))
        if not isinstance(reg, dict) or reg.get('schema_version') != 1 or not isinstance(reg.get('ids'), dict):
            raise InputError('registry must contain schema_version=1 and an ids object')
        registry = dict(reg['ids'])
        if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in registry.items()):
            raise InputError('registry identities and IDs must be nonempty strings')
    for path in current_files:
        data = read_array(path)
        inputs.append({'path': str(path), 'records': len(data)})
        for i, rec in enumerate(data):
            label = f'{path}[{i}]'
            key = identity(rec, label)
            candidate = copy.deepcopy(rec)
            if rec.get('unique_key') and rec['unique_key'] != key:
                candidate['legacy_unique_key'] = rec['unique_key']
                legacy_migrations.add(key)
            candidate['unique_key'] = key
            candidate.setdefault('parent_id', '')
            origins.setdefault(key, []).append(label)
            if key not in current:
                current[key] = candidate
                continue
            prior = current[key]
            old_id, new_id = prior.get('record_id'), candidate.get('record_id')
            if old_id and new_id and old_id != new_id:
                raise InputError(f'current stable ID conflict for {key}: {old_id} vs {new_id}')
            if content(prior) == content(candidate):
                duplicates += 1
                continue
            old_time, new_time = timestamp(prior.get('retrieved_at')), timestamp(candidate.get('retrieved_at'))
            if old_time is None or new_time is None or old_time == new_time:
                raise InputError(f'current versions conflict without ordered aware retrieved_at: {key}')
            chosen = candidate if new_time > old_time else prior
            current[key] = chosen
            resolutions.append({'unique_key': key, 'method': 'later_retrieved_at',
                                'selected_retrieved_at': chosen.get('retrieved_at'),
                                'changed_fields': sorted(k for k in set(prior) | set(candidate)
                                                         if prior.get(k) != candidate.get(k))})

    ids_to_keys = {}

    def reserve(key, value):
        if not value:
            return
        if key in registry and registry[key] != value:
            raise InputError(f'stable ID conflicts with registry/history for {key}')
        other = ids_to_keys.get(value)
        if other is not None and other != key:
            raise InputError(f'record_id {value} is assigned to multiple identities')
        registry[key] = value
        ids_to_keys[value] = key

    for key, value in list(registry.items()):
        reserve(key, value)
    for key, rec in previous.items():
        reserve(key, rec.get('record_id'))
    for key, rec in current.items():
        reserve(key, rec.get('record_id'))
    maximum = max((int(match.group(1)) for value in registry.values()
                   if (match := re.fullmatch(r'C(\d+)', value))), default=0)

    current_contexts = contexts(current)
    previous_contexts = contexts(previous)
    result = copy.deepcopy(current)
    carried = []
    if mode == 'incremental':
        for key, rec in previous.items():
            if key not in current:
                result[key] = copy.deepcopy(rec)
                carried.append(key)
    cache_records, cached_translation_fields, cached_semantic_fields = 0, 0, 0
    cache_rejected = []
    for key, rec in result.items():
        if key not in registry:
            maximum += 1
            reserve(key, f'C{maximum:04d}')
        rec['record_id'] = registry[key]
        rec['unique_key'] = key
        rec.setdefault('parent_id', '')
        rec.setdefault('likes', None)
        rec['comment_type'] = 'reply' if rec['parent_id'] else 'top_level'
        meta = {'snapshot_status': 'carried_previous_unrefreshed' if key in carried else 'current',
                'cache_reused_fields': []}
        if key in carried:
            meta['note'] = 'Retained from previous snapshot; retrieved_at and metrics were not refreshed.'
        else:
            meta['current_sources'] = origins[key]
            if timestamp(rec.get('retrieved_at')) is None:
                warnings.append({'unique_key': key, 'reason': 'missing_or_nonaware_retrieved_at'})
            old = previous.get(key)
            if old is not None:
                equal = (current_contexts[key][1] and previous_contexts[key][1]
                         and current_contexts[key][0] == previous_contexts[key][0])
                if equal:
                    translation_version_matches = rec.get('translation_version') == old.get('translation_version')
                    if translation_version_matches:
                        for field in TRANSLATION_FIELDS:
                            if not rec.get(field) and old.get(field):
                                rec[field] = copy.deepcopy(old[field])
                                meta['cache_reused_fields'].append(field)
                                cached_translation_fields += 1
                    codebook = rec.get('codebook_version')
                    if codebook and codebook == old.get('codebook_version'):
                        for field in SEMANTIC_FIELDS:
                            if (field not in rec or rec[field] is None) and field in old:
                                rec[field] = copy.deepcopy(old[field])
                                meta['cache_reused_fields'].append(field)
                                cached_semantic_fields += 1
                    elif any(f not in rec for f in SEMANTIC_FIELDS):
                        cache_rejected.append({'unique_key': key, 'reason': 'semantic_codebook_unversioned_or_changed'})
                else:
                    cache_rejected.append({'unique_key': key, 'reason': 'text_parent_product_or_author_context_changed_or_incomplete'})
            if meta['cache_reused_fields']:
                cache_records += 1
        rec['_reconcile'] = meta

    # Graph checks include carried records, but cache never uses carried records as fresh parents.
    contexts(result)
    children, roots = {}, []
    orphan_keys = []
    for key, rec in result.items():
        pkey = parent_key(rec)
        if pkey and pkey not in result:
            orphan_keys.append(key)
            warnings.append({'unique_key': key, 'reason': 'parent_not_obtained', 'parent_key': pkey})
            rec['parent_record_id'] = ''
            roots.append(key)
        elif pkey:
            children.setdefault(pkey, []).append(key)
            rec['parent_record_id'] = registry[pkey]
        else:
            rec['parent_record_id'] = ''
            roots.append(key)
    insertion = {key: i for i, key in enumerate(result)}

    def sort_key(key):
        rec = result[key]
        row = rec.get('source_row')
        row = row if isinstance(row, int) and not isinstance(row, bool) else sys.maxsize
        return (row, rec['platform'], rec['video_id'], insertion[key])

    ordered = []

    def visit(key):
        ordered.append(result[key])
        for child in sorted(children.get(key, []), key=sort_key):
            visit(child)

    for key in sorted(roots, key=sort_key):
        visit(key)
    if len(ordered) != len(result):
        raise InputError('comment graph could not be ordered completely')
    for i, rec in enumerate(ordered, 2):
        rec['comments_sheet_row'] = i
    report = {'status': 'passed_with_boundaries' if warnings or cache_rejected else 'passed',
              'mode': mode, 'scope': 'Normalized local records only; no live completeness or semantic guarantee',
              'inputs': inputs, 'current_unique_records': len(current), 'output_records': len(ordered),
              'previous_only_excluded': len(set(previous) - set(current)) if mode == 'full' else 0,
              'previous_only_carried_unrefreshed': len(carried), 'duplicates_collapsed': duplicates,
              'legacy_unique_keys_migrated': len(legacy_migrations),
              'ordered_current_version_resolutions': resolutions, 'cache_records': cache_records,
              'cached_translation_fields': cached_translation_fields,
              'cached_semantic_fields': cached_semantic_fields,
              'cache_rejected': cache_rejected, 'orphan_records': orphan_keys, 'warnings': warnings,
              'registry_ids': len(registry), 'all_output_ids_unique': len({r['record_id'] for r in ordered}) == len(ordered)}
    return ordered, report, {'schema_version': 1, 'ids': registry}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', type=Path, action='append', required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--mode', choices=('full', 'incremental'), default='full')
    parser.add_argument('--registry', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        sources = {p.resolve() for p in args.current + ([args.previous] if args.previous else [])}
        destinations = [p.resolve() for p in (args.output, args.report, args.registry) if p]
        if len(destinations) != len(set(destinations)) or any(p in sources for p in destinations):
            raise InputError('Output, report, and registry must be distinct and must not overwrite input files')
        records, report, registry = reconcile(args.current, args.previous, args.registry, args.mode)
        write_atomic(args.output, records)
        if args.registry:
            write_atomic(args.registry, registry)
        if args.report:
            write_atomic(args.report, report)
        stdout_report = report
        if args.report:
            stdout_report = {key: value for key, value in report.items()
                             if not isinstance(value, list)}
            stdout_report.update(report_path=str(args.report),
                                 warning_count=len(report['warnings']),
                                 cache_rejected_count=len(report['cache_rejected']),
                                 orphan_count=len(report['orphan_records']),
                                 current_version_resolution_count=len(report['ordered_current_version_resolutions']))
        print(json.dumps(stdout_report, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, RecursionError) as error:
        report = {'status': 'failed', 'error': str(error), 'mode': args.mode,
                  'scope': 'Operation incomplete; inputs were not modified. Check destinations before retrying a write error.'}
        # Do not write an error report to a path that could overwrite a source.
        if args.report:
            protected = {p.resolve() for p in args.current +
                         ([args.previous] if args.previous else []) +
                         ([args.registry] if args.registry else []) + [args.output]}
            if args.report.resolve() not in protected:
                try:
                    write_atomic(args.report, report)
                except OSError:
                    pass
        print(json.dumps(report, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
