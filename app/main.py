import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, database
from .library import games as games_module
from .security import BasicAuthMiddleware
from .routers import (
    bookmarks, campaigns, dashboard, games, library, maps, ops, screen, settings, tables, tools,
)


@asynccontextmanager
async def lifespan(app):
    database.init_db()
    games_module.reload()
    yield


app = FastAPI(title="GM Helper", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)  # no-op unless GM_HELPER_PASSWORD is set
app.mount("/static", StaticFiles(directory=os.path.join(config.APP_DIR, "static")), name="static")

for module in (dashboard, settings, games, library, bookmarks, campaigns, maps, screen, tools, tables, ops):
    app.include_router(module.router)
