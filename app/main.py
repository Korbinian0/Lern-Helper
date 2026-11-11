import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, REDIS_URL, UPLOAD_DIR
from app.database import Base, SessionLocal, engine
from app.models import Card, Job, JobStatus, Setting
from app.schemas import CardUpdate, SettingsPayload
from app.services import render_export
from app.worker import DEFAULTS, load_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="FlashCard Forge", version="1.0.0")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for key, value in DEFAULTS.items():
            if db.get(Setting, key) is None:
                db.add(Setting(key=key, value=value))
        db.commit()


def job_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "filename": job.filename,
        "status": job.status.value,
        "progress": job.progress,
        "progress_text": job.progress_text,
        "error": job.error,
        "page_count": job.page_count,
        "text_length": job.text_length,
        "card_count": len(job.cards),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "queue_position": None,
    }


def get_job(job_id: str, cards: bool = False) -> Job:
    with SessionLocal() as db:
        query = select(Job).where(Job.id == job_id)
        if cards:
            query = query.options(selectinload(Job.cards))
        job = db.scalar(query)
        if not job:
            raise HTTPException(status_code=404, detail="Job nicht gefunden")
        db.expunge(job)
        return job


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"active": "upload", "max_upload_mb": MAX_UPLOAD_BYTES // 1024 // 1024},
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    with SessionLocal() as db:
        settings = load_settings(db)
    return templates.TemplateResponse(request=request, name="admin.html", context={"active": "admin", "settings": settings})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).options(selectinload(Job.cards)).order_by(Job.created_at.desc())).all()
        queued = [job.id for job in reversed(jobs) if job.status == JobStatus.QUEUED]
        rows = [{"job": job, "position": queued.index(job.id) + 1 if job.id in queued else None} for job in jobs]
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"active": "dashboard", "jobs": rows})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    job = get_job(job_id, cards=True)
    return templates.TemplateResponse(request=request, name="job.html", context={"active": "", "job": job})


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_fragment(request: Request, job_id: str):
    job = get_job(job_id, cards=True)
    with SessionLocal() as db:
        queue = list(db.scalars(select(Job.id).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at)).all())
    position = queue.index(job.id) + 1 if job.id in queue else None
    return templates.TemplateResponse(request=request, name="job_status.html", context={"job": job, "position": position})


@app.get("/jobs/{job_id}/detail-status", response_class=HTMLResponse)
def detail_status_fragment(request: Request, job_id: str):
    job = get_job(job_id, cards=True)
    return templates.TemplateResponse(request=request, name="detail_status.html", context={"job": job})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unterstützt werden PDF, JPG, PNG, TIFF und WEBP.")
    stored_path = UPLOAD_DIR / f"{Path(filename).stem[:80]}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}{suffix}"
    size = 0
    try:
        with stored_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Maximale Dateigröße: {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
                destination.write(chunk)
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if size == 0:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Die hochgeladene Datei ist leer.")
    with SessionLocal() as db:
        job = Job(filename=filename, stored_path=str(stored_path), status=JobStatus.QUEUED)
        db.add(job)
        db.commit()
        job_id = job.id
    pool = None
    try:
        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        await pool.enqueue_job("process_job", job_id)
    except Exception as exc:
        stored_path.unlink(missing_ok=True)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = "Queue nicht erreichbar. Bitte Redis/Worker prüfen."
            job.progress_text = "Nicht in die Warteschlange übernommen"
            db.commit()
        logger.exception("Could not enqueue job %s", job_id)
        raise HTTPException(status_code=503, detail="Die Verarbeitungswarteschlange ist nicht erreichbar.") from exc
    finally:
        if pool is not None:
            await pool.aclose()
    return {"id": job_id, "status": "queued", "filename": filename}


@app.get("/api/jobs")
def list_jobs(status: str | None = None):
    statuses = {value.strip() for value in (status or "").split(",") if value.strip()}
    with SessionLocal() as db:
        query = select(Job).options(selectinload(Job.cards)).order_by(Job.created_at.desc())
        if statuses:
            allowed = {item.value for item in JobStatus}
            if not statuses <= allowed:
                raise HTTPException(status_code=400, detail="Ungültiger Statusfilter")
            query = query.where(Job.status.in_([JobStatus(value) for value in statuses]))
        jobs = db.scalars(query).all()
        queue = [job.id for job in db.scalars(select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at)).all()]
        result = []
        for job in jobs:
            data = job_dict(job)
            data["queue_position"] = queue.index(job.id) + 1 if job.id in queue else None
            result.append(data)
        return result


@app.get("/api/jobs/{job_id}")
def job_progress(job_id: str):
    job = get_job(job_id, cards=True)
    data = job_dict(job)
    if job.status == JobStatus.QUEUED:
        with SessionLocal() as db:
            queue = list(db.scalars(select(Job.id).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at)).all())
            data["queue_position"] = queue.index(job.id) + 1 if job.id in queue else None
    return data


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    job = get_job(job_id, cards=True)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Der Job ist noch nicht abgeschlossen.")
    return {"job_id": job.id, "cards": [{"id": card.id, "question": card.question, "answer": card.answer} for card in job.cards]}


@app.get("/api/jobs/{job_id}/export")
def export_job(job_id: str, format: str = "csv"):
    job = get_job(job_id, cards=True)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Der Job ist noch nicht abgeschlossen.")
    cards = [{"question": card.question, "answer": card.answer} for card in job.cards]
    try:
        body, media_type, extension = render_export(cards, format.lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="flashcards-{job.id}.{extension}"'})


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job nicht gefunden")
        path = Path(job.stored_path)
        db.delete(job)
        db.commit()
    path.unlink(missing_ok=True)
    return {"deleted": job_id}


@app.put("/api/jobs/{job_id}/cards/{card_id}")
def update_card(job_id: str, card_id: int, payload: CardUpdate):
    with SessionLocal() as db:
        card = db.scalar(select(Card).where(Card.job_id == job_id, Card.id == card_id))
        if not card:
            raise HTTPException(status_code=404, detail="Karte nicht gefunden")
        card.question = payload.question.strip()
        card.answer = payload.answer.strip()
        db.commit()
        db.refresh(card)
        return {"id": card.id, "question": card.question, "answer": card.answer}


@app.get("/api/settings")
def get_settings():
    with SessionLocal() as db:
        settings = load_settings(db)
    settings["chunk_size"] = int(settings["chunk_size"])
    settings["cards_per_chunk"] = int(settings["cards_per_chunk"])
    settings["context_length"] = int(settings["context_length"])
    settings["temperature"] = float(settings["temperature"])
    return settings


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload):
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{payload.ollama_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            names = {model.get("name") for model in models}
            if names and payload.model not in names:
                raise HTTPException(status_code=422, detail="Das gewählte Modell ist auf dem Ollama-Server nicht verfügbar.")
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ollama-Server nicht erreichbar: {exc}") from exc
    with SessionLocal() as db:
        for key, value in payload.model_dump().items():
            setting = db.get(Setting, key)
            if setting:
                setting.value = str(value)
            else:
                db.add(Setting(key=key, value=str(value)))
        db.commit()
    return {"saved": True}


@app.get("/api/models")
async def available_models():
    with SessionLocal() as db:
        settings = load_settings(db)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{settings['ollama_url']}/api/tags")
            response.raise_for_status()
            return {"models": [model.get("name") for model in response.json().get("models", [])]}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ollama-Server nicht erreichbar: {exc}") from exc


@app.get("/health")
def health():
    with SessionLocal() as db:
        db.execute(select(Setting.key).limit(1))
    return {"status": "ok"}