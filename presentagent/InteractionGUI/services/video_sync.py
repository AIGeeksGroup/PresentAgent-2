"""
Video Synchronization Service for PresentAgent.

This module handles the mapping between video timestamps and slide/page numbers.
"""

from typing import Optional
from dataclasses import dataclass


@dataclass
class SlideInfo:
    """Information about a single slide."""
    page: int
    timestamp: float


class VideoSyncService:
    """
    Service for synchronizing video playback with slide/page content.

    Maintains a mapping between page numbers and video timestamps,
    supporting both exact lookups and linear interpolation.
    """

    def __init__(self):
        """Initialize the sync service with empty slides cache."""
        self._presentations: dict[str, list[SlideInfo]] = {}

    def register_presentation(
        self,
        presentation_id: str,
        slides: list[dict],
        duration: float
    ) -> None:
        """
        Register a presentation with its slides mapping.

        Args:
            presentation_id: Unique presentation identifier.
            slides: List of {page, timestamp} dictionaries.
            duration: Total video duration in seconds.
        """
        slide_infos = [
            SlideInfo(page=s["page"], timestamp=s["timestamp"])
            for s in slides
        ]
        self._presentations[presentation_id] = slide_infos

    def get_timestamp_for_page(
        self,
        presentation_id: str,
        page: int
    ) -> dict:
        """
        Get the video timestamp for a specific page.

        Args:
            presentation_id: The presentation ID.
            page: Target page number (1-indexed).

        Returns:
            dict with page, timestamp, and total_pages info.

        Raises:
            ValueError: If presentation not found or page out of range.
        """
        if presentation_id not in self._presentations:
            # Return linear interpolation for unregistered presentations
            # Default: 20 pages, 180.5 seconds duration
            duration = 180.5
            total_pages = 20
            if page < 1 or page > total_pages:
                raise ValueError(f"Page must be between 1 and {total_pages}")
            ratio = (page - 1) / (total_pages - 1) if total_pages > 1 else 0
            timestamp = ratio * duration
            return {
                "page": page,
                "timestamp": round(timestamp, 2),
                "total_pages": total_pages
            }

        slides = self._presentations[presentation_id]
        total_pages = max(s.page for s in slides)

        if page < 1 or page > total_pages:
            raise ValueError(f"Page must be between 1 and {total_pages}")

        # Try exact match first
        for slide in slides:
            if slide.page == page:
                return {
                    "page": page,
                    "timestamp": slide.timestamp,
                    "total_pages": total_pages
                }

        # Linear interpolation between nearest slides
        lower = max((s for s in slides if s.page < page), default=None)
        upper = next((s for s in slides if s.page > page), None)

        if lower is None:
            timestamp = upper.timestamp if upper else 0
        elif upper is None:
            timestamp = lower.timestamp
        else:
            ratio = (page - lower.page) / (upper.page - lower.page)
            timestamp = lower.timestamp + ratio * (upper.timestamp - lower.timestamp)

        return {
            "page": page,
            "timestamp": round(timestamp, 2),
            "total_pages": total_pages
        }

    def get_page_for_timestamp(
        self,
        presentation_id: str,
        timestamp: float
    ) -> int:
        """
        Get the page number for a specific video timestamp.

        Args:
            presentation_id: The presentation ID.
            timestamp: Video timestamp in seconds.

        Returns:
            The page number (1-indexed).
        """
        if presentation_id not in self._presentations:
            # Default linear estimation
            return max(1, min(20, int(timestamp / 9.5) + 1))

        slides = self._presentations[presentation_id]
        total_pages = max(s.page for s in slides)

        # Find the nearest slide
        nearest = min(
            slides,
            key=lambda s: abs(s.timestamp - timestamp)
        )

        return nearest.page

    def clear_presentation(self, presentation_id: str) -> None:
        """Remove a presentation from the cache."""
        if presentation_id in self._presentations:
            del self._presentations[presentation_id]
