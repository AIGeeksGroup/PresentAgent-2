"""
PresentAgent API Router - FastAPI endpoints for presenter interaction.

This module exposes the core PresentAgent functionality via HTTP API,
reusing the existing PresentAgent class without modifying it.
"""

from __future__ import annotations

import asyncio
import json
import os

def _get_base_url() -> str:
    """Return the base URL for audio URL construction. Read from env or default."""
    return os.environ.get("API_BASE_URL", "http://localhost:8000")
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import mimetypes

from interaction.agent import PresentAgent
from interaction.document_processor import (
    DocumentProcessor,
    SentenceIndex,
    find_best_position,
    needs_video_seek,
    get_cached_index,
    set_cached_index,
)
from api.websocket_manager import get_websocket_manager


# ---------------------------------------------------------------------------
# Singleton agent instance - reused across all requests
# ---------------------------------------------------------------------------
_agent: Optional[PresentAgent] = None
# Track the current uploaded doc path so the agent can reload it
_uploaded_doc_path: Optional[str] = None


def get_agent() -> PresentAgent:
    """Get or create the singleton PresentAgent instance."""
    global _agent
    if _agent is None:
        _agent = PresentAgent()
    return _agent


def reset_agent_doc(doc_path: str) -> None:
    """
    Re-initialize the agent with a new document path.
    The agent is rebuilt so it picks up the new source content.
    Also resets conversation memory to avoid context bleed from previous documents.
    """
    global _agent, _uploaded_doc_path
    _uploaded_doc_path = doc_path
    _agent = PresentAgent(source_md_path=doc_path)
    # Explicitly reset memory to ensure clean slate for new document
    _agent.reset_memory()
    print(f"[INFO] Agent reinitialized with new document: {doc_path}, memory reset.")


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    """Request model for presentation generation."""
    topic: str = Field(..., min_length=1, max_length=500, description="Presentation topic")
    language: str = Field(default="zh", description="Language code: zh or en")


class GenerateResponse(BaseModel):
    """Response model for successful generation."""
    success: bool = True
    data: dict


class ChatRequest(BaseModel):
    """Request model for Q&A chat."""
    question: str = Field(..., min_length=1, max_length=2000, description="User question")
    presentation_id: Optional[str] = Field(default=None, description="Presentation ID context")
    current_page: Optional[int] = Field(default=None, ge=1, description="Current video page")
    include_video_position: bool = Field(default=False, description="Whether to compute video position (slower if True)")


class ChatResponse(BaseModel):
    """Response model for chat."""
    success: bool = True
    data: dict


class PageSyncRequest(BaseModel):
    """Query parameters for page-to-timestamp sync."""
    presentation_id: str
    page: int = Field(..., ge=1, description="Target page number")


class PageSyncResponse(BaseModel):
    """Response model for page sync."""
    success: bool = True
    data: dict


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = False
    error: dict


class DocumentLoadRequest(BaseModel):
    """Request model for loading/document processing."""
    doc_path: Optional[str] = Field(default=None, description="Path to the document")
    force_rebuild: bool = Field(default=False, description="Force rebuild the index")


class _DocumentUploadRequest(BaseModel):
    """Request model for document upload via JSON body (alternative to multipart)."""
    content: str = Field(..., description="Raw file content")
    filename: str = Field(..., description="Original filename (e.g. source.md or slide_notes.json)")
    force_rebuild: bool = Field(default=True, description="Force rebuild sentence index")


class DocumentLoadResponse(BaseModel):
    """Response model for document loading."""
    success: bool = True
    data: dict


class DocumentUploadResponse(BaseModel):
    """Response model for document upload."""
    success: bool = True
    data: dict


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "presenter-api"}


