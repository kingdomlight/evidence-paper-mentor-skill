import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True

import paper_tools
from paper_tools import extract_pdf, inspect_pdf, sectionize, prepare_context, split_text
from paper_tools import verify_evidence, validate_note, read_json, render_markdown, write_json
from paper_tools import plan_tasks, recovery_plan
from evaluate import check_skill, evaluation_passed, run_evaluation

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
fitz = __import__("fitz")



def sections_fixture():
    return {'paper_id': 'paper01', 'pages': [{'page': 1, 'text': 'Abstract\nWe evaluate TinyNet on DatasetA.\nIntroduction\nWe study efficient classification.'}, {'page': 2, 'text': 'Method\nTinyNet uses a gated encoder.\nExperiments\nAccuracy improves from 70.0 to 72.5.'}], 'sections': []}

def note_fixture():
    return {'paper_id': 'paper01', 'title': 'TinyNet', 'reading_depth': 'full', 'coverage': '第 1–2 页', 'research_question': '高效分类', 'summary': 'TinyNet 使用门控编码器开展高效分类。', 'methods': [{'name': 'TinyNet', 'steps': ['门控编码']}], 'experiments': [], 'contributions': ['提出 TinyNet'], 'limitations': [], 'claims': [{'statement': 'TinyNet 使用门控编码器', 'statement_type': 'paper_fact', 'section': 'Method', 'evidence': [{'page': 2, 'section': 'Method', 'quote': 'TinyNet uses a gated encoder.'}]}], 'warnings': [], 'uncertainties': []}

class ContextTests(unittest.TestCase):

    def setUp(self):
        data = sections_fixture()
        data['pages'].append({'page': 3, 'text': 'References\n[1] Example reference.'})
        self.data = sectionize(data)

    def test_read_excludes_references(self):
        result = prepare_context(self.data, task='read')
        self.assertNotIn('References', [item['section'] for item in result['contexts']])

    def test_reference_task_can_include_references(self):
        result = prepare_context(self.data, task='references')
        self.assertIn('References', [item['section'] for item in result['contexts']])

    def test_small_budget_reports_truncation(self):
        result = prepare_context(self.data, max_characters=40)
        self.assertLessEqual(result['character_count'], 40)
        self.assertTrue(result['truncated'])

    def test_context_preserves_page_numbers(self):
        result = prepare_context(self.data)
        for item in result['contexts']:
            self.assertEqual(item['page_start'], item['pages'][0])

    def test_long_text_is_split_without_infinite_loop(self):
        chunks = split_text('x' * 100, 20)
        self.assertEqual(len(chunks), 5)
        self.assertEqual(sum((len(chunk) for chunk in chunks)), 100)

class EvidenceTests(unittest.TestCase):

    def test_valid_quote_and_page(self):
        result = verify_evidence(note_fixture(), sections_fixture())
        self.assertTrue(result['valid'])
        self.assertEqual(result['matched_evidence'], 1)

    def test_wrong_page_is_rejected(self):
        note = note_fixture()
        note['claims'][0]['evidence'][0]['page'] = 1
        result = verify_evidence(note, sections_fixture())
        self.assertFalse(result['valid'])
        self.assertIn('引文未在指定页面匹配', result['errors'][0])

    def test_altered_quote_is_rejected(self):
        note = note_fixture()
        note['claims'][0]['evidence'][0]['quote'] = 'TinyNet uses two gated encoders.'
        self.assertFalse(verify_evidence(note, sections_fixture())['valid'])

    def test_nonexistent_page_is_rejected(self):
        note = note_fixture()
        note['claims'][0]['evidence'][0]['page'] = 99
        self.assertFalse(verify_evidence(note, sections_fixture())['valid'])

    def test_missing_evidence_is_rejected(self):
        note = note_fixture()
        note['claims'][0]['evidence'] = []
        result = verify_evidence(note, sections_fixture())
        self.assertFalse(result['valid'])
        self.assertEqual(result['evidence_match_rate'], 0)

    def test_whitespace_difference_is_normalized(self):
        note = note_fixture()
        note['claims'][0]['evidence'][0]['quote'] = 'TinyNet   uses a gated encoder.'
        self.assertTrue(verify_evidence(note, sections_fixture())['valid'])

    def test_semantic_review_is_not_claimed(self):
        result = verify_evidence(note_fixture(), sections_fixture())
        self.assertFalse(result['semantic_support_checked'])
        self.assertTrue(result['manual_review_required'])

