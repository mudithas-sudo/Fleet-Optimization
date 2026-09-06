# FleetOps runs as one long-lived FastAPI process (REST + SSE + static
# frontend, SQLite on local disk). That rules out serverless/edge hosts
# like Vercel; this image runs as-is on any container platform —
# Render, Railway, Fly.io, or a plain VM.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    FAKE_ROUTES=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# fleet.db is created in the working dir on first write. That's ephemeral
# on most hosts (wiped on redeploy) — fine for a demo. To keep data, mount
# a volume and set DB_PATH=/data/fleet.db (see render.yaml).
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
