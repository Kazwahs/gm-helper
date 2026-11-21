import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import autobackup, config, database
from .access import AccessMiddleware
from .library import games as games_module
from .web import NoCacheStaticFiles
from .routers import (
    accounts, bookmarks, campaign_world, campaigns, dashboard, games, library, live, maps, ops, play, screen, settings,
    tables, tools,
)


@asynccontextmanager
async def lifespan(app):
    database.init_db()
    games_module.reload()
    scheduler = autobackup.Scheduler()   # automatic backups; see autobackup.py
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(title="GM Helper", lifespan=lifespan)
# Sign-in and who-may-open-what (access.py). While there are no accounts it falls back to the
# old behaviour: open, or HTTP Basic if GM_HELPER_PASSWORD is set.
app.add_middleware(AccessMiddleware)
app.mount("/static", NoCacheStaticFiles(directory=os.path.join(config.APP_DIR, "static")), name="static")

# Every router the app serves. Listed once so the access-policy test can walk all routes.
ROUTER_MODULES = (dashboard, settings, games, library, bookmarks, campaigns, campaign_world, maps, live, screen, tools,
                  tables, ops, accounts, play)

for module in ROUTER_MODULES:
    app.include_router(module.router)
