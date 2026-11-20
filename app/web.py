"""Shared web plumbing for the routers: the Jinja environment and the
handful of helpers every page needs (template context, library root, the
current game)."""
import os

from fastapi.templating import Jinja2Templates

from . import config, database
from . import llm as llm_module
from . import theme as theme_module
from .library import games as games_module

templates = Jinja2Templates(directory=os.path.join(config.APP_DIR, "templates"))


def library_root():
    return database.get_setting("library_root", "")


def base_ctx(request):
    theme_name = theme_module.get_theme_name()
    ctx = {
        "request": request,
        "library_root": library_root(),
        "theme_name": theme_name,
        "ai_enabled": llm_module.is_enabled(),
    }
    if theme_name == "custom":
        ctx["custom_theme_vars"] = theme_module.get_custom_vars()
    return ctx


def game_ctx(game_key):
    """The game dict for a ?game=<key> query parameter, or None."""
    return games_module.get_game(game_key) if game_key else None
