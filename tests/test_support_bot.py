"""Тести support-бота (Фаза 1, RAG doc-QA — спека §6 acceptance).

Chunking і retrieval тестуються офлайн (без API). LLM-виклик у `ask()`
інжектимо через `llm=` — фінальну відповідь моделі не мокаємо в мережу.
"""

from __future__ import annotations

from pathlib import Path

from server.support_bot import (
    Retriever,
    ask,
    build_context,
    chunk_markdown,
    discover_docs,
    load_chunks,
)

_ROOT = Path(__file__).resolve().parents[1]


# --- chunking ----------------------------------------------------------------


def test_chunk_by_h2(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(
        "# Заголовок\n\nвступ\n\n## Перша\nтекст один\n\n## Друга\nтекст два\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(md, root=tmp_path)
    headings = [c.heading for c in chunks]
    assert "Перша" in headings and "Друга" in headings
    first = next(c for c in chunks if c.heading == "Перша")
    assert "текст один" in first.text


def test_decisions_each_bullet_is_a_chunk(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    dec = docs / "DECISIONS.md"
    dec.write_text(
        "# Журнал рішень\n\nФормат: дата · рішення.\n\n---\n\n"
        "- **2026-07-31 · Рішення А.** Пояснення А,\n  продовження А.\n\n"
        "- **2026-07-31 · Рішення Б.** Пояснення Б.\n\n"
        "- **2026-07-31 · Рішення В.** Пояснення В.\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(dec, root=tmp_path)
    assert len(chunks) == 3
    assert all(c.text.startswith("- ") for c in chunks)
    assert chunks[0].heading == "2026-07-31 · Рішення А."
    # багаторядковий bullet лишається одним чанком
    assert "продовження А" in chunks[0].text


def test_long_h2_section_split_by_h3(tmp_path):
    md = tmp_path / "big.md"
    filler = ("слово " * 1600).strip()
    md.write_text(
        f"## Велика секція\n\n### Підрозділ 1\n{filler}\n\n### Підрозділ 2\nхвіст\n",
        encoding="utf-8",
    )
    chunks = chunk_markdown(md, root=tmp_path)
    headings = [c.heading for c in chunks]
    assert any("Підрозділ 1" in h for h in headings)
    assert any("Підрозділ 2" in h for h in headings)


# --- discovery (динамічний скан, acceptance #4) ------------------------------


def test_discovery_picks_up_new_spec_without_code_change(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# c", encoding="utf-8")
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "existing.md").write_text("## a\nx", encoding="utf-8")
    found = discover_docs(tmp_path)
    assert (tmp_path / "CLAUDE.md") in found
    assert (specs / "existing.md") in found
    # додаємо новий файл — підхоплюється без зміни коду
    (specs / "new_phase.md").write_text("## b\ny", encoding="utf-8")
    assert (specs / "new_phase.md") in discover_docs(tmp_path)


# --- retrieval (BM25, офлайн, реальний корпус репо) --------------------------


def test_retrieval_surfaces_current_decision_over_old_spec():
    # acceptance #3: питання про element_index -> актуальне рішення у DECISIONS.md
    top = Retriever(load_chunks(_ROOT)).search("element_index у PreflightIssue", k=5)
    assert any(
        c.file_path == "docs/DECISIONS.md" and "element_index" in c.text for c in top
    )


def test_retrieval_surfaces_canonical_schema():
    # acceptance #1: канонічна CanvasJSON схема -> вказівка на server/schema.py
    top = Retriever(load_chunks(_ROOT)).search("яка канонічна CanvasJSON схема", k=5)
    assert any("schema.py" in c.text for c in top)


# --- ask() з інжектованим LLM ------------------------------------------------


def test_ask_builds_context_with_sources_and_calls_llm():
    captured: dict[str, str] = {}

    def fake_llm(question: str, context: str) -> str:
        captured["q"] = question
        captured["ctx"] = context
        return "ВІДПОВІДЬ"

    out = ask("чому CMYK через Ghostscript?", root=_ROOT, llm=fake_llm)
    assert out == "ВІДПОВІДЬ"
    assert captured["q"] == "чому CMYK через Ghostscript?"
    assert "[файл:" in captured["ctx"]  # джерела у контексті


def test_build_context_empty_is_safe():
    assert build_context([]) == ""
