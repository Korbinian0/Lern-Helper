# FlashCard Forge

Webanwendung zum Erstellen und Verwalten von Lernkarten aus PDFs und Bildern. FastAPI und HTMX liefern die Oberfläche; PyMuPDF extrahiert PDF-Text, Tesseract übernimmt Scans und Bilder. Die Generierung läuft asynchron über arq/Redis und verbindet sich mit einem externen Ollama-Server. Einstellungen, Aufträge und Karten liegen in SQLite.

## Start mit Docker

Voraussetzungen: Docker Compose und ein erreichbarer Ollama-Server. Ollama wird nicht in diesem Compose-Stack gestartet.

```sh
docker compose up --build -d
```

Öffne anschließend <http://localhost:8080>. Standardmäßig erreicht der Container Ollama auf `http://host.docker.internal:11434`; für einen anderen Host kannst du `OLLAMA_DEFAULT_URL` in der Umgebung setzen. Daten und Uploads bleiben im lokalen Verzeichnis `data/` erhalten. `MAX_UPLOAD_MB` setzt das Uploadlimit (Standard: 50 MB).

In `/admin` Server-URL und Modell auswählen. Das Speichern testet die Verbindung über `/api/tags`. Für OCR müssen die passenden Tesseract-Sprachpakete im Image installiert sein; standardmäßig sind Deutsch und Englisch enthalten.

## Lokale Entwicklung

Benötigt werden Python 3.11+, Tesseract mit den Sprachpaketen `deu` und `eng`, Redis sowie ein externer Ollama-Server.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export REDIS_URL=redis://localhost:6379/0
export OLLAMA_DEFAULT_URL=http://localhost:11434
uvicorn app.main:app --reload
```

In einem zweiten Terminal den Worker starten:

```sh
arq app.worker.WorkerSettings
```

## Bedienung

- `/` lädt PDF-, JPG-, PNG-, TIFF- und WEBP-Dateien hoch.
- `/dashboard` zeigt Queue, Status, Kartenanzahl und Exporte.
- `/jobs/{id}` zeigt und bearbeitet einzelne Karten.
- `/admin` konfiguriert Ollama, OCR und Generierung.
- Die API ist unter `/api` verfügbar; `/health` prüft die Webanwendung.

Die Chunk-Größe wird als Wortzahl angenähert, da Tokenizer zwischen Ollama-Modellen variieren. Je Chunk wird ein Überlapp von 10 % verwendet. Die Zahl der erzeugten Karten hängt davon ab, wie zuverlässig das Modell das vorgegebene Format einhält.

## Betrieb

Der Compose-Stack bindet den Webserver an alle Interfaces des Hosts. Für einen öffentlichen oder gemeinsam genutzten Betrieb muss ein authentifizierender Reverse Proxy vorgeschaltet und der Zugriff auf `/admin` sowie die API entsprechend eingeschränkt werden. TLS, Backups für `data/` und ein erreichbarer Redis-/Ollama-Dienst gehören zur Betriebsumgebung.

Die fokussierten Tests laufen mit:

```sh
python -m unittest discover -s tests
```