class ExtractTests(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def make_pdf(self, name='sample.pdf', text=True):
        document = fitz.open()
        for index in range(2):
            page = document.new_page()
            if text:
                content = 'Abstract\nWe evaluate TinyNet on DatasetA with repeated controls. The study measures classification accuracy and inference cost.\nIntroduction\nEfficient classification remains difficult on small devices. We describe the problem and the evaluation setting.\n' if index == 0 else 'Method\nTinyNet uses a gated encoder and a compact classifier. The encoder controls information flow through each block.\nExperiments\nAccuracy improves from 70.0 to 72.5 on DatasetA. We report three runs and compare against the same baselines.\nConclusion\nThe results support efficiency on DatasetA.'
                page.insert_text((50, 50), content, fontsize=10)
        path = self.root / name
        document.save(path)
        document.close()
        return path

    def test_inspect_and_extract_real_pdf(self):
        path = self.make_pdf()
        inspection = inspect_pdf(path)
        self.assertEqual(inspection['status'], 'ok')
        self.assertEqual(inspection['page_count'], 2)
        result = extract_pdf(path)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['pages']), 2)
        self.assertGreater(len(result['pages'][0]['blocks']), 0)

    def test_sections_keep_physical_pages(self):
        result = sectionize(extract_pdf(self.make_pdf()))
        names = [section['name'] for section in result['sections']]
        self.assertIn('Abstract', names)
        self.assertIn('Method', names)
        method = next((section for section in result['sections'] if section['name'] == 'Method'))
        self.assertEqual(method['page_start'], 2)

    def test_empty_text_pdf_is_not_ok(self):
        path = self.make_pdf('empty.pdf', text=False)
        self.assertEqual(inspect_pdf(path)['status'], 'insufficient_text')

    def test_two_column_blocks_keep_reading_order(self):
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 60), 'LEFT FIRST\nLEFT SECOND\nLEFT THIRD', fontsize=10)
        page.insert_text((320, 60), 'RIGHT FIRST\nRIGHT SECOND\nRIGHT THIRD', fontsize=10)
        path = self.root / 'two-column.pdf'
        document.save(path)
        document.close()
        text = extract_pdf(path)['pages'][0]['text']
        self.assertLess(text.index('LEFT THIRD'), text.index('RIGHT FIRST'))

    def test_missing_file_is_handled(self):
        result = inspect_pdf(self.root / 'missing.pdf')
        self.assertEqual(result['status'], 'unreadable')
        self.assertIn('error', result)

class OrchestrationTests(unittest.TestCase):

    def test_unsupported_host_uses_serial_mode(self):
        plan = plan_tasks(depth='full', page_counts={'paper01': 30}, subagents_supported=False)
        self.assertEqual(plan['mode'], 'serial')
        self.assertTrue(all((task['executor'] == 'main' for task in plan['tasks'])))

    def test_quick_read_does_not_spawn_agents(self):
        plan = plan_tasks(depth='quick', page_counts={'paper01': 30}, subagents_supported=True)
        self.assertEqual(plan['mode'], 'serial')

    def test_short_paper_does_not_spawn_agents(self):
        plan = plan_tasks(depth='full', page_counts={'paper01': 6}, subagents_supported=True)
        self.assertEqual(plan['mode'], 'serial')

    def test_long_paper_uses_three_independent_roles(self):
        plan = plan_tasks(depth='full', page_counts={'paper01': 30}, subagents_supported=True)
        self.assertEqual(plan['mode'], 'parallel')
        self.assertEqual(len(plan['tasks']), 3)
        self.assertEqual(len({task['task_type'] for task in plan['tasks']}), 3)

    def test_comparison_preserves_paper_ids_and_batches(self):
        ids = ['paper01', 'paper02', 'paper03', 'paper04']
        plan = plan_tasks(task='compare', paper_ids=ids, subagents_supported=True)
        self.assertEqual([task['paper_id'] for task in plan['tasks']], ids)
        self.assertEqual(max((task['batch'] for task in plan['tasks'])), 2)
        self.assertTrue(plan['merge']['requires_all'])

    def test_failed_agent_falls_back_to_main(self):
        plan = plan_tasks(depth='full', page_counts={'paper01': 30}, subagents_supported=True)
        recovery = recovery_plan(plan, ['1'])
        self.assertEqual(recovery['mode'], 'serial_recovery')
        self.assertEqual(len(recovery['tasks']), 1)
        self.assertEqual(recovery['tasks'][0]['executor'], 'main')
        self.assertTrue(recovery['evidence_verification_required'])

    def test_simple_question_does_not_spawn_agents(self):
        plan = plan_tasks(task='ask', depth='full', page_counts={'paper01': 30}, subagents_supported=True)
        self.assertEqual(plan['mode'], 'serial')

