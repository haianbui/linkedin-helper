from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.dependencies import get_orchestrator
from app.models.search import SearchSession

router = APIRouter(tags=["search"])

# In-memory stores (work for single-process; on serverless each request is self-contained via /search/stream)
_sessions: dict[str, SearchSession] = {}
_session_results: dict[str, list[dict]] = {}


class SearchRequest(BaseModel):
    query: str
    max_results: int = 100


class SearchResponse(BaseModel):
    session_id: str
    status: str


@router.post("/search")
async def start_search(req: SearchRequest) -> SearchResponse:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    session = SearchSession(natural_query=req.query.strip())
    _sessions[session.id] = session
    _session_results[session.id] = []

    return SearchResponse(session_id=session.id, status=session.status.value)


@router.get("/search/{session_id}/stream")
async def stream_search(session_id: str, query: str = Query(default="")):
    """SSE stream endpoint. Works two ways:
    - With in-memory session (local dev): looks up session by ID
    - With query param (serverless): creates session on the fly
    """
    session = _sessions.get(session_id)
    if not session:
        if not query:
            raise HTTPException(status_code=404, detail="Session not found")
        # Serverless mode: create session from query param
        session = SearchSession(id=session_id, natural_query=query)

    orchestrator = get_orchestrator()
    collected_results: list[dict] = []

    async def event_generator():
        async for sse_event in orchestrator.execute_search(session):
            if sse_event.event == "result":
                data = json.loads(sse_event.data)
                collected_results.append(data)
                _session_results.setdefault(session_id, []).append(data)

            yield {"event": sse_event.event, "data": sse_event.data}

    return EventSourceResponse(event_generator())


@router.get("/search/{session_id}/results")
async def get_results(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "status": session.status.value,
        "query": session.natural_query,
        "result_count": session.result_count,
        "results": _session_results.get(session_id, []),
    }


@router.get("/sessions")
async def list_sessions():
    return [
        {
            "id": s.id,
            "query": s.natural_query,
            "status": s.status.value,
            "result_count": s.result_count,
            "created_at": s.created_at.isoformat(),
        }
        for s in sorted(_sessions.values(), key=lambda s: s.created_at, reverse=True)[:20]
    ]


@router.get("/export/{session_id}/csv")
async def export_csv(session_id: str):
    results = _session_results.get(session_id, [])
    if not results:
        raise HTTPException(status_code=404, detail="No results found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rank", "Name", "LinkedIn URL", "Title", "Company",
        "Location", "Match Score", "Match Summary", "Headline",
    ])

    for r in results:
        profile = r.get("profile", {})
        evaluation = r.get("evaluation", {})
        writer.writerow([
            r.get("rank", ""),
            profile.get("full_name", ""),
            profile.get("linkedin_url", ""),
            profile.get("current_title", ""),
            profile.get("current_company", ""),
            profile.get("location", ""),
            evaluation.get("match_score", ""),
            evaluation.get("summary", ""),
            profile.get("headline", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=linkedin-search-{session_id}.csv"},
    )
