import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path


def read_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)

def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

def normalize_text(value):
    value = unicodedata.normalize('NFKC', value or '')
    value = value.replace('-\n', '').replace('\xad', '')
    return re.sub('\\s+', ' ', value).strip()

def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def require_fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('缺少 PyMuPDF，请安装 scripts/requirements.txt 中的依赖') from exc
    return fitz

def inspect_pdf(path, max_pages=200):
    source = Path(path)
    result = {'file': source.name, 'status': 'unreadable', 'warnings': [], 'sha256': None, 'page_count': 0, 'character_count': 0, 'metadata': {}}
    if not source.is_file():
        result['error'] = '文件不存在或不是普通文件'
        return result
    result['size_bytes'] = source.stat().st_size
    result['sha256'] = file_sha256(source)
    try:
        fitz = require_fitz()
        document = fitz.open(source)
        if document.needs_pass:
            result['status'] = 'encrypted'
            result['error'] = 'PDF 需要密码'
            document.close()
            return result
        result['page_count'] = document.page_count
        result['metadata'] = {key: value for key, value in (document.metadata or {}).items() if value}
        characters = 0
        for page in document:
            characters += len(normalize_text(page.get_text('text', sort=True)))
        result['character_count'] = characters
        if document.page_count == 0:
            result['status'] = 'empty'
            result['error'] = 'PDF 没有页面'
        elif characters < 100:
            result['status'] = 'insufficient_text'
            result['error'] = '可提取正文过少，可能是扫描件'
        else:
            result['status'] = 'ok'
        if document.page_count > max_pages:
            result['warnings'].append(f'页数超过默认建议值 {max_pages}')
        document.close()
    except Exception as exc:
        result['error'] = str(exc)
        if 'PyMuPDF' in str(exc):
            result['status'] = 'dependency_missing'
    return result

def extract_pdf(path):
    source = Path(path)
    fitz = require_fitz()
    document = fitz.open(source)
    if document.needs_pass:
        document.close()
        raise ValueError('PDF 需要密码')
    pages = []
    warnings = []
    for index, page in enumerate(document):
        text = page.get_text('text', sort=False)
        normalized = normalize_text(text)
        blocks = []
        raw = page.get_text('dict', sort=False)
        for block in raw.get('blocks', []):
            if block.get('type') != 0:
                continue
            lines = []
            sizes = []
            for line in block.get('lines', []):
                line_text = ''.join((span.get('text', '') for span in line.get('spans', [])))
                if line_text.strip():
                    lines.append(line_text)
                sizes.extend((span.get('size', 0) for span in line.get('spans', [])))
            block_text = normalize_text('\n'.join(lines))
            if block_text:
                blocks.append({'bbox': [round(float(value), 2) for value in block.get('bbox', [0, 0, 0, 0])], 'text': block_text, 'max_font_size': round(max(sizes, default=0), 2)})
        if len(normalized) < 100:
            warnings.append(f'第 {index + 1} 页可提取文本过少')
        pages.append({'page': index + 1, 'width': round(float(page.rect.width), 2), 'height': round(float(page.rect.height), 2), 'text': text, 'normalized_text': normalized, 'character_count': len(normalized), 'blocks': blocks})
    metadata = {key: value for key, value in (document.metadata or {}).items() if value}
    result = {'paper_id': source.stem, 'title': metadata.get('title', source.stem), 'source_file': source.name, 'sha256': file_sha256(source), 'page_count': len(pages), 'character_count': sum((page['character_count'] for page in pages)), 'status': 'ok' if sum((page['character_count'] for page in pages)) >= 100 else 'insufficient_text', 'metadata': metadata, 'warnings': warnings, 'pages': pages}
    document.close()
    return result

HEADING_PATTERN = re.compile('^(?:(?:[IVX]+|\\d+(?:\\.\\d+)*)[.)]?\\s+)?([A-Za-z][A-Za-z /-]{1,55}|[\\u4e00-\\u9fff]{2,12})$')

