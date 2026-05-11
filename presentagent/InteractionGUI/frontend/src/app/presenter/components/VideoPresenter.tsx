'use client';

import { useRef, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react';
import { usePresenterStore } from '../hooks/usePresenterStore';
import { useVideoSync } from '../hooks/useVideoSync';

export interface VideoPresenterHandle {
  seekTo: (seconds: number) => void;
  pause: () => void;
}

export const VideoPresenter = forwardRef<VideoPresenterHandle, { onVideoRef?: (ref: HTMLVideoElement | null) => void }>(
  ({ onVideoRef }, ref) => {
    const videoRef = useRef<HTMLVideoElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    
    const {
      videoUrl,
      isVideoPlaying,
      setVideoPlaying,
      updateVideoTime,
      generationStatus,
      isAILoading,
      lastAudioUrl,
      targetVideoPosition,
      setTargetVideoPosition,
      videoDuration,
    } = usePresenterStore();

    const { updatePageFromTime } = useVideoSync();

    // Expose methods to parent via ref
    useImperativeHandle(ref, () => ({
      seekTo: (seconds: number) => {
        if (videoRef.current) {
          videoRef.current.currentTime = seconds;
        }
      },
      pause: () => {
        if (videoRef.current) {
          videoRef.current.pause();
        }
      },
    }));

    // Get internal video element and expose it
    useEffect(() => {
      if (videoRef.current && onVideoRef) {
        onVideoRef(videoRef.current);
      }
    }, [onVideoRef, videoUrl]);

    const handleTimeUpdate = useCallback(() => {
      if (videoRef.current) {
        const currentTime = videoRef.current.currentTime;
        updateVideoTime(currentTime);
        updatePageFromTime(currentTime);
      }
    }, [updateVideoTime, updatePageFromTime]);

    const handleMetadataLoaded = useCallback(() => {
      // If a target video position was set before metadata loaded, seek now
      if (targetVideoPosition !== null && videoRef.current && videoRef.current.duration > 0) {
        const targetTime = targetVideoPosition * videoRef.current.duration;
        videoRef.current.currentTime = targetTime;
        videoRef.current.pause();
        setTargetVideoPosition(null);
      }
    }, [targetVideoPosition, setTargetVideoPosition]);

    const handlePause = useCallback(() => {
      setVideoPlaying(false);
    }, [setVideoPlaying]);

    const handlePlay = useCallback(() => {
      setVideoPlaying(true);
    }, [setVideoPlaying]);

    // Control video playing state
    useEffect(() => {
      if (videoRef.current) {
        if (isVideoPlaying) {
          videoRef.current.play().catch(() => {});
        } else {
          videoRef.current.pause();
        }
      }
    }, [isVideoPlaying]);

    // Handle video position seek from Q&A
    useEffect(() => {
      if (targetVideoPosition !== null && videoRef.current && videoDuration > 0) {
        const targetTime = targetVideoPosition * videoDuration;
        videoRef.current.currentTime = targetTime;
        videoRef.current.pause();
        setTargetVideoPosition(null);
      }
    }, [targetVideoPosition, videoDuration, setTargetVideoPosition]);

    if (generationStatus === 'idle') {
      return (
        <div className="flex items-center justify-center h-full bg-zinc-900 rounded-xl border-2 border-dashed border-zinc-700">
          <div className="text-center text-zinc-500">
            <svg className="w-16 h-16 mx-auto mb-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p>Enter a topic to generate presentation</p>
          </div>
        </div>
      );
    }

    if (generationStatus === 'loading') {
      return (
        <div className="flex items-center justify-center h-full bg-zinc-900 rounded-xl">
          <div className="text-center">
            <svg className="animate-spin h-12 w-12 mx-auto mb-4 text-blue-500" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <p className="text-zinc-400">Generating presentation...</p>
            <p className="text-zinc-500 text-sm mt-2">This may take a few minutes</p>
          </div>
        </div>
      );
    }

    if (!videoUrl) {
      return (
        <div className="flex items-center justify-center h-full bg-zinc-900 rounded-xl border-2 border-dashed border-zinc-700">
          <div className="text-center text-zinc-500">
            <p>Video failed to load</p>
          </div>
        </div>
      );
    }

  return (
    <div className="relative h-full bg-black rounded-xl overflow-hidden" ref={containerRef}>
        <video
          ref={videoRef}
          src={videoUrl}
          controls
          controlsList="nodownload"
          className="w-full h-full"
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleMetadataLoaded}
          onPlay={handlePlay}
          onPause={handlePause}
          onError={(e) => console.error('Video error:', e)}
        />
      </div>
    );
  }
);

VideoPresenter.displayName = 'VideoPresenter';
