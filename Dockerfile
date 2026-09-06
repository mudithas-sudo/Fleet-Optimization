# FleetOps runs as one long-lived FastAPI process (REST + SSE + static
# frontend, SQLite on local disk). That rules out serverless/edge hosts
# like Vercel; this image runs as-is on any container platform —
# Hugging Face Spaces, Render, Railway, Fly.io, or a plain VM.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    FAKE_ROUTES=1

# Run as a non-root user (Hugging Face Spaces requires UID 1000; harmless
# elsewhere). Gives the app a writable HOME for any library caches.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# fleet.db is created in the working dir on first write — ephemeral on most
# hosts (wiped on redeploy/restart), which is fine for a demo. To keep data,
# mount a volume and set DB_PATH to a path inside it (see render.yaml).
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