# ---------------------------------------------------------------------------
# Presentation Generation
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateResponse)
async def generate_presentation(req: GenerateRequest):
    """
    Generate a presentation from a topic.

    This endpoint triggers the PPT generation pipeline and returns
    the video URL, duration, and slide mapping.

    TODO: Implement actual PPT generation service integration.
    Currently returns mock data for development.
    """
    from services.generator import GeneratorService

    try:
        generator = GeneratorService()
        result = await generator.generate(
            topic=req.topic,
            language=req.language
        )
        return GenerateResponse(success=True, data=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Q&A Chat (SSE Streaming)
# ---------------------------------------------------------------------------

async def generate_chat_stream(req: ChatRequest) -> AsyncGenerator[str, None]:
    """
    Generate SSE stream for chat responses.
    
    Yields events:
    - message: text chunks from AI
    - audio: audio URL when available
    - done: completion with final data
    """
    try:
        agent = get_agent()
        reply, audio_path = agent.chat_with_audio(req.question)
        
        # Convert local file path to HTTP accessible URL
        # audio_url will be served via /api/presenter/audio/{path} endpoint
        audio_url: Optional[str] = None
        if audio_path and os.path.exists(audio_path):
            rel_path = os.path.relpath(audio_path, Path(__file__).parent.parent / "tts_output")
            audio_url = f"{_get_base_url()}/api/presenter/audio/{rel_path.replace(os.sep, '/')}"
        
        # Send the complete reply
        yield f"event: message\ndata: {json.dumps({'type': 'text', 'content': reply})}\n\n"
        
        # Send audio URL if available
        if audio_url:
            yield f"event: audio\ndata: {json.dumps({'type': 'audio', 'audio_url': audio_url})}\n\n"
        
        # Send completion
        yield f"event: done\ndata: {json.dumps({'type': 'done', 'reply': reply, 'audio_url': audio_url})}\n\n"
        
    except Exception as e:
        error_data = json.dumps({'type': 'error', 'message': str(e)})
        yield f"event: error\ndata: {error_data}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    Process a user question and return AI response as SSE stream.
    
    This endpoint provides real-time streaming of the AI response,
    with events for text chunks, audio URL, and completion.
    """
    return StreamingResponse(
        generate_chat_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Process a user question and return AI response with audio.

    This endpoint directly calls the existing PresentAgent.chat_with_audio()
    method, reusing all existing logic for LLM calls, TTS, and memory management.
    """
    try:
        agent = get_agent()
        reply, audio_path, video_position = agent.chat_with_audio(req.question)

        # Convert local file path to HTTP accessible URL
        # audio_url will be served via /api/presenter/audio/{path} endpoint
        audio_url: Optional[str] = None
        if audio_path and os.path.exists(audio_path):
            rel_path = os.path.relpath(audio_path, Path(__file__).parent.parent / "tts_output")
            audio_url = f"{_get_base_url()}/api/presenter/audio/{rel_path.replace(os.sep, '/')}"

        return ChatResponse(success=True, data={
            "reply": reply,
            "audio_url": audio_url,
            "video_position": video_position,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Page Sync (Timestamp Lookup)
# ---------------------------------------------------------------------------

@router.get("/video/page")
async def get_page_timestamp(
    presentation_id: str,
    page: int
):
    """
    Get the video timestamp for a specific page.

    Uses the slides mapping data to find the exact timestamp,
    or falls back to linear interpolation.
    """
    from services.video_sync import VideoSyncService

    try:
        sync_service = VideoSyncService()
        result = sync_service.get_timestamp_for_page(presentation_id, page)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Audio File Serving
# ---------------------------------------------------------------------------

@router.get("/audio/{path:path}")
async def get_audio(path: str):
    """
    Serve audio files from the tts_output directory.

    Maps URLs like /api/presenter/audio/round_xxx/reply_000.wav
    to files in ./tts_output/round_xxx/reply_000.wav
    """
    # Get the project root directory
    project_root = Path(__file__).parent.parent
    tts_dir = project_root / "tts_output"

    # Construct the file path
    file_path = tts_dir / path

    # Security check: ensure the path is within tts_output
    if not str(file_path.resolve()).startswith(str(tts_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        filename=file_path.name
    )


# ---------------------------------------------------------------------------
# Memory Management (Optional)
# ---------------------------------------------------------------------------

@router.post("/memory/reset")
async def reset_memory():
    """Reset the conversation memory."""
    try:
        agent = get_agent()
        agent.reset_memory()
        return {"success": True, "message": "Memory reset successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/summary")
async def get_memory_summary():
    """Get the current conversation memory summary."""
    try:
        agent = get_agent()
        summary = agent.get_summary()
        return {"success": True, "data": {"summary": summary}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Document Upload (MD / JSON) — must come before /document/load so FastAPI
# matches the more-specific route first
# ---------------------------------------------------------------------------

@router.post("/document/upload", response_model=DocumentUploadResponse)
async def upload_document(req: _DocumentUploadRequest):
    """
    Upload a document (MD or JSON) from the frontend.

    The raw file content is written to a temp directory and the agent is
    reloaded to use the new file as its knowledge base. Both .md and .json
    are supported — the JSON parser extracts text from "notes" fields or
    top-level string values, matching the logic in interaction/agent.py.

    Frontend usage:
        POST /api/presenter/document/upload
        Content-Type: application/json
        Body: { "content": "...", "filename": "source.md" }
    """
    import traceback

    print(f"[DOC UPLOAD] filename={req.filename}, content_len={len(req.content)}")

    try:
        suffix = Path(req.filename).suffix.lower()
        print(f"[DOC UPLOAD] suffix={suffix}")
        if suffix not in (".md", ".markdown", ".txt", ".json"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}. "
                       "Only .md, .markdown, .txt, and .json are accepted.",
            )

        # Write uploaded content to a temp file so the agent can read it
        upload_dir = Path(__file__).parent.parent / "uploads"
        upload_dir.mkdir(exist_ok=True)
        safe_name = req.filename.lstrip("/\\")
        out_path = upload_dir / safe_name
        out_path.write_text(req.content, encoding="utf-8")
        print(f"[DOC UPLOAD] file written: {out_path}")

        # Re-initialise the agent so it picks up the new document
        print(f"[DOC UPLOAD] resetting agent with: {out_path.resolve()}")
        reset_agent_doc(str(out_path.resolve()))
        print("[DOC UPLOAD] agent reset done")

        # Rebuild sentence index so video-seek still works
        from interaction.agent import load_source_md
        from interaction.document_processor import (
            DocumentProcessor,
            set_cached_index,
        )

        text_content, _ = load_source_md(str(out_path.resolve()))
        print(f"[DOC UPLOAD] loaded text content, len={len(text_content)}")

        processor = DocumentProcessor()
        index = processor.build_index(text_content, str(out_path.resolve()))
        cache_dir = Path(__file__).parent.parent / ".cache"
        cache_dir.mkdir(exist_ok=True)
        processor.save_index(str(cache_dir / "sentence_index.json"))
        set_cached_index(index)
        print(f"[DOC UPLOAD] index built: {len(index.sentences)} sentences")

        return DocumentUploadResponse(success=True, data={
            "status": "loaded",
            "doc_path": str(out_path.resolve()),
            "filename": req.filename,
            "sentence_count": len(index.sentences),
            "total_words": index.total_words,
            "model": index.model_name,
        })

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        print(f"[DOC UPLOAD] ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Document Loading / Index Building
# ---------------------------------------------------------------------------

@router.post("/document/load", response_model=DocumentLoadResponse)
async def load_document(
    req: DocumentLoadRequest,
    background_tasks: BackgroundTasks,
):
    """
    Load or rebuild the sentence index for a document.

    If doc_path is not provided, uses the default source.md.
    If force_rebuild is True, always rebuilds the index.
    Otherwise, uses cached index if available and valid.
    """
    from interaction.agent import load_source_md

    try:
        content, resolved_path = load_source_md(req.doc_path)
        doc_path = resolved_path or req.doc_path
        if not content:
            raise HTTPException(status_code=400, detail="No document path provided and source.md not found")

        doc_file = Path(doc_path)
        if not doc_file.exists():
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_path}")

        text_content = content  # Use content from load_source_md

        if not req.force_rebuild:
            cached = get_cached_index()
            if cached and cached.doc_hash == DocumentProcessor().build_index(text_content, str(doc_path)).doc_hash:
                return DocumentLoadResponse(success=True, data={
                    "status": "cached",
                    "doc_path": str(doc_path),
                    "sentence_count": len(cached.sentences),
                    "total_words": cached.total_words,
                })

        processor = DocumentProcessor()
        index = processor.build_index(text_content, str(doc_path))

        cache_dir = Path(__file__).parent.parent / ".cache"
        cache_dir.mkdir(exist_ok=True)
        processor.save_index(str(cache_dir / "sentence_index.json"))
        set_cached_index(index)

        return DocumentLoadResponse(success=True, data={
            "status": "built",
            "doc_path": str(doc_path),
            "sentence_count": len(index.sentences),
            "total_words": index.total_words,
            "model": index.model_name,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Video Seek via WebSocket
# ---------------------------------------------------------------------------

@router.websocket("/ws/video")
async def websocket_video(websocket: WebSocket):
    """
    WebSocket endpoint for real-time video control.

    Client sends:
        - {"type": "register", "session_id": "xxx"}
        - {"type": "video_status", "position": 0.5, "is_playing": true}

    Server sends:
        - {"type": "video_seek", "position": 0.45}
        - {"type": "error", "error": "message"}
    """
    ws_manager = get_websocket_manager()
    session_id = None

    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "register":
                session_id = data.get("session_id", "default")
                await ws_manager.connect(websocket, session_id)
                await websocket.send_json({
                    "type": "registered",
                    "session_id": session_id,
                })

            elif msg_type == "video_status":
                session_id = session_id or data.get("session_id", "default")
                position = data.get("position", 0.0)
                is_playing = data.get("is_playing", False)

                index = get_cached_index()
                if index and index.sentences:
                    from interaction.document_processor import needs_video_seek, find_best_position

                    question = data.get("question", "")
                    if question and needs_video_seek(question):
                        sent_id, pos_ratio = find_best_position(question, index)
                        if pos_ratio is not None:
                            await ws_manager.send_video_seek(session_id, pos_ratio)

            elif msg_type == "seek":
                session_id = session_id or data.get("session_id", "default")
                position = data.get("position", 0.0)
                await ws_manager.send_video_seek(session_id, position)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        if session_id:
            await ws_manager.disconnect(websocket, session_id)
    except Exception as e:
        if session_id:
            await ws_manager.send_error(session_id, str(e))
            await ws_manager.disconnect(websocket, session_id)