class ValidateTests(unittest.TestCase):

    def setUp(self):
        contracts = read_json(SCRIPTS.parent / 'assets' / 'contracts.json')
        self.schema = contracts['reading_note_schema']
        self.note = note_fixture()
        self.evidence = verify_evidence(self.note, sections_fixture())

    def test_valid_note_passes_schema_and_evidence(self):
        result = validate_note(self.note, self.schema, self.evidence)
        self.assertTrue(result['valid'])

    def test_missing_required_field_fails(self):
        del self.note['research_question']
        result = validate_note(self.note, self.schema, self.evidence)
        self.assertFalse(result['valid'])
        self.assertTrue(any(('research_question' in error for error in result['errors'])))

    def test_invalid_statement_type_fails(self):
        self.note['claims'][0]['statement_type'] = 'certain_fact'
        self.assertFalse(validate_note(self.note, self.schema)['valid'])

    def test_invalid_evidence_report_fails(self):
        self.evidence['valid'] = False
        result = validate_note(self.note, self.schema, self.evidence)
        self.assertFalse(result['valid'])
        self.assertIn('证据校验未通过', result['errors'])

    def test_full_note_requires_claims(self):
        self.note['claims'] = []
        result = validate_note(self.note, self.schema)
        self.assertFalse(result['valid'])

    def test_boolean_is_not_integer_page(self):
        self.note['claims'][0]['evidence'][0]['page'] = True
        result = validate_note(self.note, self.schema)
        self.assertFalse(result['valid'])

