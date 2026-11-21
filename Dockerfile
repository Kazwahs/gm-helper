FROM python:3.11-slim

# Build with --build-arg INSTALL_OCR=false for a much smaller image that skips
# EasyOCR/PyTorch. Everything else works; only OCR of scanned PDFs needs them.
ARG INSTALL_OCR=true

WORKDIR /app

COPY requirements.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OCR" = "true" ]; then pip install --no-cache-dir -r requirements-ocr.txt; fi

COPY app ./app
COPY run.py .

# Startup smoke check: fail the *build* - not the running container - if any file has a
# syntax error on this Python version or the app can't be imported. (An image that
# builds but crashes on start is worse than one that never gets built.)
RUN python -m compileall -q app && python -c "import app.main"

# Persistent state (the SQLite DB + editable games.json) is controlled at
# *run* time via GM_HELPER_DATA_DIR (or the individual GM_HELPER_DB_PATH /
# GM_HELPER_GAMES_CONFIG_PATH overrides) - see app/config.py. Deliberately no
# ENV or VOLUME baked in here: a value set here would take priority over
# whatever a docker-compose deployment passes in, which previously caused
# the app to silently write into an anonymous, root-owned Docker volume
# instead of the intended mounted host folder.

EXPOSE 8000

# /healthz answers without logging in (even with GM_HELPER_PASSWORD set) and
# returns 503 if the database can't be opened. No curl in the slim base image,
# so this uses Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
