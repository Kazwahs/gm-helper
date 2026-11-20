"""Bookmark collections and the bookmarks inside them."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import bookmarks as bookmarks_module
from .. import database
from ..web import base_ctx, templates

router = APIRouter()


@router.get("/bookmarks")
def bookmarks_page(request: Request):
    ctx = base_ctx(request)
    ctx["collections"] = bookmarks_module.list_collections_with_bookmarks()
    return templates.TemplateResponse(request, "bookmarks.html", ctx)


@router.get("/api/bookmarks/collections")
def api_list_collections():
    return JSONResponse(bookmarks_module.list_collections())


@router.post("/api/bookmarks/collections")
def api_create_collection(name: str = Form(...)):
    try:
        collection_id = bookmarks_module.create_collection(name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": collection_id, "name": name.strip()})


@router.post("/api/bookmarks/collections/{collection_id}/rename")
def api_rename_collection(collection_id: int, name: str = Form(...)):
    try:
        bookmarks_module.rename_collection(collection_id, name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/bookmarks/collections/{collection_id}/delete")
def api_delete_collection(collection_id: int):
    bookmarks_module.delete_collection(collection_id)
    return JSONResponse({"ok": True})


@router.post("/api/bookmarks/add")
def api_add_bookmark(book_id: int = Form(...), page_number: int = Form(None),
                      note: str = Form(""), collection_id: int = Form(None),
                      new_collection_name: str = Form(None)):
    if not collection_id and not new_collection_name:
        return JSONResponse({"error": "Choose a collection or name a new one."}, status_code=400)
    with database.get_cursor() as cur:
        book_exists = cur.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if collection_id:
            collection_exists = cur.execute(
                "SELECT 1 FROM bookmark_collections WHERE id = ?", (collection_id,)
            ).fetchone()
        else:
            collection_exists = True
    if not book_exists:
        return JSONResponse({"error": "That book no longer exists - try rescanning the library."}, status_code=400)
    if not collection_exists:
        return JSONResponse({"error": "That collection no longer exists - refresh the page."}, status_code=400)
    try:
        if not collection_id:
            collection_id = bookmarks_module.get_or_create_collection(new_collection_name)
        bookmark_id = bookmarks_module.add_bookmark(collection_id, book_id, page_number, note)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": bookmark_id, "collection_id": collection_id})


@router.post("/api/bookmarks/{bookmark_id}/note")
def api_update_bookmark_note(bookmark_id: int, note: str = Form("")):
    bookmarks_module.update_bookmark_note(bookmark_id, note)
    return JSONResponse({"ok": True})


@router.post("/api/bookmarks/{bookmark_id}/delete")
def api_delete_bookmark(bookmark_id: int):
    bookmarks_module.delete_bookmark(bookmark_id)
    return JSONResponse({"ok": True})
