/**
 * QA Stream Hook - Handles Q&A streaming communication with backend.
 */

import { useCallback, useRef } from 'react';
import { usePresenterStore } from './usePresenterStore';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000/api/presenter';

export function useQAStream() {
  const {
    presentationId,
    currentPage,
    addMessage,
    setAILoading,
    setLastAudio,
    addToast,
    setTargetVideoPosition,
    messages,
  } = usePresenterStore();

  // Use ref to track message count for this request
  const pendingRef = useRef<number>(0);

  /**
   * Send a question and get AI response.
   */
  const sendQuestion = useCallback(
    async (question: string): Promise<void> => {
      if (!question.trim()) return;

      // Add user message
      addMessage({
        content: question,
        sender: 'user',
      });

      setAILoading(true);

      try {
        const response = await fetch(`${API_BASE}/chat`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            question,
            presentation_id: presentationId,
            current_page: currentPage,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const data = await response.json();

        if (data.success) {
          // Add assistant response as a new message
          addMessage({
            content: data.data.reply,
            sender: 'assistant',
            audioUrl: data.data.audio_url,
          });

          // Update last audio URL
          if (data.data.audio_url) {
            setLastAudio(data.data.audio_url);
          }

          // Handle video position seek if available
          if (data.data.video_position !== null && data.data.video_position !== undefined) {
            setTargetVideoPosition(data.data.video_position);
          }
        } else {
          throw new Error(data.error?.message || 'Unknown error');
        }
      } catch (error) {
        console.error('Q&A request failed:', error);
        addToast({ type: 'error', message: 'Network error, please try again later' });
      } finally {
        setAILoading(false);
      }
    },
    [presentationId, currentPage, addMessage, setAILoading, setLastAudio, addToast, setTargetVideoPosition]
  );

  return {
    sendQuestion,
  };
}
