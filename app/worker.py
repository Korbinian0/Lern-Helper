import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from arq.connections import RedisSettings
from ollama import AsyncClient
from sqlalchemy import select

from app.config import OLLAMA_DEFAULT_URL, REDIS_URL
from app.database import SessionLocal
from app.models import Card, Job, JobStatus, Setting
from app.services import extract_document, make_chunks, parse_cards


DEFAULTS = {
    "ollama_url": OLLAMA_DEFAULT_URL,
    "model": "llama3.2:3b",
    "embedding_model": "nomic-embed-text",
    "ocr_language": "deu+eng",
    "chunk_size": "500",
    "cards_per_chunk": "5",
    "output_format": "anki_csv",
    "temperature": "0.3",
    "context_length": "4096",
}


def load_settings(db) -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update({row.key: row.value for row in db.scalars(select(Setting)).all()})
    return values


async def process_job(ctx, job_id: str) -> None:
    started = time.monotonic()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = JobStatus.PROCESSING
        job.progress_text = "Dokument wird gelesen"
        job.started_at = datetime.now(timezone.utc)
        settings = load_settings(db)
        job.model = settings["model"]
        job.chunk_size = int(settings["chunk_size"])
        db.commit()
        path = Path(job.stored_path)

    try:
        def update_page_progress(page_number: int, total_pages: int) -> None:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job:
                    job.progress = int((page_number - 1) / max(total_pages, 1) * 10)
                    job.progress_text = f"Seite {page_number}/{total_pages} wird gelesen"
                    db.commit()

        pages, page_count = await asyncio.to_thread(
            extract_document, path, settings["ocr_language"], update_page_progress
        )
        text = "\n\n".join(page for page in pages if page)
        if not text.strip():
            raise ValueError("Es wurde kein Text erkannt. Prüfe die Datei und die OCR-Sprache.")
        chunks = make_chunks(text, int(settings["chunk_size"]))
        client = AsyncClient(host=settings["ollama_url"])
        cards: list[tuple[str, str]] = []
        for index, chunk in enumerate(chunks, start=1):
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                job.page_count = page_count
                job.text_length = len(text)
                job.progress = int((index - 1) / max(len(chunks), 1) * 100)
                job.progress_text = f"Chunk {index}/{len(chunks)}"
                db.commit()
            prompt = (
                "Du bist ein Experte für Lernmaterialien.\n"
                f"Erstelle aus dem folgenden Text EXAKT {settings['cards_per_chunk']} Frage-Antwort-Paare.\n"
                "Format (strikt, keine zusätzlichen Texte):\n"
                "FRAGE:\nANTWORT: <kurze, präzise Antwort>\n\n"
                f"Text:\n{chunk}"
            )
            result = await client.generate(
                model=settings["model"],
                prompt=prompt,
                options={"temperature": float(settings["temperature"]), "num_ctx": int(settings["context_length"])},
            )
            cards.extend(parse_cards(result.response))
        if not cards:
            raise ValueError("Das Modell hat keine Frage-Antwort-Karten im erwarteten Format geliefert.")
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.cards.extend(Card(question=q, answer=a, position=i) for i, (q, a) in enumerate(cards, start=1))
            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.progress_text = f"{len(cards)} Karten erstellt"
            job.page_count = page_count
            job.text_length = len(text)
            job.duration_seconds = round(time.monotonic() - started, 2)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error = str(exc)[:2000]
                job.progress_text = "Verarbeitung fehlgeschlagen"
                job.duration_seconds = round(time.monotonic() - started, 2)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()


class WorkerSettings:
    functions = [process_job]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    max_jobs = 2
    job_timeout = 60 * 60
    keep_result = 0