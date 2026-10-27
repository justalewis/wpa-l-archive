FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wpal/ wpal/
COPY templates/ templates/
COPY static/ static/

# The archive database is not in the image. It lives on the Fly volume at
# /data/archive.db and is uploaded separately (see NOTES.md, "Deploy"), so a
# code deploy never ships 1.2 GB. Opt-out requests go to /data/requests.db.
ENV WPAL_DB=/data/archive.db \
    WPAL_REQUESTS_DB=/data/requests.db \
    PORT=8080

EXPOSE 8080
# One process, a few threads: SQLite is read-only here apart from the small
# requests database, and one process keeps that to a single writer.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", \
     "--worker-class", "gthread", "--timeout", "60", "--access-logfile", "-", "wpal.app:app"]
