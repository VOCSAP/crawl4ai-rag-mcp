import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from crawl4ai_mcp import smart_chunk_markdown

SYSTEM_PROMPT = "You are a helpful assistant that provides concise contextual information."


def contextual_messages(full_document: str, chunk: str, doc_truncation: int) -> list[dict[str, str]]:
    prompt = f"""<document>
{full_document[:doc_truncation]}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{chunk}
</chunk>
Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def language_of(text: str) -> str:
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿñæœ]+", text.casefold())
    french = {"le", "la", "les", "des", "une", "dans", "avec", "pour", "sur", "est", "ce", "cette", "qui", "et", "ou"}
    english = {"the", "and", "of", "in", "to", "for", "with", "is", "this", "that", "from", "or", "on"}
    french_score = sum(word in french for word in words)
    english_score = sum(word in english for word in words)
    if french_score == english_score:
        return "unknown"
    return "fr" if french_score > english_score else "en"


def format_issues(context: str, expected_language: str | None, max_chars: int) -> list[str]:
    issues: list[str] = []
    stripped = context.strip()
    if not stripped:
        issues.append("empty")
    if len(stripped) > max_chars:
        issues.append("too_long")
    if re.match(r"^(here is|context:|contexte:|the context is|sure[,:!]?)\b", stripped, re.IGNORECASE):
        issues.append("preamble")
    if expected_language:
        actual_language = language_of(stripped)
        if actual_language != expected_language:
            issues.append(f"language:{actual_language}")
    return issues
