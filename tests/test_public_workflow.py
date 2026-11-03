"""Offline behavior tests using the repository's synthetic examples only."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / 'examples'


class PublicWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='creator-video-test-')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def example(self, name):
        return json.loads((EXAMPLES / (name + '.json')).read_text(encoding='utf-8'))

    def write(self, name, data):
        path = self.work / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def cli(self, script, *arguments, ok=True):
        command = [sys.executable, str(REPO / 'scripts' / script), *map(str, arguments)]
        result = subprocess.run(command, cwd=self.work, capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        self.assertNotIn('Traceback', result.stderr)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {'stdout': result.stdout, 'stderr': result.stderr}

    def audit(self, **overrides):
        paths = {name: self.write(name + '.json', overrides.get(name, self.example(name)))
                 for name in ('inventory', 'metrics', 'comments', 'coverage')}
        return self.cli('audit_corpus.py', '--inventory', paths['inventory'],
                        '--metrics', paths['metrics'], '--comments', paths['comments'],
                        '--coverage', paths['coverage'], '--expected-videos', 3,
                        '--required-metrics', 'views', 'likes', 'saves', 'comments', 'shares',
                        ok=overrides.get('ok', True))

    def merge(self, current, previous=None, mode='full', registry=None):
        current_path = self.write('current.json', current)
        output = self.work / 'merged.json'
        args = ['--current', current_path, '--mode', mode, '--output', output,
                '--report', self.work / 'merge-report.json']
        if previous is not None:
            args += ['--previous', self.write('previous.json', previous)]
        if registry is not None:
            args += ['--registry', registry]
        report = self.cli('reconcile_comments.py', *args)
        return json.loads(output.read_text(encoding='utf-8')), report

    def test_example_audit_distinguishes_zero_and_unknown(self):
        result = self.audit()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['counts']['inventory_videos'], 3)
        self.assertEqual(result['counts']['comment_records'], 5)
        self.assertEqual(result['counts']['top_level_records'], 3)
        self.assertEqual(result['counts']['reply_records'], 2)
        self.assertEqual(result['counts']['metric_unknowns'], 4)
        self.assertEqual(result['counts']['metric_confirmed_zeroes'], 1)

    def test_audit_rejects_missing_requested_metric(self):
        metrics = self.example('metrics')
        del metrics[0]['metrics']['saves']
        result = self.audit(metrics=metrics, ok=False)
        self.assertTrue(any('missing required metric saves' in x for x in result['errors']))

    def test_audit_rejects_reply_cycle(self):
        comments = self.example('comments')
        comments[1]['parent_id'] = comments[1]['comment_id']
        result = self.audit(comments=comments, ok=False)
        self.assertTrue(any('self-parent' in x for x in result['errors']))

    def test_audit_rejects_source_mismatch(self):
        comments = self.example('comments')
        comments[0]['source_url'] = 'https://example.com/other-video'
        result = self.audit(comments=comments, ok=False)
        self.assertTrue(any('differs from inventory' in x for x in result['errors']))

    def test_full_and_incremental_have_explicit_snapshot_boundaries(self):
        previous = self.example('comments')
        current = previous[:-1]
        full, full_report = self.merge(current, previous)
        self.assertEqual(len(full), 4)
        self.assertEqual(full_report['previous_only_excluded'], 1)
        incremental, report = self.merge(current, previous, 'incremental')
        self.assertEqual(len(incremental), 5)
        carried = next(x for x in incremental if x['record_id'] == 'C0005')
        self.assertEqual(carried['_reconcile']['snapshot_status'], 'carried_previous_unrefreshed')
        self.assertEqual(carried['retrieved_at'], previous[-1]['retrieved_at'])
        self.assertEqual(carried['likes'], previous[-1]['likes'])
        self.assertEqual(report['previous_only_carried_unrefreshed'], 1)

    def test_registry_keeps_ids_when_a_record_returns(self):
        original = self.example('comments')
        registry = self.work / 'registry.json'
        first, _ = self.merge(original, registry=registry)
        added = copy.deepcopy(original[2])
        added.pop('record_id')
        added['comment_id'] = '00000000000000002002'
        added['unique_key'] = 'TikTok:' + added['video_id'] + ':' + added['comment_id']
        second, _ = self.merge(original[:-1] + [added], original, registry=registry)
        new_id = next(x['record_id'] for x in second if x['comment_id'] == added['comment_id'])
        self.assertEqual(new_id, 'C0006')
        returned, _ = self.merge(original + [added], second, registry=registry)
        old_ids = {x['unique_key']: x['record_id'] for x in first}
        self.assertTrue(all(x['record_id'] == old_ids[x['unique_key']]
                            for x in returned if x['unique_key'] in old_ids))

    def test_same_text_different_comments_remain_separate(self):
        records, _ = self.merge(self.example('comments'))
        repeated = [x for x in records if x['text_original'] == 'How much does it cost?']
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0]['unique_key'], repeated[1]['unique_key'])

    def test_cache_requires_matching_parent_context_and_codebook(self):
        previous = self.example('comments')
        current = copy.deepcopy(previous)
        for rec in current:
            rec['text_en'] = ''
            rec['text_zh'] = ''
            rec.pop('sentiment')
        matching, _ = self.merge(current, previous)
        self.assertEqual(matching[1]['text_zh'], previous[1]['text_zh'])
        self.assertEqual(matching[1]['sentiment'], previous[1]['sentiment'])
        current[0]['text_original'] = 'Does the example include a charger?'
        changed, _ = self.merge(current, previous)
        child = next(x for x in changed if x['record_id'] == 'C0002')
        self.assertEqual(child['text_zh'], '')
        self.assertNotIn('sentiment', child)
        new_codebook = copy.deepcopy(previous)
        for rec in new_codebook:
            rec['codebook_version'] = 'demo-2'
            rec['text_zh'] = ''
            rec.pop('sentiment')
        versioned, _ = self.merge(new_codebook, previous)
        self.assertEqual(versioned[0]['text_zh'], previous[0]['text_zh'])
        self.assertNotIn('sentiment', versioned[0])

    def test_conflicting_current_versions_need_ordered_times(self):
        records = self.example('comments')
        changed = copy.deepcopy(records[0])
        changed['likes'] = 99
        path = self.write('conflicting.json', records + [changed])
        result = self.cli('reconcile_comments.py', '--current', path,
                          '--output', self.work / 'merged.json', ok=False)
        self.assertEqual(result['status'], 'failed')

    def verify(self, actual=None, baseline=None, ok=True):
        args = ['--expected', self.write('payload.json', self.example('payload')),
                '--actual', self.write('readback.json', actual or self.example('readback'))]
        if baseline is not None:
            args += ['--baseline', self.write('baseline.json', baseline)]
        return self.cli('verify_delivery.py', *args, ok=ok)

    def test_example_readback_has_real_native_link_evidence(self):
        result = self.verify()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['tabs'][0]['native_links_verified'], 5)

    def test_readback_rejects_missing_link_and_numeric_id(self):
        actual = self.example('readback')
        actual['links'][0]['segments'] = []
        result = self.verify(actual, ok=False)
        self.assertTrue(any('native-link' in x for x in result['errors']))
        actual = self.example('readback')
        actual['values'][3][1] = int(actual['values'][3][1])
        result = self.verify(actual, ok=False)
        self.assertTrue(any('value/type' in x for x in result['errors']))

    def test_readback_rejects_formula_and_non_target_changes(self):
        actual = self.example('readback')
        actual['formulas'][1][0] = '=1+1'
        result = self.verify(actual, ok=False)
        self.assertTrue(any('formula differs' in x for x in result['errors']))
        baseline = self.example('readback')
        baseline['values'].append(['Preserved note'] + [None] * 6)
        baseline['formulas'].append([None] * 7)
        actual = copy.deepcopy(baseline)
        actual['values'][-1][0] = 'Changed note'
        result = self.verify(actual, baseline, ok=False)
        self.assertTrue(any('non-target value changed' in x for x in result['errors']))

    def prepared_task(self, tasks=None):
        config = self.example('project')
        config['inventory'] = str(self.write('inventory.json', self.example('inventory')))
        if tasks is not None:
            config['tasks'] = tasks
        task = self.work / 'task'
        result = self.cli('workflow.py', 'prepare', '--config', self.write('project.json', config),
                          '--directory', task)
        self.assertEqual(result['video_count'], 3)
        self.evidence = self.write('evidence.json', {'status': 'passed', 'scope': 'Synthetic offline test'})
        return task

    def record(self, task, phase, status='succeeded', ok=True, *extra):
        return self.cli('workflow.py', 'record', '--directory', task, '--phase', phase,
                        '--status', status, *extra, ok=ok)

    def test_task_plan_groups_only_requested_work(self):
        task = self.prepared_task(['metrics'])
        result = self.cli('workflow.py', 'resume', '--directory', task)
        self.assertEqual(result['ready_phases'], ['preflight'])
        data = json.loads((task / 'task.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['groups']), 3)
        self.assertNotIn('analysis', [x['id'] for x in data['phases']])

    def test_active_job_is_owned_and_cannot_be_silently_replaced(self):
        task = self.prepared_task(['metrics'])
        self.record(task, 'preflight', 'verified', True, '--evidence', self.evidence)
        self.record(task, 'collect.metrics.YouTube', 'submitted', True, '--run-id', 'DEMO-RUN-001')
        result = self.cli('workflow.py', 'resume', '--directory', task)
        self.assertEqual(result['active_jobs'][0]['run_id'], 'DEMO-RUN-001')
        self.record(task, 'collect.metrics.YouTube', 'pending', False)
        self.record(task, 'collect.metrics.YouTube', 'running', False, '--run-id', 'DEMO-RUN-002')

    def test_upstream_failure_invalidates_completed_downstream(self):
        task = self.prepared_task(['metrics'])
        phases = json.loads((task / 'task.json').read_text(encoding='utf-8'))['phases']
        for phase in phases:
            self.record(task, phase['id'], 'verified' if phase['id'] == 'verify' else 'succeeded',
                        True, '--evidence', self.evidence)
        self.assertEqual(self.cli('workflow.py', 'resume', '--directory', task)['status'], 'complete')
        self.record(task, 'normalize', 'failed', True, '--note', 'Synthetic regression test')
        result = self.cli('workflow.py', 'resume', '--directory', task)
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(result['effective_states']['verify'], 'stale')
        self.record(task, 'normalize', 'succeeded', True, '--evidence', self.evidence)
        self.record(task, 'verify', 'verified', False, '--evidence', self.evidence)
        for phase in ('payload', 'publish', 'verify'):
            self.record(task, phase, 'verified' if phase == 'verify' else 'succeeded',
                        True, '--evidence', self.evidence)
        self.assertEqual(self.cli('workflow.py', 'resume', '--directory', task)['status'], 'complete')

    def test_changed_evidence_or_inventory_prevents_false_completion(self):
        task = self.prepared_task(['metrics'])
        self.record(task, 'preflight', 'verified', True, '--evidence', self.evidence)
        self.evidence.write_text('{"status":"changed"}', encoding='utf-8')
        result = self.cli('workflow.py', 'resume', '--directory', task)
        self.assertTrue(result['evidence_integrity_errors'])
        data = json.loads((task / 'inventory.json').read_text(encoding='utf-8'))
        data[0]['source_url'] = 'https://example.com/changed'
        (task / 'inventory.json').write_text(json.dumps(data), encoding='utf-8')
        self.cli('workflow.py', 'resume', '--directory', task, ok=False)


if __name__ == '__main__':
    unittest.main()
