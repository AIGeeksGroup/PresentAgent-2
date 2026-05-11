'use client';

import { useState, useRef, useEffect } from 'react';
import { usePresenterStore } from '../hooks/usePresenterStore';
import { useQAStream } from '../hooks/useQAStream';

// Test questions list (general examples)
const TEST_QUESTIONS = [
  "What are the key points covered in this presentation?",
  "How does the methodology in this work compare to traditional approaches?",
  "What are the main findings or conclusions presented?",
];

export function QASession() {
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  const { messages, isAILoading, generationStatus } = usePresenterStore();
  const { sendQuestion } = useQAStream();

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isAILoading) return;

    const question = inputValue.trim();
    setInputValue('');
    await sendQuestion(question);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  // Quick send test questions
  const handleQuickTest = async (question: string) => {
    if (isAILoading) return;
    setInputValue(question);
    await sendQuestion(question);
    setInputValue('');
  };

  const isDisabled = generationStatus !== 'success' || isAILoading;

  return (
    <div className="flex flex-col h-full bg-zinc-900 rounded-xl border border-zinc-700">
      {/* Header */}
      <div className="px-4 py-3 border-b border-zinc-700">
        <h2 className="text-white font-medium">Intelligent Q&A</h2>
        <p className="text-zinc-500 text-sm mt-1">Ask questions about the presentation content</p>
      </div>

      {/* Test Questions (only show when no messages yet) */}
      {messages.length === 0 && generationStatus === 'success' && (
        <div className="px-4 py-3 border-b border-zinc-700 bg-zinc-800/50">
          <p className="text-zinc-400 text-xs mb-2">Quick test questions:</p>
          <div className="flex flex-wrap gap-2">
            {TEST_QUESTIONS.map((q, i) => (
              <button
                key={i}
                onClick={() => handleQuickTest(q)}
                disabled={isAILoading}
                className="px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white text-xs rounded-lg transition-colors text-left truncate max-w-[200px]"
                title={q}
              >
                {q.length > 40 ? q.substring(0, 40) + '...' : q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-zinc-500">
            <svg className="w-12 h-12 mb-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <p>Start a conversation, ask questions about the presentation</p>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-xl px-4 py-3 ${
                  msg.sender === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-zinc-800 text-zinc-100'
                }`}
              >
                {msg.sender === 'assistant' && (
                  <div className="flex items-center gap-2 mb-1 text-zinc-400 text-xs">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                    AI Assistant
                    {msg.audioUrl && (
                      <span className="text-green-400 ml-2">🔊 Audio generated</span>
                    )}
                  </div>
                )}
                <p className="whitespace-pre-wrap">{msg.content || (isAILoading && msg.sender === 'assistant' ? 'Thinking...' : '')}</p>
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-zinc-700">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isDisabled ? 'Generate presentation first' : 'Type your question...'}
            disabled={isDisabled}
            rows={2}
            className="flex-1 px-4 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={isDisabled || !inputValue.trim()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors self-end"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </form>
        <p className="text-zinc-500 text-xs mt-2">Press Enter to send, Shift+Enter for new line</p>
      </div>
    </div>
  );
}