HEADINGS = {'abstract': 'Abstract', '摘要': 'Abstract', 'introduction': 'Introduction', '引言': 'Introduction', '介绍': 'Introduction', 'related work': 'Related Work', 'related works': 'Related Work', '相关工作': 'Related Work', 'background': 'Background', '背景': 'Background', 'method': 'Method', 'methods': 'Method', 'methodology': 'Method', 'approach': 'Method', 'proposed method': 'Method', 'our approach': 'Method', '方法': 'Method', '研究方法': 'Method', 'experiments': 'Experiments', 'experiment': 'Experiments', 'experimental setup': 'Experiments', 'experimental results': 'Experiments', '实验': 'Experiments', '实验设计': 'Experiments', 'results': 'Results', 'results and discussion': 'Results', '结果': 'Results', '实验结果': 'Results', 'discussion': 'Discussion', '讨论': 'Discussion', 'conclusion': 'Conclusion', 'conclusions': 'Conclusion', '结论': 'Conclusion', '总结': 'Conclusion', 'limitations': 'Limitations', 'limitation': 'Limitations', '局限性': 'Limitations', 'references': 'References', 'reference': 'References', '参考文献': 'References', 'appendix': 'Appendix', '附录': 'Appendix'}

def canonical_heading(line):
    candidate = normalize_text(line).strip(' :：')
    if not candidate or len(candidate) > 70:
        return None
    match = HEADING_PATTERN.match(candidate)
    if not match:
        return None
    label = match.group(1).strip().lower().rstrip(':')
    return HEADINGS.get(label)

def sectionize(pages_data):
    sections = []
    current = {'name': 'Front Matter', 'page_start': 1, 'page_end': 1, 'segments': []}
    sections.append(current)
    for page in pages_data.get('pages', []):
        page_number = page['page']
        buckets = {id(section): section for section in sections}
        current = sections[-1]
        for raw_line in page.get('text', '').splitlines():
            line = raw_line.strip()
            heading = canonical_heading(line)
            if heading and heading != current['name']:
                current = {'name': heading, 'page_start': page_number, 'page_end': page_number, 'segments': []}
                sections.append(current)
            if not current['segments'] or current['segments'][-1]['page'] != page_number:
                current['segments'].append({'page': page_number, 'text': ''})
            current['segments'][-1]['text'] += raw_line + '\n'
            current['page_end'] = page_number
        if not current['segments'] and page.get('text'):
            current['segments'].append({'page': page_number, 'text': page['text']})
        buckets.clear()
    populated = []
    for section in sections:
        if not section['segments']:
            continue
        section['text'] = ''.join((segment['text'] for segment in section['segments']))
        section['character_count'] = len(normalize_text(section['text']))
        populated.append(section)
    names = [section['name'] for section in populated]
    warnings = []
    if not any((name in {'Abstract', 'Introduction', 'Method', 'Experiments'} for name in names)):
        warnings.append('未识别到常见论文章节，已保留页级文本')
    return {'paper_id': pages_data.get('paper_id', 'paper'), 'title': pages_data.get('title', ''), 'source_file': pages_data.get('source_file', ''), 'sha256': pages_data.get('sha256'), 'status': pages_data.get('status', 'ok'), 'page_count': pages_data.get('page_count', 0), 'warnings': pages_data.get('warnings', []) + warnings, 'sections': populated, 'pages': pages_data.get('pages', [])}

PRIORITY = {'Abstract': 0, 'Introduction': 1, 'Method': 2, 'Experiments': 3, 'Results': 4, 'Discussion': 5, 'Conclusion': 6, 'Limitations': 7, 'Related Work': 8, 'Background': 9, 'Front Matter': 10, 'Appendix': 11, 'References': 20}

def split_text(text, limit):
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip('\n')
    return chunks

