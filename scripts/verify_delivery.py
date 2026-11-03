#!/usr/bin/env python3
"""Verify saved Feishu sheet readbacks against typed payloads, without network calls."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


class InputError(ValueError):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'),
                          parse_constant=lambda x: (_ for _ in ()).throw(InputError('Nonfinite JSON number')))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f'Cannot read JSON {path}: {exc}') from exc


def scalar_equal(expected, actual):
    if expected in ('', None) and actual in ('', None):
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        finite_expected = isinstance(expected, int) or math.isfinite(expected)
        finite_actual = isinstance(actual, int) or math.isfinite(actual)
        return finite_expected and finite_actual and expected == actual
    return type(expected) is type(actual) and expected == actual


def sheets_from(data):
    if isinstance(data, dict) and 'tabs' in data:
        sheets = data['tabs']
    elif isinstance(data, list):
        sheets = data
    elif isinstance(data, dict):
        sheets = [data]
    else:
        raise InputError('Expected sheet object, sheet array, or object with tabs')
    if not isinstance(sheets, list) or any(not isinstance(x, dict) for x in sheets):
        raise InputError('tabs must be a list of sheet objects')
    return sheets


def sheet_label(sheet):
    label = sheet.get('name') or sheet.get('id')
    if not isinstance(label, str) or not label:
        raise InputError('Every sheet requires a nonempty name or id')
    return label


def match_sheet(expected, candidates):
    matches = [s for s in candidates if
               (expected.get('id') is not None and s.get('id') == expected.get('id')) or
               (expected.get('name') is not None and s.get('name') == expected.get('name'))]
    if len(matches) != 1:
        raise InputError(f'{sheet_label(expected)}: expected one readback, found {len(matches)}')
    if expected.get('id') and matches[0].get('id') != expected['id']:
        raise InputError(f'{sheet_label(expected)}: readback sheet id differs')
    return matches[0]


def coordinate(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InputError(f'{label} must be a nonnegative integer')
    return value


def expected_cells(sheet):
    """Rows/cols are zero based, including the table header."""
    if 'cells' in sheet:
        cells = sheet['cells']
        if not isinstance(cells, list):
            raise InputError('cells must be a list')
        result = {}
        for cell in cells:
            if not isinstance(cell, dict) or 'value' not in cell:
                raise InputError('Each target cell requires row, col, value')
            key = (coordinate(cell.get('row'), 'row'), coordinate(cell.get('col'), 'col'))
            if key in result:
                raise InputError(f'Duplicate target cell {key}')
            result[key] = dict(cell)
        return result
    headers, rows = sheet.get('headers'), sheet.get('rows')
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise InputError('Expected tab requires headers/rows or cells')
    matrix = [headers] + rows
    width = len(headers)
    if not width or any(not isinstance(row, list) or len(row) != width for row in matrix):
        raise InputError('Expected table rows must match nonempty header width')
    ro = coordinate(sheet.get('start_row', 0), 'start_row')
    co = coordinate(sheet.get('start_col', 0), 'start_col')
    hcols = sheet.get('hyperlink_columns', [])
    if not isinstance(hcols, list) or any(not isinstance(c, int) or isinstance(c, bool) or c < 0 or c >= width for c in hcols):
        raise InputError('hyperlink_columns must contain valid table column indexes')
    formulas = sheet.get('formulas')
    if formulas is not None and (not isinstance(formulas, list) or len(formulas) != len(matrix)
                                or any(not isinstance(r, list) or len(r) != width for r in formulas)):
        raise InputError('Expected formulas must match the full header+rows matrix')
    result = {}
    for r, row in enumerate(matrix):
        for c, value in enumerate(row):
            cell = {'row': r + ro, 'col': c + co, 'value': value,
                    'formula': formulas[r][c] if formulas is not None else None}
            if r > 0 and c in hcols and value not in ('', None):
                cell['link'] = {'text': value, 'target': value}
            result[(r + ro, c + co)] = cell
    return result


def matrix_value(sheet, field, row, col):
    matrix = sheet.get(field)
    if (not isinstance(matrix, list) or row >= len(matrix) or
            not isinstance(matrix[row], list) or col >= len(matrix[row])):
        raise InputError(f'{sheet_label(sheet)}: missing {field} evidence at ({row}, {col}); sheet may be unloaded')
    return matrix[row][col]


def link_index(sheet):
    links = sheet.get('links')
    if links is None:
        return None
    if not isinstance(links, list):
        raise InputError('Readback links must be a list')
    result = {}
    for group in links:
        cells = group if isinstance(group, list) else [group]
        for cell in cells:
            if not isinstance(cell, dict):
                raise InputError('Readback links must contain cell objects')
            key = (coordinate(cell.get('row'), 'link row'), coordinate(cell.get('col'), 'link col'))
            if key in result:
                raise InputError(f'Duplicate link evidence at {key}')
            segments = cell.get('segments')
            if segments is not None and not isinstance(segments, list):
                raise InputError('Link segments must be a list or null')
            result[key] = segments
    return result


def config_entries(path, key):
    if path is None:
        return []
    data = read_json(path)
    data = data.get(key) if isinstance(data, dict) else data
    if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
        raise InputError(f'{path}: expected list of {key}')
    return data


def entry_applies(entry, sheet):
    return entry.get('sheet') in {sheet.get('name'), sheet.get('id')}


def extra_change_allowed(entries, sheet, row, col):
    for entry in entries:
        if not entry_applies(entry, sheet):
            continue
        r = coordinate(entry.get('row'), 'allowed row')
        c = coordinate(entry.get('col'), 'allowed col')
        nr = entry.get('row_count', 1)
        nc = entry.get('col_count', 1)
        if not isinstance(nr, int) or isinstance(nr, bool) or nr < 1 or not isinstance(nc, int) or isinstance(nc, bool) or nc < 1:
            raise InputError('Allowed range row_count/col_count must be positive integers')
        if r <= row < r + nr and c <= col < c + nc:
            return True
    return False


def validate(expected, actuals, baselines, normalizations, allowed):
    errors, applied, summaries = [], [], []
    targets_by_sheet = {}
    for tab in sheets_from(expected):
        label = sheet_label(tab)
        actual = match_sheet(tab, actuals)
        targets = expected_cells(tab)
        targets_by_sheet[label] = targets
        links = link_index(actual)
        checked_values = checked_formulas = checked_links = 0
        for (row, col), cell in targets.items():
            prefix = f'{label} ({row}, {col})'
            value = matrix_value(actual, 'values', row, col)
            wanted = cell['value']
            same = scalar_equal(wanted, value)
            if not same:
                rules = [n for n in normalizations if entry_applies(n, actual)
                         and n.get('row') == row and n.get('col') == col]
                for rule in rules:
                    if rule.get('rule') != 'nbsp_to_space' or 'expected' not in rule:
                        raise InputError('Each normalization requires rule=nbsp_to_space and exact expected text')
                    if (isinstance(wanted, str) and '\u00a0' in wanted and rule['expected'] == wanted
                            and value == wanted.replace('\u00a0', ' ')):
                        same = True
                        applied.append({'sheet': label, 'row': row, 'col': col, 'rule': rule['rule']})
                        break
            if not same:
                errors.append(f'{prefix}: value/type differs ({type(wanted).__name__} vs {type(value).__name__})')
            checked_values += 1
            formula = matrix_value(actual, 'formulas', row, col)
            if not scalar_equal(cell.get('formula'), formula):
                errors.append(f'{prefix}: formula differs')
            checked_formulas += 1
            if 'link' in cell:
                spec = cell['link']
                spec = {'text': wanted, 'target': spec} if isinstance(spec, str) else spec
                if not isinstance(spec, dict) or not isinstance(spec.get('target'), str) or not isinstance(spec.get('text'), str):
                    raise InputError(f'{prefix}: link requires string text and target')
                segments = links.get((row, col)) if links is not None else None
                if links is None or (row, col) not in links:
                    errors.append(f'{prefix}: missing native-link evidence')
                else:
                    urls = [s for s in (segments or []) if isinstance(s, dict) and s.get('type') == 'url']
                    if len(urls) != 1 or urls[0].get('text') != spec['text'] or urls[0].get('link') != spec['target']:
                        errors.append(f'{prefix}: native-link text/target differs or missing')
                    else:
                        checked_links += 1
            elif links is not None and (row, col) in links:
                segments = links[(row, col)] or []
                if any(isinstance(s, dict) and s.get('type') == 'url' for s in segments):
                    errors.append(f'{prefix}: unexpected native-link on a plain-value target')
        summaries.append({'sheet': label, 'values_checked': checked_values,
                          'formulas_checked': checked_formulas, 'native_links_verified': checked_links})

    baseline_values = baseline_formulas = baseline_links = 0
    for baseline in baselines:
        actual = match_sheet(baseline, actuals)
        label = sheet_label(baseline)
        targets = targets_by_sheet.get(label, {})
        if not targets:
            for tab in sheets_from(expected):
                if tab.get('id') and tab.get('id') == baseline.get('id'):
                    targets = expected_cells(tab)
                    break
        matrix = baseline.get('values')
        if not isinstance(matrix, list) or any(not isinstance(r, list) for r in matrix):
            raise InputError(f'{label}: baseline requires values matrix')
        for row, cells in enumerate(matrix):
            for col, wanted in enumerate(cells):
                if (row, col) in targets or extra_change_allowed(allowed, actual, row, col):
                    continue
                if not scalar_equal(wanted, matrix_value(actual, 'values', row, col)):
                    errors.append(f'{label} ({row}, {col}): non-target value changed')
                baseline_values += 1
                if not scalar_equal(matrix_value(baseline, 'formulas', row, col), matrix_value(actual, 'formulas', row, col)):
                    errors.append(f'{label} ({row}, {col}): non-target formula changed')
                baseline_formulas += 1
        before_links, after_links = link_index(baseline), link_index(actual)
        if before_links is None:
            errors.append(f'{label}: baseline lacks non-target native-link evidence')
        else:
            for (row, col), segments in before_links.items():
                if (row, col) in targets or extra_change_allowed(allowed, actual, row, col):
                    continue
                if after_links is None or (row, col) not in after_links:
                    errors.append(f'{label} ({row}, {col}): missing non-target native-link readback')
                elif segments != after_links[(row, col)]:
                    errors.append(f'{label} ({row}, {col}): non-target native-link segments changed')
                baseline_links += 1
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'tabs': summaries, 'normalizations_applied': applied,
            'non_target_checks': {'requested': bool(baselines), 'captured_values_checked': baseline_values,
                                  'captured_formulas_checked': baseline_formulas,
                                  'explicit_link_cells_checked': baseline_links},
            'not_verified': ['Live cloud persistence/freshness', 'Document content', 'Styles, filters and sheet structure',
                             'Uncaptured cells/links and whole-file preservation']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--actual', type=Path, action='append', required=True)
    parser.add_argument('--baseline', type=Path, action='append', default=[])
    parser.add_argument('--allowed-changes', type=Path)
    parser.add_argument('--normalizations', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    inputs = [args.expected] + args.actual + args.baseline + [p for p in (args.allowed_changes, args.normalizations) if p]
    try:
        if args.output and args.output.resolve() in [p.resolve() for p in inputs]:
            raise InputError('--output must not overwrite an input file')
        actuals = [s for path in args.actual for s in sheets_from(read_json(path))]
        baselines = [s for path in args.baseline for s in sheets_from(read_json(path))]
        result = validate(read_json(args.expected), actuals, baselines,
                          config_entries(args.normalizations, 'normalizations'),
                          config_entries(args.allowed_changes, 'allowed_changes'))
        result['evidence'] = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]
        code = 0 if result['status'] == 'passed' else 1
    except (InputError, OSError, TypeError, KeyError, IndexError, OverflowError) as exc:
        result = {'status': 'failed', 'input_error': str(exc), 'errors': [],
                  'not_verified': ['Input could not be fully verified']}
        code = 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        # Never write a diagnostic over an input, including on malformed-input failures.
        if args.output.resolve() not in [p.resolve() for p in inputs]:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + '\n', encoding='utf-8')
            except OSError as exc:
                print(f'Cannot write report: {exc}', file=sys.stderr)
                return 2
    if args.output:
        brief = dict(result)
        brief['error_count'] = len(result.get('errors', []))
        brief['errors'] = result.get('errors', [])[:20]
        brief['errors_truncated'] = len(result.get('errors', [])) > 20
        brief['report_path'] = str(args.output)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    else:
        print(rendered)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
