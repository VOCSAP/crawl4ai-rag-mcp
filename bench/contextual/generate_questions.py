import argparse
import json
from pathlib import Path

from common import read_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and validate reviewed retrieval questions from raw chunks.")
    parser.add_argument("--chunks", type=Path, default=Path("bench/contextual/data/chunks.jsonl"))
    parser.add_argument("--request-output", type=Path, default=Path("bench/contextual/data/question-request.md"))
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def question_chunks(chunks: list[dict[str, object]]) -> list[dict[str, object]]:
    return [record for record in chunks if record.get("question_eligible", True)]


def write_request(chunks: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "Generate exactly one realistic retrieval question for every raw content chunk below. "
            "Use only the raw chunk, not document-level context. Preserve the chunk language. "
            "Return JSONL only, one object per input in the form "
            '{"chunk_id":"...","question":"..."}.\n\n'
        )
        for record in question_chunks(chunks):
            handle.write(json.dumps({"chunk_id": record["chunk_id"], "language": record["language"], "chunk": record["chunk"]}, ensure_ascii=False))
            handle.write("\n")


def validate_questions(chunks: list[dict[str, object]], questions: list[dict[str, object]]) -> None:
    chunk_ids = {str(record["chunk_id"]) for record in question_chunks(chunks)}
    question_ids = [str(record.get("chunk_id", "")) for record in questions]
    missing = chunk_ids - set(question_ids)
    unexpected = set(question_ids) - chunk_ids
    duplicated = {question_id for question_id in question_ids if question_ids.count(question_id) > 1}
    empty = [str(record.get("chunk_id", "")) for record in questions if not str(record.get("question", "")).strip()]
    if missing or unexpected or duplicated or empty:
        details = []
        if missing:
            details.append(f"missing={sorted(missing)}")
        if unexpected:
            details.append(f"unexpected={sorted(unexpected)}")
        if duplicated:
            details.append(f"duplicated={sorted(duplicated)}")
        if empty:
            details.append(f"empty={sorted(empty)}")
        raise SystemExit("Invalid questions: " + "; ".join(details))
    print(f"validated {len(questions)} questions against {len(chunks)} corpus chunks")


def main() -> None:
    args = parse_args()
    chunks = read_jsonl(args.chunks)
    if args.limit:
        chunks = chunks[:args.limit]
    if not chunks:
        raise SystemExit("No chunks found.")
    targets = question_chunks(chunks)
    write_request(chunks, args.request_output)
    print(f"wrote question request for {len(targets)} chunks to {args.request_output}")
    if args.questions:
        validate_questions(chunks, read_jsonl(args.questions))


if __name__ == "__main__":
    main()
