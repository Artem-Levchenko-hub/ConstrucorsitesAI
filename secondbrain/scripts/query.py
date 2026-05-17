"""Query the compiled SecondBrain knowledge base."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from config import KNOWLEDGE_DIR, QA_DIR, now_iso
from llm_provider import run_llm_text
from utils import append_log_block, article_rel, ensure_structure, list_wiki_articles, load_state, save_state, slugify


def _load_knowledge_context(max_chars: int = 36000, snippet_chars: int = 2800) -> tuple[str, list[Path]]:
    articles = list_wiki_articles()
    chunks: list[str] = []
    total = 0
    for article in articles:
        rel = article_rel(article)
        text = article.read_text(encoding="utf-8")
        snippet = text[:snippet_chars]
        if len(text) > snippet_chars:
            snippet += "\n...(truncated)"
        block = f"### {rel}\n```markdown\n{snippet}\n```"
        if total + len(block) > max_chars and chunks:
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks), articles


def _run_llm_query(question: str, context: str) -> tuple[str, float]:
    prompt = f"""You are answering a fixed user question from a project wiki.
Do not ask for another question. Use only provided context.
If context is insufficient, say what is missing.
Prefer concise factual bullets and include wikilink citations.

QUESTION (answer this exact question):
{question}

KNOWLEDGE BASE CONTEXT:
{context}
"""
    result = run_llm_text(prompt, KNOWLEDGE_DIR.parent, max_turns=2)
    if result.ok:
        return result.text.strip(), float(result.cost_usd)
    return "", 0.0


def _run_llm_query_with_retry(question: str, context: str) -> tuple[str, float]:
    answer, cost = _run_llm_query(question, context)
    if answer.strip():
        return answer, cost
    compact_context = context[:12000]
    if compact_context.strip():
        return _run_llm_query(question, compact_context)
    return "", 0.0


def _keyword_score(question: str, text: str) -> int:
    q_tokens = [t for t in re.findall(r"[A-Za-zА-Яа-я0-9_]+", question.lower()) if len(t) > 2]
    t_low = text.lower()
    return sum(t_low.count(tok) for tok in q_tokens)


def _run_local_query(question: str, articles: list[Path]) -> str:
    ranked: list[tuple[int, Path]] = []
    for path in articles:
        text = path.read_text(encoding="utf-8")
        ranked.append((_keyword_score(question, text), path))
    ranked.sort(key=lambda x: x[0], reverse=True)
    top = [p for score, p in ranked[:3] if score > 0]
    if not top:
        top = [p for _s, p in ranked[:2]]

    lines = ["Локальный ответ (без LLM):", ""]
    for path in top:
        rel = article_rel(path)
        text = path.read_text(encoding="utf-8")
        snippet = "\n".join([ln for ln in text.splitlines() if ln.strip()][:8])
        lines.append(f"- [[{rel.removesuffix('.md')}]]")
        lines.append(f"  {snippet[:300]}")
    lines.append("")
    lines.append("Для более глубокого синтеза включите рабочий LLM backend (`cursor` CLI agent или `claude`).")
    return "\n".join(lines)


def _write_qa_file(question: str, answer: str) -> Path:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    base_slug = slugify(question)[:80]
    path = QA_DIR / f"{base_slug}.md"
    if path.exists():
        suffix = now_iso().replace(":", "-")
        path = QA_DIR / f"{base_slug}-{suffix}.md"
    now = now_iso()
    body = (
        "---\n"
        f'title: "Q: {question}"\n'
        f'question: "{question}"\n'
        f"filed: {now[:10]}\n"
        "---\n\n"
        f"# Q: {question}\n\n"
        "## Answer\n\n"
        f"{answer}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def _append_index_entry(path: Path, summary: str) -> None:
    index_path = KNOWLEDGE_DIR / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n| Article | Summary | Compiled From | Updated |\n| --- | --- | --- | --- |\n",
            encoding="utf-8",
        )
    line = f"| [[{article_rel(path).removesuffix('.md')}]] | {summary} | query.py --file-back | {now_iso()[:10]} |\n"
    with index_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _count_qa_articles() -> int:
    return len([p for p in QA_DIR.glob("*.md") if p.is_file()])


def main() -> int:
    ensure_structure()
    parser = argparse.ArgumentParser(description="Query SecondBrain knowledge")
    parser.add_argument("question", help="Question to ask the knowledge base")
    parser.add_argument("--file-back", action="store_true", help="Store answer as QA page")
    args = parser.parse_args()

    total_before = len(list_wiki_articles())
    qa_before = _count_qa_articles()
    context, articles = _load_knowledge_context()
    answer, cost = _run_llm_query_with_retry(args.question, context)
    if not answer.strip():
        answer = _run_local_query(args.question, articles)
    print(answer)

    if args.file_back:
        qa_file = _write_qa_file(args.question, answer)
        _append_index_entry(qa_file, "Filed query answer")
        total_after = len(list_wiki_articles())
        qa_after = _count_qa_articles()
        qa_created = max(0, qa_after - qa_before)
        append_log_block(
            f"[{now_iso()}] query | {args.question}",
            [
                f"Filed to: [[{article_rel(qa_file).removesuffix('.md')}]]",
                f"Wiki totals: before={total_before}, after={total_after}, delta={total_after - total_before}",
                f"QA notes: before={qa_before}, after={qa_after}, added={qa_created}",
            ],
        )
        print(f"\nSaved to {qa_file}")
    else:
        append_log_block(
            f"[{now_iso()}] query | {args.question}",
            [
                "Query executed without file-back",
                f"Wiki articles available: {total_before}",
                f"QA notes currently: {qa_before}",
            ],
        )

    state = load_state()
    state["query_count"] = int(state.get("query_count", 0)) + 1
    state["total_cost"] = float(state.get("total_cost", 0.0)) + float(cost)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

