/**
 * Video Sync Hook - Handles page-to-timestamp synchronization.
 */

import { useCallback } from 'react';
import { usePresenterStore } from './usePresenterStore';
import type { SlideMap } from '../types/presenter';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000/api/presenter';

/**
 * Convert page number to video timestamp using linear interpolation.
 */
export function pageToTimestamp(
  page: number,
  totalPages: number,
  duration: number
): number {
  if (page < 1) return 0;
  if (page > totalPages) return duration;
  if (totalPages <= 1) return 0;
  
  const ratio = (page - 1) / (totalPages - 1);
  return ratio * duration;
}

/**
 * Convert video timestamp to page number.
 */
export function timestampToPage(
  timestamp: number,
  totalPages: number,
  duration: number
): number {
  if (duration <= 0) return 1;
  if (timestamp <= 0) return 1;
  if (timestamp >= duration) return totalPages;
  
  const ratio = timestamp / duration;
  return Math.round(ratio * (totalPages - 1)) + 1;
}

/**
 * Find nearest timestamp using slide map data from server.
 */
export function findNearestTimestamp(
  targetPage: number,
  slides: SlideMap[]
): number {
  const exactMatch = slides.find((s) => s.page === targetPage);
  if (exactMatch) return exactMatch.timestamp;
  
  const lower = slides.filter((s) => s.page < targetPage).pop();
  const upper = slides.find((s) => s.page > targetPage);
  
  if (!lower && !upper) return 0;
  if (!lower) return upper!.timestamp;
  if (!upper) return lower.timestamp;
  
  const ratio = (targetPage - lower.page) / (upper.page - lower.page);
  return lower.timestamp + ratio * (upper.timestamp - lower.timestamp);
}

export function useVideoSync() {
  const {
    slides,
    totalPages,
    videoDuration,
    presentationId,
    currentPage,
    setCurrentPage,
    setPageInputError,
    setIsPageSyncing,
    addToast,
  } = usePresenterStore();

  /**
   * Jump to a specific page in the video.
   */
  const jumpToPage = useCallback(
    async (page: number, videoRef: React.RefObject<HTMLVideoElement | null>): Promise<void> => {
      // Validate page number
      if (!Number.isInteger(page) || page < 1 || page > totalPages) {
        setPageInputError(`Please enter an integer between 1 and ${totalPages}`);
        return;
      }

      setPageInputError(null);
      setIsPageSyncing(true);

      try {
        let targetTime: number;

        // Try to get exact timestamp from server
        if (slides.length > 0 && presentationId) {
          try {
            const response = await fetch(
              `${API_BASE}/video/page?presentation_id=${encodeURIComponent(presentationId)}&page=${page}`
            );
            const data = await response.json();
            if (data.success) {
              targetTime = data.data.timestamp;
            } else {
              targetTime = findNearestTimestamp(page, slides);
            }
          } catch {
            // Fallback to local calculation
            targetTime = findNearestTimestamp(page, slides);
          }
        } else {
          // Use local linear algorithm
          targetTime = pageToTimestamp(page, totalPages, videoDuration);
        }

        // Seek video to target time
        if (videoRef.current) {
          videoRef.current.currentTime = targetTime;
          videoRef.current.pause();
        }

        setCurrentPage(page);
      } catch (error) {
        console.error('Page jump failed:', error);
        addToast({ type: 'error', message: 'Page jump failed, please try again' });
      } finally {
        setIsPageSyncing(false);
      }
    },
    [slides, totalPages, videoDuration, presentationId, setCurrentPage, setPageInputError, setIsPageSyncing, addToast]
  );

  /**
   * Update current page based on video time.
   */
  const updatePageFromTime = useCallback(
    (timestamp: number) => {
      if (slides.length > 0) {
        const newPage = slides.reduce((prev, curr) =>
          Math.abs(curr.timestamp - timestamp) < Math.abs(prev.timestamp - timestamp) ? curr : prev
        ).page;
        if (newPage !== currentPage) {
          setCurrentPage(newPage);
        }
      } else {
        const newPage = timestampToPage(timestamp, totalPages, videoDuration);
        if (newPage !== currentPage) {
          setCurrentPage(newPage);
        }
      }
    },
    [slides, totalPages, videoDuration, currentPage, setCurrentPage]
  );

  return {
    jumpToPage,
    updatePageFromTime,
    pageToTimestamp,
    timestampToPage,
    findNearestTimestamp,
  };
}