def prepare_context(data, task='read', query='', max_characters=24000):
    sections = list(data.get('sections', []))
    if task != 'references':
        sections = [section for section in sections if section['name'] != 'References']
    query_terms = [term.lower() for term in query.split() if term.strip()]

    def rank(section):
        score = PRIORITY.get(section['name'], 15)
        if task == 'ask' and query_terms:
            haystack = (section['name'] + ' ' + section.get('text', '')).lower()
            score -= sum((2 for term in query_terms if term in haystack))
        return score
    selected = []
    omitted = []
    used = 0
    for section in sorted(sections, key=rank):
        if used >= max_characters:
            omitted.append(section['name'])
            continue
        section_used = False
        for segment in section.get('segments', []):
            for text in split_text(segment.get('text', ''), min(6000, max_characters)):
                if not text.strip():
                    continue
                if used + len(text) > max_characters:
                    omitted.append(section['name'])
                    continue
                selected.append({'paper_id': data.get('paper_id'), 'section': section['name'], 'page_start': segment['page'], 'page_end': segment['page'], 'pages': [segment['page']], 'text': text, 'character_count': len(text)})
                used += len(text)
                section_used = True
        if not section_used and section['name'] not in omitted:
            omitted.append(section['name'])
    selected.sort(key=lambda item: (item['page_start'], item['section']))
    return {'paper_id': data.get('paper_id'), 'task': task, 'query': query, 'status': data.get('status', 'ok'), 'contexts': selected, 'omitted_sections': sorted(set(omitted)), 'character_count': used, 'truncated': bool(omitted), 'warnings': data.get('warnings', [])}

def verify_evidence(note, sections_data):
    pages = {page['page']: normalize_text(page.get('text', '')) for page in sections_data.get('pages', [])}
    claims = note.get('claims', [])
    results = []
    errors = []
    matched = 0
    evidence_count = 0
    for claim_index, claim in enumerate(claims):
        evidences = claim.get('evidence', [])
        claim_result = {'claim_index': claim_index, 'statement': claim.get('statement', ''), 'valid': True, 'evidence': []}
        if not claim.get('statement', '').strip():
            claim_result['valid'] = False
            errors.append(f'论断 {claim_index} 缺少 statement')
        if not evidences:
            claim_result['valid'] = False
            errors.append(f'论断 {claim_index} 没有证据')
        for evidence_index, evidence in enumerate(evidences):
            evidence_count += 1
            page = evidence.get('page')
            quote = normalize_text(evidence.get('quote', ''))
            item = {'evidence_index': evidence_index, 'page': page, 'valid': True, 'error': ''}
            if not isinstance(page, int) or isinstance(page, bool) or page not in pages:
                item['valid'] = False
                item['error'] = '页码不存在或类型错误'
            elif not quote:
                item['valid'] = False
                item['error'] = '引文为空'
            elif len(quote) > 500:
                item['valid'] = False
                item['error'] = '引文超过 500 字符'
            elif quote not in pages[page]:
                item['valid'] = False
                item['error'] = '引文未在指定页面匹配'
            if item['valid']:
                matched += 1
            else:
                claim_result['valid'] = False
                errors.append(f"论断 {claim_index} 证据 {evidence_index}: {item['error']}")
            claim_result['evidence'].append(item)
        results.append(claim_result)
    return {'paper_id': note.get('paper_id'), 'valid': bool(claims) and (not errors), 'total_claims': len(claims), 'total_evidence': evidence_count, 'matched_evidence': matched, 'evidence_match_rate': round(matched / evidence_count, 4) if evidence_count else 0, 'claims': results, 'errors': errors, 'semantic_support_checked': False, 'manual_review_required': True}

def validate_value(value, schema, path, errors):
    expected = schema.get('type')
    type_checks = {'object': lambda item: isinstance(item, dict), 'array': lambda item: isinstance(item, list), 'string': lambda item: isinstance(item, str), 'integer': lambda item: isinstance(item, int) and (not isinstance(item, bool)), 'number': lambda item: isinstance(item, (int, float)) and (not isinstance(item, bool)), 'boolean': lambda item: isinstance(item, bool)}
    if expected and expected in type_checks and (not type_checks[expected](value)):
        errors.append(f'{path}: 应为 {expected}')
        return
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: 不在允许枚举中')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            errors.append(f'{path}: 字符串过短')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            errors.append(f'{path}: 字符串过长')
    if isinstance(value, (int, float)) and (not isinstance(value, bool)):
        if 'minimum' in schema and value < schema['minimum']:
            errors.append(f'{path}: 小于最小值')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append(f'{path}.{key}: 缺少必填字段')
        for key, child_schema in schema.get('properties', {}).items():
            if key in value:
                validate_value(value[key], child_schema, f'{path}.{key}', errors)
    if isinstance(value, list) and 'items' in schema:
        for index, item in enumerate(value):
            validate_value(item, schema['items'], f'{path}[{index}]', errors)

