"""
WebSocket Manager - Handles WebSocket connections for real-time video control.

Provides bidirectional communication between server and client,
enabling push-based video seek events.
"""

from __future__ import annotations

from typing import Dict, Set, Optional
from fastapi import WebSocket
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages active WebSocket connections and message broadcasting.
    
    Usage:
        ws_manager = WebSocketManager()
        
        # Connect a new client
        await ws_manager.connect(websocket, session_id="user123")
        
        # Send video seek event
        await ws_manager.send_video_seek("user123", 0.45)  # 45% into video
        
        # Disconnect
        await ws_manager.disconnect(websocket, "user123")
    """
    
    def __init__(self):
        # session_id -> set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        """
        Accept a new WebSocket connection.
        
        Args:
            websocket: FastAPI WebSocket instance
            session_id: Unique session identifier for the client
        """
        await websocket.accept()
        
        async with self._lock:
            if session_id not in self._connections:
                self._connections[session_id] = set()
            self._connections[session_id].add(websocket)
        
        logger.info(f"WebSocket connected: session_id={session_id}")
    
    async def disconnect(self, websocket: WebSocket, session_id: str) -> None:
        """
        Remove a WebSocket connection.
        
        Args:
            websocket: FastAPI WebSocket instance
            session_id: Session identifier
        """
        async with self._lock:
            if session_id in self._connections:
                self._connections[session_id].discard(websocket)
                if not self._connections[session_id]:
                    del self._connections[session_id]
        
        logger.info(f"WebSocket disconnected: session_id={session_id}")
    
    async def send_video_seek(
        self,
        session_id: str,
        position: float,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Send a video seek event to a specific session.
        
        Args:
            session_id: Target session identifier
            position: Position ratio (0.0 - 1.0)
            duration: Optional video duration in seconds
            
        Returns:
            True if message was sent, False if session not found
        """
        message = {
            "type": "video_seek",
            "position": position,
        }
        
        if duration is not None:
            message["timestamp"] = position * duration
        
        return await self._send_json(session_id, message)
    
    async def send_video_status(
        self,
        session_id: str,
        is_playing: bool,
        position: float,
        duration: Optional[float] = None,
    ) -> bool:
        """
        Send video status update to a specific session.
        
        Args:
            session_id: Target session identifier
            is_playing: Whether video is currently playing
            position: Current position ratio (0.0 - 1.0)
            duration: Optional video duration in seconds
        """
        message = {
            "type": "video_status",
            "is_playing": is_playing,
            "position": position,
        }
        
        if duration is not None:
            message["timestamp"] = position * duration
        
        return await self._send_json(session_id, message)
    
    async def send_chat_chunk(
        self,
        session_id: str,
        chunk: str,
        is_final: bool = False,
    ) -> bool:
        """
        Send a chat message chunk to a specific session.
        
        Args:
            session_id: Target session identifier
            chunk: Text chunk
            is_final: Whether this is the final chunk
        """
        message = {
            "type": "chat_chunk",
            "content": chunk,
            "is_final": is_final,
        }
        
        return await self._send_json(session_id, message)
    
    async def send_error(
        self,
        session_id: str,
        error_message: str,
        error_code: Optional[str] = None,
    ) -> bool:
        """
        Send an error message to a specific session.
        
        Args:
            session_id: Target session identifier
            error_message: Human-readable error message
            error_code: Optional error code for programmatic handling
        """
        message = {
            "type": "error",
            "error": error_message,
        }
        
        if error_code:
            message["error_code"] = error_code
        
        return await self._send_json(session_id, message)
    
    async def _send_json(
        self,
        session_id: str,
        data: dict,
    ) -> bool:
        """
        Internal method to send JSON data to a session.
        
        Args:
            session_id: Target session identifier
            data: Dictionary to serialize as JSON
            
        Returns:
            True if sent successfully, False if session not found
        """
        connections_to_remove: Set[WebSocket] = set()
        
        async with self._lock:
            connections = self._connections.get(session_id, set()).copy()
        
        if not connections:
            logger.warning(f"Session not found: {session_id}")
            return False
        
        json_str = json.dumps(data, ensure_ascii=False)
        
        for websocket in connections:
            try:
                await websocket.send_text(json_str)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                connections_to_remove.add(websocket)
        
        # Clean up dead connections
        if connections_to_remove:
            async with self._lock:
                for ws in connections_to_remove:
                    for sid, conns in self._connections.items():
                        conns.discard(ws)
        
        return True
    
    async def broadcast(self, message: dict) -> int:
        """
        Broadcast a message to all connected sessions.
        
        Args:
            message: Dictionary to serialize as JSON
            
        Returns:
            Number of clients that received the message
        """
        count = 0
        async with self._lock:
            session_ids = list(self._connections.keys())
        
        for session_id in session_ids:
            if await self._send_json(session_id, message):
                count += 1
        
        return count
    
    async def get_active_sessions(self) -> list[str]:
        """Get list of active session IDs."""
        async with self._lock:
            return list(self._connections.keys())
    
    async def get_session_count(self) -> int:
        """Get total number of active sessions."""
        async with self._lock:
            return len(self._connections)
    
    async def get_connection_count(self) -> int:
        """Get total number of active connections."""
        async with self._lock:
            return sum(len(conns) for conns in self._connections.values())


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------

_ws_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """Get the global WebSocket manager instance."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager
