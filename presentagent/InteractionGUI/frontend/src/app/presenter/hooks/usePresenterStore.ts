/**
 * Presenter Store - Global state management for PresentAgent-2 presenter.
 */

import { create } from 'zustand';
import type { Message, SlideMap, GenerationStatus, ToastMessage } from '../types/presenter';

interface PresenterState {
  // Topic related
  topic: string;
  isGenerating: boolean;
  generationStatus: GenerationStatus;
  errorMessage: string | null;
  presentationId: string | null;

  // Interaction mode
  interactionMode: 'single' | 'discussion';

  // Video related
  videoUrl: string;
  videoDuration: number;
  currentVideoTime: number;
  isVideoPlaying: boolean;

  // Local files
  mdFile: File | null;

  // Page related
  totalPages: number;
  currentPage: number;
  slides: SlideMap[];
  isPageSyncing: boolean;
  pageInputError: string | null;

  // Q&A related
  messages: Message[];
  isAILoading: boolean;
  lastAudioUrl: string | null;
  targetVideoPosition: number | null;

  // Toast notifications
  toasts: ToastMessage[];

  // Actions
  setTopic: (topic: string) => void;
  setInteractionMode: (mode: 'single' | 'discussion') => void;
  startGeneration: () => void;
  setVideoReady: (data: {
    presentationId: string;
    videoUrl: string;
    duration: number;
    totalPages: number;
    slides: SlideMap[];
  }) => void;
  setGenerationError: (error: string) => void;
  updateVideoTime: (time: number) => void;
  setVideoPlaying: (playing: boolean) => void;
  setCurrentPage: (page: number) => void;
  setPageInputError: (error: string | null) => void;
  setIsPageSyncing: (syncing: boolean) => void;
  addMessage: (msg: Omit<Message, 'id' | 'timestamp'>) => void;
  updateLastAssistantMessage: (content: string) => void;
  setAILoading: (loading: boolean) => void;
  setLastAudio: (url: string) => void;
  addToast: (toast: Omit<ToastMessage, 'id'>) => void;
  removeToast: (id: string) => void;
  setMdFile: (file: File | null) => void;
  setTargetVideoPosition: (position: number | null) => void;
  reset: () => void;
}

const generateId = () => Math.random().toString(36).substring(2, 9);

const initialState = {
  topic: '',
  isGenerating: false,
  generationStatus: 'idle' as GenerationStatus,
  errorMessage: null,
  presentationId: null,
  interactionMode: 'single' as 'single' | 'discussion',
  videoUrl: '',
  videoDuration: 0,
  currentVideoTime: 0,
  isVideoPlaying: false,
  totalPages: 0,
  currentPage: 1,
  slides: [],
  isPageSyncing: false,
  pageInputError: null,
  messages: [],
  isAILoading: false,
  lastAudioUrl: null,
  targetVideoPosition: null,
  toasts: [],
  mdFile: null,
};

export const usePresenterStore = create<PresenterState>((set, get) => ({
  ...initialState,

  setTopic: (topic: string) => set({ topic }),

  setInteractionMode: (mode: 'single' | 'discussion') => set({ interactionMode: mode }),

  startGeneration: () => set({
    isGenerating: true,
    generationStatus: 'loading',
    errorMessage: null,
  }),

  setVideoReady: (data) => set({
    isGenerating: false,
    generationStatus: 'success',
    presentationId: data.presentationId,
    videoUrl: data.videoUrl,
    videoDuration: data.duration,
    totalPages: data.totalPages,
    slides: data.slides,
    currentPage: 1,
    errorMessage: null,
  }),

  setGenerationError: (error: string) => set({
    isGenerating: false,
    generationStatus: 'error',
    errorMessage: error,
  }),

  updateVideoTime: (time: number) => set({ currentVideoTime: time }),

  setVideoPlaying: (playing: boolean) => set({ isVideoPlaying: playing }),

  setCurrentPage: (page: number) => set({ currentPage: page }),

  setPageInputError: (error: string | null) => set({ pageInputError: error }),

  setIsPageSyncing: (syncing: boolean) => set({ isPageSyncing: syncing }),

  addMessage: (msg) => set((state) => ({
    messages: [...state.messages, {
      ...msg,
      id: generateId(),
      timestamp: Date.now(),
    }],
  })),

  updateLastAssistantMessage: (content: string) => set((state) => {
    const messages = [...state.messages];
    const lastIndex = messages.length - 1;
    if (lastIndex >= 0 && messages[lastIndex].sender === 'assistant') {
      messages[lastIndex] = { ...messages[lastIndex], content };
    }
    return { messages };
  }),

  setAILoading: (loading: boolean) => set({ isAILoading: loading }),

  setLastAudio: (url: string) => set({ lastAudioUrl: url }),

  addToast: (toast) => {
    const id = generateId();
    const fullToast = { ...toast, id };
    set((state) => ({ toasts: [...state.toasts, fullToast] }));
    
    // Auto-remove after duration
    const duration = toast.duration ?? 3000;
    if (duration > 0) {
      setTimeout(() => {
        get().removeToast(id);
      }, duration);
    }
  },

  removeToast: (id: string) => set((state) => ({
    toasts: state.toasts.filter((t) => t.id !== id),
  })),

  setMdFile: (file: File | null) => set({ mdFile: file }),

  setTargetVideoPosition: (position: number | null) => set({ targetVideoPosition: position }),

  reset: () => set(initialState),
}));
