"""Automatic backups: a small "my data" snapshot written to a backups folder on
a schedule, keeping only the newest few.

The snapshots are the same kind as Settings -> Backup -> "Download my data"
(see backup.py): everything you created, without the book search index or the
saved AI key, so each one is typically well under a few MB and a month of
daily copies costs next to nothing.

They live in a ``backups`` folder next to the database (override with
``GM_HELPER_BACKUP_DIR``). That protects against mistakes, a bad update or a
corrupted database - it does NOT protect against the disk dying, so now and then
copy that folder somewhere else (or download a backup from Settings).

A daemon thread checks every few minutes whether a backup is due and makes one
if the newest is older than the chosen interval. It starts with the app (see
main.py) and stops with it.
"""
import os
import re
import shutil
import threading
from datetime import datetime

from . import backup, config, database

ENABLED_KEY = "autobackup_enabled"
HOURS_KEY = "autobackup_hours"
KEEP_KEY = "autobackup_keep"
LAST_ERROR_KEY = "autobackup_last_error"

DEFAULT_HOURS = 24
DEFAULT_KEEP = 14
MAX_HOURS = 24 * 30
MAX_KEEP = 200

AUTO_NAME = re.compile(r"^gm_helper-auto-\d{8}-\d{4}\.db$")

# How often the background thread looks, and how long it waits after startup
# before the first look (so a restart never slows the app coming up).
CHECK_EVERY_SECONDS = 600
FIRST_CHECK_DELAY_SECONDS = 60


def backup_dir():
    override = os.environ.get("GM_HELPER_BACKUP_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(config.DB_PATH) or ".", "backups")


def _clamp_int(value, default, low, high):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def get_settings():
    return {
        "enabled": database.get_setting(ENABLED_KEY, "1") != "0",
        "hours": _clamp_int(database.get_setting(HOURS_KEY), DEFAULT_HOURS, 1, MAX_HOURS),
        "keep": _clamp_int(database.get_setting(KEEP_KEY), DEFAULT_KEEP, 1, MAX_KEEP),
    }


def save_settings(enabled, hours, keep):
    """Stores (and returns) the cleaned-up settings. Out-of-range or non-numeric
    values fall back to the defaults / nearest allowed value."""
    database.set_setting(ENABLED_KEY, "1" if enabled else "0")
    database.set_setting(HOURS_KEY, str(_clamp_int(hours, DEFAULT_HOURS, 1, MAX_HOURS)))
    database.set_setting(KEEP_KEY, str(_clamp_int(keep, DEFAULT_KEEP, 1, MAX_KEEP)))
    return get_settings()


def list_backups():
    """The automatic backups on disk, newest first: [{name, size, modified}]."""
    folder = backup_dir()
    if not os.path.isdir(folder):
        return []
    found = []
    for name in os.listdir(folder):
        if not AUTO_NAME.match(name):
            continue
        path = os.path.join(folder, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        found.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
    found.sort(key=lambda b: (b["mtime"], b["name"]), reverse=True)
    for b in found:
        b["modified"] = datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M")
    return found


def path_for(name):
    """Full path of an automatic backup by name, or None (also for anything that
    isn't exactly one of our own file names, so a request can't reach elsewhere)."""
    if not AUTO_NAME.match(name or ""):
        return None
    path = os.path.join(backup_dir(), name)
    return path if os.path.isfile(path) else None


def prune(keep=None):
    """Deletes all but the newest `keep` automatic backups. Returns how many went."""
    keep = get_settings()["keep"] if keep is None else keep
    removed = 0
    for old in list_backups()[keep:]:
        try:
            os.remove(os.path.join(backup_dir(), old["name"]))
            removed += 1
        except OSError:
            pass
    return removed


def run_backup(now=None):
    """Makes one automatic backup now, trims old ones, and returns its file name.
    Raises if the backup could not be written (the caller records the error)."""
    now = now or datetime.now()
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    tmp_path = backup.make_backup("user-data")
    name = f"gm_helper-auto-{now.strftime('%Y%m%d-%H%M')}.db"
    final = os.path.join(folder, name)
    try:
        shutil.move(tmp_path, final)
    except Exception:
        backup.remove_file(tmp_path)
        raise
    prune()
    return name


def record_run(error=None):
    database.set_setting(LAST_ERROR_KEY, error or "")


def last_error():
    return database.get_setting(LAST_ERROR_KEY, "") or ""


def is_due(now=None):
    """True when automatic backups are on and the newest one is older than the
    interval (or there is none yet)."""
    s = get_settings()
    if not s["enabled"]:
        return False
    existing = list_backups()
    if not existing:
        return True
    age_hours = ((now or datetime.now()).timestamp() - existing[0]["mtime"]) / 3600
    return age_hours >= s["hours"]


def run_if_due(now=None):
    """One scheduler tick: backs up if due. Returns the new file name, or None."""
    if not is_due(now):
        return None
    try:
        name = run_backup(now)
    except Exception as e:  # never let a failed backup take the app down
        record_run(f"{type(e).__name__}: {e}")
        return None
    record_run(None)
    return name


class Scheduler:
    """The background thread. start() once at app startup, stop() at shutdown."""

    def __init__(self, check_every=CHECK_EVERY_SECONDS, first_delay=FIRST_CHECK_DELAY_SECONDS):
        self._stop = threading.Event()
        self._thread = None
        self._check_every = check_every
        self._first_delay = first_delay

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gm-helper-autobackup", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self):
        wait = self._first_delay
        while not self._stop.wait(wait):
            try:
                run_if_due()
            except Exception:
                pass  # run_if_due already records backup failures; this is for anything stranger
            wait = self._check_every
