"""open-notebook-mcp: MCP server exposing open-notebook's REST API.

Curate research in the open-notebook UI; read findings back from any
Hermes session. Tools are namespaced with an ``on_`` prefix to keep them
distinct from generic verbs (list_/search_/ask_) used by other MCPs.

NOTE: Do not add ``from __future__ import annotations`` to this file.
Future annotations become strings and break FastMCP's ``Context`` typing
in tool decorators. Annotations below use bare types (X = None, not
``Optional[X]``) for the same reason.
"""

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from open_notebook_mcp.__about__ import __version__


# --- Configuration -----------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:5055"
DEFAULT_TIMEOUT_S = 30.0


def _base_url() -> str:
    raw = (os.environ.get("OPEN_NOTEBOOK_URL") or DEFAULT_BASE_URL).rstrip("/")
    # Auto-append /api if the user gave the bare host. Tolerate either form.
    if not raw.endswith("/api"):
        raw = raw + "/api"
    return raw


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("OPEN_NOTEBOOK_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _timeout_s() -> float:
    try:
        return float(os.environ.get("OPEN_NOTEBOOK_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S


# --- HTTP client -------------------------------------------------------------


class OpenNotebookError(RuntimeError):
    """Wraps non-2xx responses with the server's error detail."""

    def __init__(self, status: int, detail: str, url: str):
        super().__init__(f"open-notebook {status} on {url}: {detail}")
        self.status = status
        self.detail = detail


class OpenNotebookClient:
    """Thin async httpx wrapper over the open-notebook REST API."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None):
        self.base_url = base_url or _base_url()
        self.timeout_s = timeout_s if timeout_s is not None else _timeout_s()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            headers={"Accept": "application/json", **_auth_headers()},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        # Strip None values from params so they don't hit the wire.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            resp = await self._client.request(
                method,
                path,
                params=clean_params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise OpenNotebookError(0, str(exc), path) from exc

        if resp.status_code >= 400:
            detail = resp.text
            try:
                payload = resp.json()
                detail = payload.get("detail") if isinstance(payload, dict) else detail
            except Exception:
                pass
            raise OpenNotebookError(resp.status_code, detail or "(no detail)", path)

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text

    # ---- Health ------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Return {auth_enabled, base_url, reachable, version}."""
        out: dict[str, Any] = {
            "base_url": self.base_url,
            "version": __version__,
            "reachable": False,
            "auth_enabled": None,
        }
        try:
            data = await self._request("GET", "/auth/status")
            out["reachable"] = True
            out["auth_enabled"] = bool((data or {}).get("auth_enabled"))
        except OpenNotebookError as exc:
            out["error"] = str(exc)
        return out

    # ---- Notebooks ---------------------------------------------------------

    async def list_notebooks(
        self, archived: bool | None = None, order_by: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/notebooks", params={"archived": archived, "order_by": order_by}
        )

    async def get_notebook(self, notebook_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/notebooks/{notebook_id}")

    async def create_notebook(self, name: str, description: str = "") -> dict[str, Any]:
        return await self._request(
            "POST", "/notebooks", json_body={"name": name, "description": description}
        )

    # ---- Sources -----------------------------------------------------------

    async def list_sources(
        self,
        notebook_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/sources",
            params={"notebook_id": notebook_id, "limit": limit, "offset": offset},
        )

    async def get_source(self, source_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/sources/{source_id}")

    async def get_source_insights(self, source_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/sources/{source_id}/insights")

    async def create_source_text(
        self,
        notebook_id: str,
        title: str,
        content: str,
        embed: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/sources",
            json_body={
                "type": "text",
                "notebooks": [notebook_id],
                "title": title,
                "content": content,
                "embed": embed,
            },
        )

    async def create_source_link(
        self,
        notebook_id: str,
        url: str,
        title: str | None = None,
        embed: bool = False,
        transformations: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "link",
            "notebooks": [notebook_id],
            "url": url,
            "embed": embed,
        }
        if title:
            body["title"] = title
        if transformations:
            body["transformations"] = transformations
        return await self._request("POST", "/sources", json_body=body)

    # ---- Notes -------------------------------------------------------------

    async def list_notes(
        self, notebook_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/notes",
            params={"notebook_id": notebook_id, "limit": limit, "offset": offset},
        )

    async def get_note(self, note_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/notes/{note_id}")

    async def create_note(
        self,
        notebook_id: str,
        content: str,
        title: str | None = None,
        note_type: str = "human",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content, "note_type": note_type}
        if title:
            body["title"] = title
        # The NoteCreate schema accepts notebook_id as the optional join key.
        body["notebook_id"] = notebook_id
        return await self._request("POST", "/notes", json_body=body)

    # ---- Search ------------------------------------------------------------

    async def search(
        self,
        query: str,
        search_type: str = "text",
        limit: int = 10,
        search_sources: bool = True,
        search_notes: bool = True,
        minimum_score: float | None = None,
        notebook_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": query,
            "type": search_type,
            "limit": limit,
            "search_sources": search_sources,
            "search_notes": search_notes,
        }
        if minimum_score is not None:
            body["minimum_score"] = minimum_score
        if notebook_id is not None:
            body["notebook_id"] = notebook_id
        return await self._request("POST", "/search", json_body=body)

    async def ask(
        self,
        question: str,
        notebook_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Non-streaming Q&A against the knowledge base."""
        body: dict[str, Any] = {"question": question, "strategy": "simple"}
        if notebook_id:
            body["notebook_id"] = notebook_id
        if model_id:
            body["model_id"] = model_id
        return await self._request("POST", "/search/ask/simple", json_body=body)


# --- Result formatting -------------------------------------------------------


def _to_json(obj: Any) -> str:
    """Serialize with a generous size cap. open-notebook sources can be huge."""
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(obj), indent=2)


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [truncated, {len(text) - limit} more chars]"


# --- MCP server --------------------------------------------------------------


mcp = FastMCP("open-notebook")


def _client() -> OpenNotebookClient:
    # One client per tool invocation keeps the server stateless and avoids
    # leaking sockets across requests.
    return OpenNotebookClient()


async def _call(label: str, coro):
    """Run a client coroutine and format any error as readable text."""
    client = _client()
    try:
        result = await coro
        return _to_json(_truncate_value(result))
    except OpenNotebookError as exc:
        return _to_json({"error": str(exc), "status": exc.status, "op": label})
    except Exception as exc:  # last-resort safety net so the tool never crashes
        return _to_json({"error": f"{type(exc).__name__}: {exc}", "op": label})
    finally:
        await client.aclose()


# ---- Health / introspection -------------------------------------------------


@mcp.tool()
async def on_health() -> str:
    """Probe the open-notebook API. Returns {reachable, auth_enabled, base_url}.

    Use this when tool calls fail unexpectedly or before long workflows to
    confirm the notebook service is up.
    """
    client = _client()
    try:
        return _to_json(await client.health())
    finally:
        await client.aclose()


# ---- Discovery --------------------------------------------------------------


@mcp.tool()
async def on_list_notebooks(archived: bool = None, order_by: str = None) -> str:
    """List all open-notebook notebooks.

    Args:
        archived: When true, return only archived notebooks. Default false (active only).
        order_by: Sort field+direction, e.g. "updated desc" (default), "name asc".
    """
    return await _call("list_notebooks", _client().list_notebooks(archived, order_by))


@mcp.tool()
async def on_list_sources(
    notebook_id: str = None, limit: int = 50, offset: int = 0
) -> str:
    """List sources (articles, links, uploads, text dumps) in the notebook.

    Args:
        notebook_id: Restrict to one notebook. Omit to list all sources globally.
        limit: Max results (default 50).
        offset: Pagination offset.
    """
    return await _call("list_sources", _client().list_sources(notebook_id, limit, offset))


@mcp.tool()
async def on_list_notes(
    notebook_id: str = None, limit: int = 50, offset: int = 0
) -> str:
    """List notes (human + AI) in the notebook.

    The list endpoint returns metadata only (no content) — call on_get_note
    for each id to pull the full text.

    Args:
        notebook_id: Restrict to one notebook. Omit for global list.
        limit: Max results (default 50).
        offset: Pagination offset.
    """
    return await _call("list_notes", _client().list_notes(notebook_id, limit, offset))


# ---- Read -------------------------------------------------------------------


@mcp.tool()
async def on_get_source(source_id: str) -> str:
    """Fetch full source content (full_text, topics, asset, processing status).

    Args:
        source_id: The source id, e.g. "source:abc123".
    """
    return await _call("get_source", _client().get_source(source_id))


@mcp.tool()
async def on_get_note(note_id: str) -> str:
    """Fetch a single note's full content.

    Args:
        note_id: The note id, e.g. "note:abc123".
    """
    return await _call("get_note", _client().get_note(note_id))


@mcp.tool()
async def on_get_source_insights(source_id: str) -> str:
    """Fetch the AI-generated insights (Dense Summary, key points, etc.) for a source.

    Insights are produced when open-notebook applies a transformation to a
    source — they are the most distilled form of the source material and
    usually the best starting point for downstream reasoning.

    Args:
        source_id: The source id.
    """
    return await _call(
        "get_source_insights", _client().get_source_insights(source_id)
    )


@mcp.tool()
async def on_search(
    query: str,
    search_type: str = "text",
    limit: int = 10,
    search_sources: bool = True,
    search_notes: bool = True,
    minimum_score: float = None,
    notebook_id: str = None,
) -> str:
    """Text or vector search across the open-notebook knowledge base.

    Args:
        query: Free-text query. For vector search, a natural-language question works best.
        search_type: "text" (BM25-style) or "vector" (semantic). Default "text".
        limit: Max results (default 10).
        search_sources: Include sources in results. Default true.
        search_notes: Include notes in results. Default true.
        minimum_score: For vector search, minimum similarity 0.0-1.0 (default 0.2).
        notebook_id: Restrict to one notebook. Omit to search everywhere.
    """
    return await _call(
        "search",
        _client().search(
            query=query,
            search_type=search_type,
            limit=limit,
            search_sources=search_sources,
            search_notes=search_notes,
            minimum_score=minimum_score,
            notebook_id=notebook_id,
        ),
    )


@mcp.tool()
async def on_ask(question: str, notebook_id: str = None, model_id: str = None) -> str:
    """Ask a question against the knowledge base (non-streaming RAG).

    open-notebook retrieves relevant chunks, then asks the configured LLM
    to answer using them. Returns a structured response with answer +
    source citations. Requires a default chat model to be set in
    open-notebook Settings.

    Args:
        question: The question to answer.
        notebook_id: Restrict retrieval to one notebook.
        model_id: Override the default LLM (must be a registered model id).
    """
    return await _call("ask", _client().ask(question, notebook_id, model_id))


# ---- Write ------------------------------------------------------------------


@mcp.tool()
async def on_create_note(
    notebook_id: str, content: str, title: str = None, note_type: str = "ai"
) -> str:
    """Write a new note into a notebook.

    Use this when the agent has findings worth preserving alongside your
    curated research. The note shows up in the open-notebook UI and is
    included in future search/ask calls.

    Args:
        notebook_id: Target notebook id, e.g. "notebook:abc123".
        content: Note body (markdown OK).
        title: Short title. If omitted, open-notebook auto-generates one.
        note_type: "ai" (default, agent-written) or "human" (treat as your own).
    """
    return await _call(
        "create_note", _client().create_note(notebook_id, content, title, note_type)
    )


@mcp.tool()
async def on_create_source_text(
    notebook_id: str, title: str, content: str, embed: bool = False
) -> str:
    """Add a text source (raw pasted content) to a notebook.

    Args:
        notebook_id: Target notebook id.
        title: Source title.
        content: Raw text content (will be chunked and indexed).
        embed: If true, generate embeddings for vector search (slower, costs tokens).
    """
    return await _call(
        "create_source_text",
        _client().create_source_text(notebook_id, title, content, embed),
    )


@mcp.tool()
async def on_create_source_link(
    notebook_id: str,
    url: str,
    title: str = None,
    embed: bool = False,
    transformations: list = None,
) -> str:
    """Add a link source (URL) to a notebook. open-notebook fetches + indexes it.

    Args:
        notebook_id: Target notebook id.
        url: The URL to fetch.
        title: Optional title (defaults to URL).
        embed: Generate embeddings for vector search.
        transformations: List of transformation ids to apply (e.g. dense summary).
    """
    return await _call(
        "create_source_link",
        _client().create_source_link(notebook_id, url, title, embed, transformations),
    )


# --- Truncation helper for content-heavy reads -------------------------------


def _truncate_value(result):
    """Truncate large string fields (``full_text``, ``content``) in-place.

    open-notebook sources and notes can be tens of thousands of chars.
    Truncate those specifically so the rest of the metadata stays intact
    and the response stays tool-friendly.
    """
    if isinstance(result, dict):
        for field in ("full_text", "content"):
            value = result.get(field)
            if isinstance(value, str) and len(value) > 12_000:
                result[field] = (
                    value[:12_000]
                    + f"\n\n... [truncated, {len(value) - 12_000} more chars]"
                )
    return result


# --- Entrypoint --------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
