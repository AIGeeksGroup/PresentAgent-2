'use client';

import { usePresenterStore } from '../hooks/usePresenterStore';

export function TopicInput() {
  const {
    topic,
    setTopic,
    videoUrl,
    mdFile,
    setVideoReady,
    setMdFile,
    addToast,
    interactionMode,
    setInteractionMode,
  } = usePresenterStore();

  const handleMdSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setMdFile(file);
    addToast({ type: 'info', message: `Uploading document: ${file.name}...` });

    try {
      const content = await file.text();
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000/api/presenter'}/document/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content,
          filename: file.name,
          force_rebuild: true,
        }),
      });

      const data = await response.json();
      if (data.success) {
        addToast({ type: 'success', message: `Document loaded: ${file.name}` });
      } else {
        addToast({ type: 'error', message: data.error?.message || 'Failed to load document' });
      }
    } catch (error) {
      console.error('Document upload failed:', error);
      addToast({ type: 'error', message: 'Failed to upload document' });
    }
  };

  const handleTopicChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTopic(e.target.value);
  };

  const handleGenerate = () => {
    if (!topic.trim()) {
      addToast({ type: 'warning', message: 'Please enter a presentation topic' });
      return;
    }
    
    addToast({ type: 'info', message: 'Presentation generation coming soon...' });
  };

  const handleVideoSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith('video/')) {
      addToast({ type: 'error', message: 'Please select a valid video file' });
      return;
    }

    const objectUrl = URL.createObjectURL(file);

    // Read real video duration — append to DOM so metadata loads reliably
    let duration = 180;
    let totalPages = 20;

    try {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.muted = true;
      video.style.display = 'none';
      document.body.appendChild(video);
      video.src = objectUrl;

      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error('timeout'));
        }, 5000);

        video.onloadedmetadata = () => {
          clearTimeout(timeout);
          duration = Math.round(video.duration) || 180;
          totalPages = Math.max(1, Math.round(duration / 9));
          document.body.removeChild(video);
          resolve();
        };
        video.onerror = () => {
          clearTimeout(timeout);
          document.body.removeChild(video);
          reject(new Error('metadata'));
        };
      });
    } catch {
      // Fallback to 180s / 20 pages — but still try to load the video
    }

    setVideoReady({
      presentationId: 'local_' + Date.now(),
      videoUrl: objectUrl,
      duration,
      totalPages,
      slides: Array.from({ length: totalPages }, (_, i) => ({
        page: i + 1,
        timestamp: Math.round((i / totalPages) * duration),
      })),
    });
    addToast({ type: 'success', message: `Video loaded: ${file.name}` });
  };

  return (
    <div className="w-full space-y-4">
      {/* Topic + Generate */}
      <div className="flex gap-3 items-center">
        <input
          type="text"
          value={topic}
          onChange={handleTopicChange}
          placeholder="Enter presentation topic..."
          className="flex-1 px-4 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {/* Interaction Mode Toggle */}
        <div className="flex items-center gap-1 bg-zinc-800 rounded-lg p-1 flex-shrink-0">
          <button
            onClick={() => setInteractionMode('single')}
            className={`px-3 py-1 text-sm rounded-md transition-colors ${
              interactionMode === 'single'
                ? 'bg-blue-600 text-white'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Single
          </button>
          <button
            onClick={() => setInteractionMode('discussion')}
            className={`px-3 py-1 text-sm rounded-md transition-colors ${
              interactionMode === 'discussion'
                ? 'bg-blue-600 text-white'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Discussion
          </button>
        </div>
        <button
          onClick={handleGenerate}
          disabled={!topic.trim()}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors whitespace-nowrap"
        >
          Generate
        </button>
      </div>

      {/* Local Files */}
      <div className="flex flex-wrap gap-4">
        {/* Video File */}
        <div className="flex-1 min-w-[200px]">
          <input
            type="file"
            accept="video/*,.mp4,.webm,.mov"
            onChange={handleVideoSelect}
            className="hidden"
            id="video-upload"
          />
          <label
            htmlFor="video-upload"
            className="flex items-center gap-2 px-4 py-2 bg-zinc-800 border border-zinc-700 rounded-lg cursor-pointer hover:bg-zinc-700 transition-colors"
          >
            <svg className="w-5 h-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            <span className="text-zinc-400 text-sm">
              {videoUrl ? 'Select different video' : 'Select video'}
            </span>
          </label>
          {videoUrl && (
            <p className="mt-1 text-xs text-green-400">✓ Loaded</p>
          )}
        </div>

        {/* Document File (MD / JSON) */}
        <div className="flex-1 min-w-[200px]">
          <input
            type="file"
            accept=".md,.markdown,.txt,.json"
            onChange={handleMdSelect}
            className="hidden"
            id="md-upload"
          />
          <label
            htmlFor="md-upload"
            className="flex items-center gap-2 px-4 py-2 bg-zinc-800 border border-zinc-700 rounded-lg cursor-pointer hover:bg-zinc-700 transition-colors"
          >
            <svg className="w-5 h-5 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span className="text-zinc-400 text-sm">
              {mdFile ? 'Select different document' : 'Select document'}
            </span>
          </label>
          {mdFile && (
            <p className="mt-1 text-xs text-green-400">✓ {mdFile.name}</p>
          )}
        </div>
      </div>
    </div>
  );
}