def validate_note(note, schema, evidence_report=None):
    errors = []
    validate_value(note, schema, '$', errors)
    if note.get('reading_depth') in {'full', 'group_meeting'} and (not note.get('claims')):
        errors.append('$.claims: 精读和组会模式至少需要一条关键论断')
    if evidence_report is not None and (not evidence_report.get('valid', False)):
        errors.append('证据校验未通过')
    return {'paper_id': note.get('paper_id'), 'valid': not errors, 'errors': errors, 'schema_checked': True, 'evidence_report_checked': evidence_report is not None, 'semantic_review_required': True}

def render_claims(note):
    lines = []
    for claim in note.get('claims', []):
        lines.append(f"- **[{claim.get('statement_type', 'unknown')}]** {claim.get('statement', '')}")
        for evidence in claim.get('evidence', []):
            section = evidence.get('section', claim.get('section', 'Unknown'))
            lines.append(f"  - 第 {evidence.get('page', '?')} 页，{section}：“{evidence.get('quote', '')}”")
    return '\n'.join(lines) or '无'

def render_markdown(note):
    methods = []
    for method in note.get('methods', []):
        steps = method.get('steps', [])
        if isinstance(steps, list):
            steps = '；'.join((str(step) for step in steps))
        methods.append(f"### {method.get('name', '未命名方法')}\n\n{steps}")
    experiments = []
    for experiment in note.get('experiments', []):
        experiments.append(f"### {experiment.get('name', '实验')}\n\n- 数据集或对象：{experiment.get('dataset', '未知')}\n- 基线：{experiment.get('baselines', '未知')}\n- 指标：{experiment.get('metrics', '未知')}\n- 结果：{experiment.get('results', '未知')}")
    limitations = []
    for item in note.get('limitations', []):
        if isinstance(item, dict):
            limitations.append(f"- [{item.get('type', 'unknown')}] {item.get('statement', item.get('text', ''))}")
        else:
            limitations.append(f'- {item}')
    warnings = '；'.join(note.get('warnings', [])) or '无'
    uncertainties = '\n'.join((f'- {item}' for item in note.get('uncertainties', []))) or '- 无'
    contributions = '\n'.join((f'- {item}' for item in note.get('contributions', []))) or '- 无'
    return f"# {note.get('title', note.get('paper_id', '论文精读'))}\n\n> 阅读深度：{note.get('reading_depth', 'full')}  \n> 覆盖范围：{note.get('coverage', '未知')}  \n> 解析警告：{warnings}\n\n## 一句话总结\n\n{note.get('summary', '')}\n\n## 研究问题\n\n{note.get('research_question', '')}\n\n## 方法拆解\n\n{chr(10).join(methods) or '无'}\n\n## 实验与结果\n\n{chr(10).join(experiments) or '无'}\n\n## 作者贡献\n\n{contributions}\n\n## 局限与复现风险\n\n{chr(10).join(limitations) or '无'}\n\n## 关键论断与证据\n\n{render_claims(note)}\n\n## 不确定项\n\n{uncertainties}\n"

def export_csv(notes, output):
    with Path(output).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['paper_id', 'title', 'statement', 'statement_type', 'section', 'pages', 'quotes'])
        for note in notes:
            for claim in note.get('claims', []):
                evidences = claim.get('evidence', [])
                writer.writerow([note.get('paper_id', ''), note.get('title', ''), claim.get('statement', ''), claim.get('statement_type', ''), claim.get('section', ''), ';'.join((str(item.get('page', '')) for item in evidences)), ' | '.join((item.get('quote', '') for item in evidences))])

