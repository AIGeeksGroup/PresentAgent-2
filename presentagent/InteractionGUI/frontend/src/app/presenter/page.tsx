'use client';

import { useRef, useCallback } from 'react';
import { TopicInput } from './components/TopicInput';
import { VideoPresenter } from './components/VideoPresenter';
import { QASession } from './components/QASession';
import { AudioPlayer } from './components/AudioPlayer';
import { StatusToast } from './components/StatusToast';
import { usePresenterStore } from './hooks/usePresenterStore';

export default function PresenterPage() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const { generationStatus } = usePresenterStore();

  const handleVideoRef = useCallback((ref: HTMLVideoElement | null) => {
    videoRef.current = ref;
  }, []);

  return (
    <div className="fixed inset-0 flex flex-col bg-zinc-950 text-white">
      {/* Header */}
      <header className="w-full p-4 border-b border-zinc-800 bg-zinc-900/50 flex-shrink-0">
        <div className="max-w-screen-2xl mx-auto">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <div>
                <h1 className="text-xl font-bold">PresentAgent-2</h1>
                <p className="text-zinc-500 text-sm">Presentation Generation & Q&A</p>
              </div>
            </div>
            
            {generationStatus === 'success' && (
              <div className="flex items-center gap-2 text-green-400 text-sm">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                </span>
                Online
              </div>
            )}
          </div>
          
          <TopicInput />
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 p-4 min-h-0 flex flex-col">
        <div className="max-w-screen-2xl mx-auto w-full h-full flex flex-col">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0">
            {/* Left: Video */}
            <section className="lg:col-span-2 min-h-0">
              <div className="h-full min-h-0">
                <VideoPresenter onVideoRef={handleVideoRef} />
              </div>
            </section>

            {/* Right: Q&A Session */}
            <aside className="lg:col-span-1 min-h-0">
              <QASession />
            </aside>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="w-full p-4 border-t border-zinc-800 bg-zinc-900/50 flex-shrink-0">
        <div className="max-w-screen-2xl mx-auto text-center text-zinc-500 text-sm">
          PresentAgent-2 © 2026 - Presentation & Q&A System
        </div>
      </footer>

      {/* Audio Player (floating) */}
      <AudioPlayer />

      {/* Toast Notifications */}
      <StatusToast />
    </div>
  );
}
