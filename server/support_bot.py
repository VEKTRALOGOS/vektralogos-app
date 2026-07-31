"""Support-бот — внутрішній doc-QA по репозиторію (Фаза 1, RAG).

Це НЕ клієнтський Sales/Support Agent (той — Фаза 3, іншими джерелами). Тут —
інструмент для проєкту: «що ми вирішили про X», «яка схема CanvasJSON», «чому
SVG не в тракті». Джерела — лише публічні `.md` репо (STRATEGY/_ops приватні,
поза скоупом — спека §1, DECISIONS).

Тракт (без агентного циклу — це RAG, не ReAct):
    scan .md -> chunk (## / DECISIONS-bullets) -> BM25 top-k -> один Claude-виклик.

Retrieval — keyword BM25 (rank_bm25), не embeddings/pgvector: корпус — десятки
чанків, вектори тут оверінженіринг (спека §3). Перегляд — коли корпус виросте.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MODEL = "claude-opus-5"  # дефолт проєкту (DECISIONS.md)

# Корінь репо: server/support_bot.py -> parents[1].
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Директорії/файли джерел (спека §1). Динамічний скан: новий .md у docs/specs/
# підхоплюється без зміни коду (acceptance §6). Приватні STRATEGY.md/_ops — НЕ тут.
_DOC_GLOBS = (
    "CLAUDE.md",
    "README.md",
    "docs/DECISIONS.md",
    "docs/specs/*.md",
    "docs/prompts/*.md",
    "docs/research/*.md",  # порожньо зараз — підхопиться, коли з'явиться
)

_MAX_CHUNK_TOKENS = 1500  # довшу ## секцію додатково ріжемо по ### (спека §2)


@dataclass
class Chunk:
    """Атомарна одиниця сенсу для retrieval."""

    file_path: str  # відносний до кореня репо
    heading: str
    text: str


# --- Джерела -----------------------------------------------------------------


def discover_docs(root: Path = _REPO_ROOT) -> list[Path]:
    """Усі .md-джерела за списком globs, відсортовані й без дублікатів."""
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in _DOC_GLOBS:
        matches = [root / pattern] if "*" not in pattern else sorted(root.glob(pattern))
        for p in matches:
            if p.is_file() and p not in seen:
                seen.add(p)
                found.append(p)
    return found


# --- Chunking (спека §2) -----------------------------------------------------


def _approx_tokens(text: str) -> int:
    """Груба оцінка кількості токенів (≈ слова + пунктуація)."""
    return len(re.findall(r"\S+", text))


def _split_by_heading(body: str, level: int) -> list[tuple[str, str]]:
    """Ділить markdown на (heading, text) по заголовках рівня `level` (## або ###).

    Текст до першого заголовка потрапляє у секцію з heading="" (преамбула).
    """
    marker = "#" * level
    pat = re.compile(rf"^{marker} +(.*)$", re.M)
    sections: list[tuple[str, str]] = []
    matches = list(pat.finditer(body))
    if not matches or matches[0].start() > 0:
        preamble = body[: matches[0].start()] if matches else body
        if preamble.strip():
            sections.append(("", preamble.strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        heading = m.group(1).strip()
        text = body[m.start() : end].strip()
        sections.append((heading, text))
    return sections


def _chunk_decisions(rel_path: str, body: str) -> list[Chunk]:
    """DECISIONS.md — плаский список `- **дата · рішення.**`; кожен bullet = чанк."""
    chunks: list[Chunk] = []
    # Кожен top-level bullet починається з `- ` на початку рядка; збираємо до
    # наступного такого ж bullet (враховуючи багаторядкові продовження).
    parts = re.split(r"^(?=- )", body, flags=re.M)
    for part in parts:
        text = part.strip()
        if not text.startswith("- "):
            continue
        # heading — жирний заголовок рішення, якщо є: **...**
        m = re.search(r"\*\*(.+?)\*\*", text, re.S)
        heading = m.group(1).strip() if m else text[:60]
        chunks.append(Chunk(file_path=rel_path, heading=heading, text=text))
    return chunks


def chunk_markdown(path: Path, root: Path = _REPO_ROOT) -> list[Chunk]:
    """Розбиває один .md на чанки за правилами спеки §2."""
    rel_path = str(path.relative_to(root))
    body = path.read_text(encoding="utf-8")

    if path.name == "DECISIONS.md":
        return _chunk_decisions(rel_path, body)

    chunks: list[Chunk] = []
    for heading, text in _split_by_heading(body, level=2):
        if _approx_tokens(text) > _MAX_CHUNK_TOKENS:
            # Занадто довга ## секція — ріжемо по ### всередині неї.
            for sub_h, sub_t in _split_by_heading(text, level=3):
                h = f"{heading} — {sub_h}" if sub_h and heading else (sub_h or heading)
                chunks.append(Chunk(file_path=rel_path, heading=h, text=sub_t))
        else:
            chunks.append(Chunk(file_path=rel_path, heading=heading, text=text))
    return chunks


def load_chunks(root: Path = _REPO_ROOT) -> list[Chunk]:
    """Усі чанки з усіх джерел."""
    chunks: list[Chunk] = []
    for path in discover_docs(root):
        chunks.extend(chunk_markdown(path, root))
    return chunks


# --- Retrieval (BM25, спека §3) ----------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Прості токени: слова у нижньому регістрі (unicode — кирилиця включно)."""
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


class Retriever:
    """BM25 по чанках. Індекс будується один раз на конструкторі."""

    def __init__(self, chunks: list[Chunk]) -> None:
        from rank_bm25 import BM25Okapi

        self.chunks = chunks
        # BM25Okapi не любить порожній корпус; тримаємо хоча б один токен на чанк.
        corpus = [_tokenize(f"{c.heading}\n{c.text}") or [""] for c in chunks]
        self._bm25 = BM25Okapi(corpus)

    def search(self, query: str, k: int = 5) -> list[Chunk]:
        """Топ-k чанків за BM25-скорингом до `query`."""
        if not self.chunks:
            return []
        scores = self._bm25.get_scores(_tokenize(query) or [""])
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self.chunks[i] for i in ranked[:k]]