def plan_tasks(task='read', depth='full', paper_ids=None, page_counts=None, subagents_supported=False):
    paper_ids = paper_ids or ['paper01']
    page_counts = page_counts or {paper_id: 0 for paper_id in paper_ids}
    if not subagents_supported or depth == 'quick' or task in {'ask', 'format', 'flashcards'}:
        return {'mode': 'serial', 'reason': '宿主不支持子 Agent，或任务拆分收益不足', 'tasks': [{'executor': 'main', 'paper_id': paper_id, 'task_type': task, 'sections': 'required'} for paper_id in paper_ids]}
    if task == 'compare' and len(paper_ids) > 1:
        return {'mode': 'parallel', 'reason': '多篇论文可独立生成论文卡片', 'tasks': [{'executor': 'subagent', 'paper_id': paper_id, 'task_type': 'single_paper', 'sections': 'required', 'batch': index // 3 + 1} for index, paper_id in enumerate(paper_ids)], 'merge': {'executor': 'main', 'task_type': 'compare', 'requires_all': True}}
    paper_id = paper_ids[0]
    if depth == 'full' and page_counts.get(paper_id, 0) >= 12:
        roles = ['method_reading', 'experiment_reading', 'critical_review']
        return {'mode': 'parallel', 'reason': '长论文的方法、实验和批判审阅可独立阅读', 'tasks': [{'executor': 'subagent', 'paper_id': paper_id, 'task_type': role, 'sections': 'assigned'} for role in roles], 'merge': {'executor': 'main', 'task_type': 'merge_and_verify', 'requires_all': True}}
    return {'mode': 'serial', 'reason': '短论文不拆分，避免额外编排开销', 'tasks': [{'executor': 'main', 'paper_id': paper_id, 'task_type': task, 'sections': 'required'}]}

def recovery_plan(plan, failed_task_ids):
    failed = set(failed_task_ids)
    recovery = []
    completed = []
    for index, task in enumerate(plan.get('tasks', [])):
        task_id = task.get('task_id', str(index))
        if task.get('executor') == 'subagent' and task_id in failed:
            item = dict(task)
            item.update({'executor': 'main', 'task_id': task_id, 'recovery': True})
            recovery.append(item)
        else:
            completed.append(task_id)
    return {'mode': 'serial_recovery', 'completed_task_ids': completed, 'tasks': recovery, 'evidence_verification_required': True}

def main():
    parser = argparse.ArgumentParser(description="PaperMentor deterministic PDF, evidence and orchestration tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ["inspect", "extract", "sectionize", "context", "verify", "validate", "export"]:
        command = subparsers.add_parser(name)
        command.add_argument("inputs", nargs="+")
        command.add_argument("--output", required=True)
        command.add_argument("--task", default="read")
        command.add_argument("--query", default="")
        command.add_argument("--max-characters", type=int, default=24000)
        command.add_argument("--sections")
        command.add_argument("--schema")
        command.add_argument("--evidence-report")
        command.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    plan = subparsers.add_parser("plan")
    plan.add_argument("--task", default="read")
    plan.add_argument("--depth", default="full")
    plan.add_argument("--paper-ids", nargs="+", default=["paper01"])
    plan.add_argument("--page-count", type=int, default=0)
    plan.add_argument("--subagents-supported", action="store_true")
    plan.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = plan_tasks(args.task, args.depth, args.paper_ids, {args.paper_ids[0]: args.page_count}, args.subagents_supported)
        write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "inspect":
        result = inspect_pdf(args.inputs[0])
        write_json(args.output, result)
    elif args.command == "extract":
        result = extract_pdf(args.inputs[0])
        write_json(args.output, result)
    elif args.command == "sectionize":
        result = sectionize(read_json(args.inputs[0]))
        write_json(args.output, result)
    elif args.command == "context":
        result = prepare_context(read_json(args.inputs[0]), args.task, args.query, args.max_characters)
        write_json(args.output, result)
    elif args.command == "verify":
        result = verify_evidence(read_json(args.inputs[0]), read_json(args.sections))
        write_json(args.output, result)
    elif args.command == "validate":
        schema = read_json(args.schema)
        if "reading_note_schema" in schema:
            schema = schema["reading_note_schema"]
        evidence = read_json(args.evidence_report) if args.evidence_report else None
        result = validate_note(read_json(args.inputs[0]), schema, evidence)
        write_json(args.output, result)
    else:
        notes = [read_json(path) for path in args.inputs]
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "markdown":
            target.write_text("\n\n---\n\n".join(render_markdown(note) for note in notes), encoding="utf-8")
            return 0
        if args.format == "json":
            write_json(target, notes[0] if len(notes) == 1 else notes)
            return 0
        export_csv(notes, target)
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("valid", result.get("status", "ok")) in {True, "ok"} else 2


if __name__ == "__main__":
    sys.exit(main())
