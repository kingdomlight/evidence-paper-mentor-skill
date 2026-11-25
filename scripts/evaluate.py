import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

from paper_tools import extract_pdf, inspect_pdf, prepare_context, read_json, sectionize
from paper_tools import validate_note, verify_evidence, write_json

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = [
    "SKILL.md",
    "scripts/paper_tools.py",
    "scripts/evaluate.py",
    "scripts/test_skill.py",
    "scripts/requirements.txt",
    "references/reading-guide.md",
    "references/quality-and-agents.md",
    "references/test-report.md",
    "assets/contracts.json",
    "assets/templates.md",
    "assets/examples/manifest.csv",
    "assets/examples/test-cases.jsonl",
    "assets/examples/benchmark.json",
    "assets/examples/review-pages.pdf",

    "assets/examples/outputs/notes.json",
    "assets/examples/outputs/showcase.md",
] + [f"assets/examples/papers/paper{index:02d}.pdf" for index in range(1, 9)]


def run_evaluation(cases_path, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    with Path(cases_path).open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                cases.append(json.loads(line))
    bundle = read_json(ROOT / "assets" / "examples" / "outputs" / "notes.json")
    notes = {note["paper_id"]: note for note in bundle["notes"]}
    benchmark = read_json(ROOT / "assets" / "examples" / "benchmark.json")
    reviews = {review["paper_id"]: review for review in benchmark["reviews"]}
    schema = read_json(ROOT / "assets" / "contracts.json")["reading_note_schema"]
    results = []
    for case in cases:
        paper_id = case["paper_id"]
        pdf = ROOT / case["pdf"]
        item = {
            "paper_id": paper_id,
            "expected_status": case.get("expected_status", "ok"),
            "note_expected": bool(case.get("note_expected")),
        }
        try:
            inspection = inspect_pdf(pdf)
            item["actual_status"] = inspection["status"]
            item["parse_passed"] = inspection["status"] == item["expected_status"]
            if inspection["status"] == "ok":
                pages = extract_pdf(pdf)
                sections = sectionize(pages)
                context = prepare_context(sections)
                item["page_count"] = pages["page_count"]
                item["section_count"] = len(sections["sections"])
                item["contexts"] = len(context["contexts"])
                if item["note_expected"]:
                    note = notes[paper_id]
                    evidence = verify_evidence(note, sections)
                    validation = validate_note(note, schema, evidence)
                    item["evidence_passed"] = evidence["valid"]
                    item["validation_passed"] = validation["valid"]
                    item["matched_evidence"] = evidence["matched_evidence"]
                    item["total_evidence"] = evidence["total_evidence"]
                    if not evidence["valid"]:
                        item["evidence_errors"] = evidence["errors"]
                    if not validation["valid"]:
                        item["validation_errors"] = validation["errors"]
        except Exception as exc:
            item["parse_passed"] = False
            item["error"] = str(exc)
        review = reviews.get(paper_id)
        if item["note_expected"] and review:
            item["manual_review_status"] = review["status"]
            item["manual_review_score"] = review["total"]
            item["manual_review_passed"] = review["passed"]
        else:
            item["manual_review_status"] = "not_applicable"
        results.append(item)
    completed_scores = [item["manual_review_score"] for item in results if item.get("manual_review_status") == "completed"]
    summary = {
        "total_cases": len(results),
        "parse_passed": sum(1 for item in results if item.get("parse_passed")),
        "notes_expected": sum(1 for item in results if item["note_expected"]),
        "notes_evidence_passed": sum(1 for item in results if item.get("evidence_passed")),
        "notes_validation_passed": sum(1 for item in results if item.get("validation_passed")),
        "matched_evidence": sum(item.get("matched_evidence", 0) for item in results),
        "total_evidence": sum(item.get("total_evidence", 0) for item in results),
        "manual_reviews_completed": len(completed_scores),
        "manual_reviews_passed": sum(1 for item in results if item.get("manual_review_passed") is True),
        "manual_review_average_score": round(sum(completed_scores) / len(completed_scores), 2) if completed_scores else None,
        "results": results,
    }
    write_json(output / "summary.json", summary)
    return summary


def check_skill(root):
    root = Path(root).resolve()
    # 主办方按提交包中的全部文件计数和计量，缓存文件也不得排除。
    git_dir = root / ".git"
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not (git_dir in path.parents)
    )
    size = sum(path.stat().st_size for path in files)
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    invalid_data_files = []
    for path in files:
        try:
            if path.suffix == ".json":
                with path.open("r", encoding="utf-8-sig") as handle:
                    json.load(handle)
            elif path.suffix == ".jsonl":
                with path.open("r", encoding="utf-8-sig") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if line.strip():
                            try:
                                json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise ValueError(f"第 {line_number} 行: {exc}") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            invalid_data_files.append({"file": path.relative_to(root).as_posix(), "error": str(exc)})
    within_count = len(files) <= 30
    within_size = size <= 40 * 1024 * 1024
    return {
        "root": str(root),
        "valid": not missing and not invalid_data_files and within_count and within_size,
        "missing_required_files": missing,
        "invalid_data_files": invalid_data_files,
        "file_count": len(files),
        "maximum_file_count": 30,
        "approximately_25_files": 20 <= len(files) <= 30,
        "size_bytes": size,
        "size_mib": round(size / (1024 * 1024), 3),
        "within_40_mib": within_size,
        "maximum_size_mib": 40,
        "counting_scope": "all_files_including_caches",
    }


def evaluation_passed(summary):
    if summary["parse_passed"] != summary["total_cases"]:
        return False
    for item in summary["results"]:
        if item["note_expected"] and not (item.get("evidence_passed") and item.get("validation_passed")):
            return False
        if item.get("manual_review_passed") is False:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="运行 PaperMentor 评估并检查提交限制")
    parser.add_argument("--cases", default=str(ROOT / "assets" / "examples" / "test-cases.jsonl"))
    parser.add_argument("--output-dir", default=str(Path(tempfile.gettempdir()) / "paper-mentor-test-results"))
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    passed = True
    if not args.package_only:
        summary = run_evaluation(args.cases, args.output_dir)
        passed = evaluation_passed(summary)
        print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))
    package = check_skill(ROOT)
    write_json(Path(args.output_dir) / "package-report.json", package)
    print(json.dumps(package, ensure_ascii=False))
    return 0 if passed and package["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())