# --- Промпт + виклик Claude (спека §4) ---------------------------------------

SYSTEM = """Ти відповідаєш на питання про проєкт Vektralogos, спираючись ЛИШЕ на подані
фрагменти документації. Не використовуй жодних знань поза ними.

Правила:
- Якщо відповідь є у фрагментах — дай пряму відповідь, коротко, і вкажи
  джерело: [файл: docs/specs/....md, розділ: "..."].
- Якщо фрагментів недостатньо для впевненої відповіді — прямо скажи
  "У документації проєкту цього немає" замість вигадувати.
- Якщо фрагменти суперечать один одному (напр. стара спека і DECISIONS.md
  з новішим рішенням) — вкажи обидва і зазнач, який документ новіший
  (за датою у назві файлу чи в тексті), якщо це видно з чанків.
- Відповідай тією мовою, якою поставлено питання."""


def build_context(chunks: list[Chunk]) -> str:
    """Складає фрагменти у текст контексту для промпту, з джерелами."""
    blocks = []
    for c in chunks:
        header = f"[файл: {c.file_path}" + (f', розділ: "{c.heading}"]' if c.heading else "]")
        blocks.append(f"{header}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def _call_claude(question: str, context: str, *, max_tokens: int) -> str:
    from dotenv import load_dotenv

    from .reliability import account, retry

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не заданий (додай у .env)")

    import anthropic

    client = anthropic.Anthropic()
    user = (
        f"Фрагменти документації:\n\n{context}\n\n"
        f"Питання: {question}"
    )

    def _call():
        r = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        account(r)  # per-run token budget (Ф4b), no-op без активного бюджета
        return r

    transient = (anthropic.APIConnectionError, anthropic.APITimeoutError,
                 anthropic.RateLimitError, anthropic.InternalServerError)
    response = retry(_call, attempts=3, exceptions=transient)
    return "".join(b.text for b in response.content if b.type == "text").strip()


def ask(
    question: str,
    *,
    k: int = 5,
    root: Path = _REPO_ROOT,
    max_tokens: int = 1024,
    llm: Callable[[str, str], str] | None = None,
) -> str:
    """Питання -> top-k чанків -> відповідь Claude (текст).

    `llm` — сім для тестів (за замовчуванням реальний claude-opus-5); отримує
    (question, context) і повертає текст відповіді.
    """
    retriever = Retriever(load_chunks(root))
    top = retriever.search(question, k=k)
    context = build_context(top)
    if llm is not None:
        return llm(question, context)
    return _call_claude(question, context, max_tokens=max_tokens)
