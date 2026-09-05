FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .

# Persistent state (the SQLite DB + editable games.json) is controlled at
# *run* time via GM_HELPER_DATA_DIR (or the individual GM_HELPER_DB_PATH /
# GM_HELPER_GAMES_CONFIG_PATH overrides) - see app/config.py. Deliberately no
# ENV or VOLUME baked in here: a value set here would take priority over
# whatever a docker-compose deployment passes in, which previously caused
# the app to silently write into an anonymous, root-owned Docker volume
# instead of the intended mounted host folder.

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
