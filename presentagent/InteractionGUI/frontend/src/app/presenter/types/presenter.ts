/**
 * Presenter Types - Type definitions for the PresentAgent presenter module.
 */

export interface Message {
  id: string;
  content: string;
  sender: 'user' | 'assistant';
  audioUrl?: string;
  timestamp: number;
}

export interface SlideMap {
  page: number;
  timestamp: number;
}

export interface GenerateResponse {
  success: boolean;
  data: {
    presentation_id: string;
    video_path: string;
    duration: number;
    total_pages: number;
    slides: SlideMap[];
  };
}

export interface ChatResponse {
  success: boolean;
  data: {
    reply: string;
    audio_url: string | null;
  };
}

export interface PageSyncResponse {
  success: boolean;
  data: {
    page: number;
    timestamp: number;
    total_pages: number;
  };
}

export interface GenerateRequest {
  topic: string;
  language?: string;
}

export interface ChatRequest {
  question: string;
  presentation_id?: string;
  current_page?: number;
}

export type GenerationStatus = 'idle' | 'loading' | 'success' | 'error';

export interface ToastMessage {
  id: string;
  type: 'info' | 'success' | 'error' | 'warning';
  message: string;
  duration?: number;
}
