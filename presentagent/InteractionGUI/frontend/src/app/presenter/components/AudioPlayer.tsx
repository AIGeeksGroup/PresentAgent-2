'use client';

import { useRef, useEffect } from 'react';
import { usePresenterStore } from '../hooks/usePresenterStore';

export function AudioPlayer() {
  const audioRef = useRef<HTMLAudioElement>(null);
  const { lastAudioUrl, isAILoading, messages } = usePresenterStore();

  // Auto-play when new audio URL is available
  useEffect(() => {
    if (lastAudioUrl && audioRef.current) {
      audioRef.current.src = lastAudioUrl;
      audioRef.current.play().catch((err) => {
        console.log('Audio autoplay prevented:', err);
      });
    }
  }, [lastAudioUrl]);

  // If no audio, don't render
  if (!lastAudioUrl) {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 bg-zinc-800 rounded-lg shadow-xl border border-zinc-700 p-3 flex items-center gap-3">
      <div className="flex items-center gap-2">
        <div className={`w-3 h-3 rounded-full ${isAILoading ? 'bg-yellow-400 animate-pulse' : 'bg-green-400'}`} />
        <span className="text-white text-sm">
          {isAILoading ? 'Playing...' : 'AI Voice'}
        </span>
      </div>
      
      <audio ref={audioRef} controls className="h-8" />
    </div>
  );
}
