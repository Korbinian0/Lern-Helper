import csv
import io
import json
import re
from pathlib import Path

import fitz
import pytesseract
from PIL import Image


def extract_document(path: Path, ocr_language: str, on_page=None) -> tuple[list[str], int]:
    if path.suffix.lower() == ".pdf":
        pages: list[str] = []
        with fitz.open(path) as document:
            page_count = len(document)
            for page_number, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                if len(text) < 50:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    text = pytesseract.image_to_string(image, lang=ocr_language).strip()
                pages.append(text)
                if on_page:
                    on_page(page_number, page_count)
        return pages, page_count

    with Image.open(path) as image:
        frames = []
        try:
            while True:
                frames.append(pytesseract.image_to_string(image.copy(), lang=ocr_language).strip())
                if on_page:
                    on_page(len(frames), getattr(image, "n_frames", 1))
                image.seek(image.tell() + 1)
        except EOFError:
            pass
    return frames, len(frames)


def make_chunks(text: str, chunk_size: int, overlap_ratio: float = 0.1) -> list[str]:
    words = text.split()
    if not words:
        return []
    overlap = max(1, int(chunk_size * overlap_ratio))
    step = max(1, chunk_size - overlap)
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + chunk_size]))
        if start + chunk_size >= len(words):
            break
    return chunks


def parse_cards(response: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"FRAGE\s*:\s*(.*?)\s*ANTWORT\s*:\s*(.*?)(?=\n\s*FRAGE\s*:|\Z)", re.I | re.S)
    return [(question.strip(), answer.strip()) for question, answer in pattern.findall(response) if question.strip() and answer.strip()]


def render_export(cards: list[dict[str, str]], export_format: str) -> tuple[str, str, str]:
    if export_format == "json":
        return json.dumps(cards, ensure_ascii=False, indent=2), "application/json; charset=utf-8", "json"
    if export_format in {"csv", "anki_csv"}:
        output = io.StringIO(newline="")
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow(["Front", "Back"])
        writer.writerows((_safe_csv_cell(card["question"]), _safe_csv_cell(card["answer"])) for card in cards)
        return output.getvalue(), "text/csv; charset=utf-8", "csv"
    if export_format == "markdown":
        body = "\n\n".join(f"## {card['question']}\n\n{card['answer']}" for card in cards)
        return body, "text/markdown; charset=utf-8", "md"
    raise ValueError("Unbekanntes Exportformat")


def _safe_csv_cell(value: str) -> str:
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
        return f"'{value}"
    return value