class ExportAndPackageTests(unittest.TestCase):

    def test_markdown_contains_claim_and_evidence(self):
        text = render_markdown(note_fixture())
        self.assertIn('TinyNet 使用门控编码器', text)
        self.assertIn('第 2 页', text)
        self.assertIn('paper_fact', text)

    def test_package_checker_uses_actual_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            result = check_skill(root)
            self.assertFalse(result['valid'])
            self.assertEqual(result['file_count'], 0)
            self.assertTrue(result['within_40_mib'])
            self.assertFalse(result['approximately_25_files'])

            cache = root / '__pycache__'
            cache.mkdir()
            (cache / 'stale.pyc').write_bytes(b'cache')
            cached_result = check_skill(root)
            self.assertEqual(cached_result['file_count'], 1)
            self.assertEqual(cached_result['size_bytes'], 5)
            self.assertEqual(cached_result['counting_scope'], 'all_files_including_caches')

            (cache / 'stale.pyc').unlink()
            cache.rmdir()
            git_dir = root / '.git'
            git_dir.mkdir()
            (git_dir / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8')
            git_result = check_skill(root)
            self.assertEqual(git_result['file_count'], 0)

    def test_current_skill_has_required_files(self):
        result = check_skill(SCRIPTS.parent)
        self.assertTrue(result['valid'], result)
        self.assertTrue(result['approximately_25_files'])
        self.assertEqual(result['file_count'], 30)
        self.assertTrue(result['within_40_mib'])

class CliAndEvaluationTests(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *arguments):
        stdout = io.StringIO()
        argv = ['paper_tools.py', *map(str, arguments)]
        with patch.object(sys, 'argv', argv), redirect_stdout(stdout):
            exit_code = paper_tools.main()
        return exit_code, stdout.getvalue()

    def test_full_corpus_evaluation(self):
        summary = run_evaluation(ROOT / 'assets' / 'examples' / 'test-cases.jsonl', self.root)
        self.assertEqual(summary['total_cases'], 8)
        self.assertEqual(summary['parse_passed'], 8)
        self.assertEqual(summary['notes_evidence_passed'], 6)
        self.assertEqual(summary['notes_validation_passed'], 6)
        self.assertEqual(summary['matched_evidence'], 72)
        self.assertEqual(summary['manual_review_average_score'], 95.5)
        self.assertTrue(evaluation_passed(summary))
        self.assertTrue((self.root / 'summary.json').is_file())

    def test_evaluation_rejects_parse_evidence_and_review_failures(self):
        self.assertFalse(evaluation_passed({'parse_passed': 0, 'total_cases': 1, 'results': []}))
        evidence_failure = {
            'parse_passed': 1,
            'total_cases': 1,
            'results': [{'note_expected': True, 'evidence_passed': False, 'validation_passed': True}],
        }
        self.assertFalse(evaluation_passed(evidence_failure))
        review_failure = {
            'parse_passed': 1,
            'total_cases': 1,
            'results': [{'note_expected': False, 'manual_review_passed': False}],
        }
        self.assertFalse(evaluation_passed(review_failure))

    def test_package_checker_reports_invalid_json_and_jsonl(self):
        (self.root / 'broken.json').write_text('{', encoding='utf-8')
        (self.root / 'broken.jsonl').write_text('{}\nnot-json\n', encoding='utf-8')
        result = check_skill(self.root)
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['invalid_data_files']), 2)
        self.assertEqual({item['file'] for item in result['invalid_data_files']}, {'broken.json', 'broken.jsonl'})

    def test_cli_pdf_workflow_and_plan(self):
        pdf = self.root / 'input.pdf'
        document = fitz.open()
        for index in range(2):
            page = document.new_page()
            text = (
                'Abstract\nWe evaluate TinyNet on DatasetA with repeated controls and report classification accuracy.\n'
                'Introduction\nEfficient classification remains difficult and requires controlled experiments.\n'
                if index == 0 else
                'Method\nTinyNet uses a gated encoder with a compact classifier and controlled information flow.\n'
                'Experiments\nAccuracy improves from 70.0 to 72.5 on DatasetA across repeated runs.\n'
                'Conclusion\nThe results support efficiency on DatasetA.'
            )
            page.insert_text((50, 50), text, fontsize=10)
        document.save(pdf)
        document.close()

        inspect_path = self.root / 'inspect.json'
        pages_path = self.root / 'pages.json'
        sections_path = self.root / 'sections.json'
        context_path = self.root / 'context.json'
        plan_path = self.root / 'plan.json'
        self.assertEqual(self.run_cli('inspect', pdf, '--output', inspect_path)[0], 0)
        self.assertEqual(self.run_cli('extract', pdf, '--output', pages_path)[0], 0)
        self.assertEqual(self.run_cli('sectionize', pages_path, '--output', sections_path)[0], 0)
        self.assertEqual(self.run_cli('context', sections_path, '--task', 'read', '--output', context_path)[0], 0)
        code, stdout = self.run_cli(
            'plan', '--paper-ids', 'paper01', '--page-count', '30',
            '--subagents-supported', '--output', plan_path,
        )
        self.assertEqual(code, 0)
        self.assertIn('"parallel"', stdout)
        self.assertEqual(read_json(inspect_path)['status'], 'ok')
        self.assertEqual(read_json(pages_path)['page_count'], 2)
        self.assertGreaterEqual(len(read_json(sections_path)['sections']), 4)
        self.assertGreater(len(read_json(context_path)['contexts']), 0)
        self.assertEqual(read_json(plan_path)['mode'], 'parallel')

    def test_cli_verify_validate_and_all_export_formats(self):
        sections_path = self.root / 'sections.json'
        note_path = self.root / 'note.json'
        evidence_path = self.root / 'evidence.json'
        validation_path = self.root / 'validation.json'
        write_json(sections_path, sectionize(sections_fixture()))
        write_json(note_path, note_fixture())

        self.assertEqual(self.run_cli(
            'verify', note_path, '--sections', sections_path, '--output', evidence_path,
        )[0], 0)
        self.assertEqual(self.run_cli(
            'validate', note_path, '--schema', ROOT / 'assets' / 'contracts.json',
            '--evidence-report', evidence_path, '--output', validation_path,
        )[0], 0)
        self.assertTrue(read_json(evidence_path)['valid'])
        self.assertTrue(read_json(validation_path)['valid'])

        for export_format in ('markdown', 'json', 'csv'):
            output = self.root / f'note.{export_format if export_format != "markdown" else "md"}'
            self.assertEqual(self.run_cli(
                'export', note_path, '--format', export_format, '--output', output,
            )[0], 0)
            content = output.read_text(encoding='utf-8-sig')
            self.assertIn('TinyNet', content)
            self.assertIn('gated encoder', content)

    def test_cli_returns_nonzero_for_unreadable_pdf(self):
        output = self.root / 'unreadable.json'
        code, _ = self.run_cli('inspect', self.root / 'missing.pdf', '--output', output)
        self.assertEqual(code, 2)
        self.assertEqual(read_json(output)['status'], 'unreadable')


if __name__ == "__main__":
    unittest.main()
