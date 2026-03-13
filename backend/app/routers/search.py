from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.dependencies import get_database, get_orchestrator
from app.models.search import SearchSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])

# In-memory stores (work for single-process; on serverless each request is self-contained)
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
        session = SearchSession(id=session_id, natural_query=query)

    orchestrator = get_orchestrator()

    async def event_generator():
        async for sse_event in orchestrator.execute_search(session):
            if sse_event.event == "result":
                data = json.loads(sse_event.data)
                _session_results.setdefault(session_id, []).append(data)

            yield {"event": sse_event.event, "data": sse_event.data}

    return EventSourceResponse(event_generator())


@router.post("/search/run")
async def run_search(req: SearchRequest):
    """Synchronous search - runs full pipeline, returns JSON."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        orchestrator = get_orchestrator()
        result = await orchestrator.execute_search_sync(
            query=req.query.strip(),
            max_results=25,
        )
        if "session_id" in result and "results" in result:
            _session_results[result["session_id"]] = result["results"]
        return result
    except Exception as e:
        logger.exception("Search run failed")
        return {"error": f"Search failed: {e}", "results": []}


@router.get("/sessions")
async def list_sessions():
    """List recent searches from DB, fallback to in-memory."""
    db = get_database()
    queries = await db.list_queries(limit=20)
    if queries:
        return [
            {
                "id": q["id"],
                "query": q["natural_query"],
                "status": q["status"],
                "result_count": q["result_count"],
                "created_at": q["created_at"],
            }
            for q in queries
        ]
    # Fallback to in-memory
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


@router.get("/sessions/{query_id}/results")
async def get_saved_results(query_id: str):
    """Load saved results for a past query from DB."""
    db = get_database()
    query = await db.get_query(query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    results = await db.get_query_results(query_id)
    return {"query": query, "results": results}


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


@router.get("/export/{session_id}/csv")
async def export_csv(session_id: str):
    """Export results as CSV. Try DB first, fallback to in-memory."""
    db = get_database()
    query_data = await db.get_query(session_id)

    if query_data:
        results = await db.get_query_results(session_id)
        dim_names = [
            query_data.get("dimension_1_name") or "Score 1",
            query_data.get("dimension_2_name") or "Score 2",
            query_data.get("dimension_3_name") or "Score 3",
        ]
    else:
        results = _session_results.get(session_id, [])
        dim_names = ["Score 1", "Score 2", "Score 3"]

    if not results:
        raise HTTPException(status_code=404, detail="No results found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rank", "Name", "LinkedIn URL", "Title", "Company",
        "Location", "Overall Score", dim_names[0], dim_names[1], dim_names[2],
        "Match Summary", "Headline",
    ])

    for r in results:
        profile = r.get("profile", {})
        evaluation = r.get("evaluation", {})
        sub_scores = evaluation.get("sub_scores", [])

        writer.writerow([
            r.get("rank", ""),
            profile.get("full_name", ""),
            profile.get("linkedin_url", ""),
            profile.get("current_title", ""),
            profile.get("current_company", ""),
            profile.get("location", ""),
            evaluation.get("match_score", ""),
            sub_scores[0].get("score", "") if len(sub_scores) > 0 else "",
            sub_scores[1].get("score", "") if len(sub_scores) > 1 else "",
            sub_scores[2].get("score", "") if len(sub_scores) > 2 else "",
            evaluation.get("summary", ""),
            profile.get("headline", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=linkedin-search-{session_id}.csv"},
    )
