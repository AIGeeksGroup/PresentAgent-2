# PresentAgent-2 — InteractionGUI

Interactive presentation viewer with real-time Q&A powered by AutoGen agents.

Given a presentation video and its accompanying document (`.md` or `.json`), the system lets audiences ask natural-language questions and receives **text + synthesized speech** answers — while automatically seeking the video to the relevant section.

---

## Features

- **Video + Q&A Panel** — Play any local video alongside a conversational Q&A interface
- **LLM-powered Answers** — Questions are answered by a single ConversableAgent using a compatible LLM
- **Text + Audio** — Each answer is returned as both text and a WAV audio file
- **Auto Video Seeking** — Uses sentence embeddings (all-MiniLM-L6-v2) and LLM-based paragraph localization to jump the video to the right section
- **Auto-Summarizing Memory** — Older conversation rounds are compressed after N turns to stay within token limits
- **Three Delivery Modes** — Single presentation, multi-speaker discussion, and grounded interactive Q&A (interaction mode is used by this GUI)
- **Local Video Support** — Load any `.mp4/.webm/.mov` video directly from disk
- **Document Upload** — Upload `.md`, `.json`, `.txt` knowledge-base files at runtime

---

## Project Structure

```
InteractionGUI/
├── main_api.py                # FastAPI server entry point
├── requirements.txt           # Python dependencies
├── .env                       # API keys & paths (do NOT commit)
├── .env.example               # Template for .env
├── .gitignore                 # Git ignore patterns
├── interaction/               # Core agent package
│   ├── __init__.py
│   ├── agent.py               # PresentAgent class + LLM calls + WAV synthesis
│   ├── config.py              # Environment variable loader
│   ├── document_processor.py  # Sentence embedding index + video position lookup
│   └── memory.py              # ConversationMemory + ContextSummarizer
├── api/                       # FastAPI endpoints
│   ├── __init__.py
│   ├── presenter.py           # /generate, /chat, /document/upload, /ws/video
│   └── websocket_manager.py   # WebSocket session manager
├── services/                  # Supporting services
│   ├── __init__.py
│   ├── generator.py           # PPT generation (stub/mock)
│   └── video_sync.py          # Page-to-timestamp sync
└── frontend/src/              # Next.js UI
    └── app/
        ├── globals.css
        ├── layout.tsx
        ├── page.tsx           # Root redirect → /presenter
        ├── favicon.ico
        └── presenter/
            ├── page.tsx       # Main presenter page
            ├── components/
            │   ├── AudioPlayer.tsx
            │   ├── QASession.tsx
            │   ├── StatusToast.tsx
            │   ├── TopicInput.tsx
            │   ├── VideoPresenter.tsx
            │   └── index.ts
            ├── hooks/
            │   ├── usePresenterStore.ts
            │   ├── useQAStream.ts
            │   ├── useVideoSync.ts
            │   └── index.ts
            └── types/
                └── presenter.ts
```

---

## Setup

### 1. Install Backend Dependencies

```bash
cd presentagent/InteractionGUI
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your API credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM API configuration (OpenAI-compatible)
ANTHROPIC_AUTH_TOKEN=your_api_key_here
ANTHROPIC_PROVIDER=your_provider_here
ANTHROPIC_BASE_URL=https://api.example.com/v1
ANTHROPIC_MODEL=your_model_name
ANTHROPIC_TEMPERATURE=0.7

# Knowledge base — the document that the agent uses to answer questions
SOURCE_MD_PATH=./source/202605032000/source.md

# Audio output directory (auto-created)
TTS_OUTPUT_DIR=./tts_output
```

> **Security note:** Never commit `.env` to version control. It contains your API key.

### 3. Install Frontend Dependencies

```bash
cd presentagent/InteractionGUI/frontend
npm install
```

---

## Running

### Backend (FastAPI)

```bash
cd presentagent/InteractionGUI
python main_api.py
```

Or with uvicorn directly:

```bash
uvicorn main_api:app --reload --port 8000 --host 0.0.0.0
```

The API docs are available at `http://localhost:8000/docs`.

### Frontend (Next.js)

```bash
cd presentagent/InteractionGUI/frontend
npm run dev
```

Open `http://localhost:3000` in your browser.

---

## Usage

1. **Select a video** — click "Select video" to load any local `.mp4/.webm/.mov` file
2. **Upload a document** — click "Select document" to upload a `.md`, `.txt`, or `.json` knowledge-base file; the agent uses this to answer questions
3. **Ask questions** — type in the Q&A panel; the agent replies with text + synthesized audio, and the video seeks to the relevant section
4. **Play audio** — click the floating audio player to hear the AI's spoken answer

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `ag2` | AutoGen agent framework |
| `openai` | Compatible LLM API (OpenAI-compatible) |
| `tiktoken` | Token counting for memory management |
| `sentence-transformers` | all-MiniLM-L6-v2 for sentence embeddings |
| `fastapi` + `uvicorn` | REST API server |
| `websockets` | Real-time video control |
| `sse-starlette` | Server-Sent Events streaming |
| `next` + `react` + `zustand` | Frontend UI |

---

## Citation

If you use this work, please cite:

```bibtex
@article{wu2025presentagent2,
  title={PresentAgent-2: Towards Generalist Multimodal Presentation Agents},
  author={Wu, Wei and Xu, Ziyang and Zhang, Zeyu and Zhao, Yang and Tang, Hao},
  journal={Tech Report},
  year={2026},
}
```
