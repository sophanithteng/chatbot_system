# RAG Chatbot Backend

## Setup

Set `GOOGLE_API_KEY` (a Gemini API key) in the environment or in a `.env` file in the backend directory. Gemini is used for chat; Chroma uses its built-in local ONNX embedding model, avoiding embedding API quotas. The first ingestion downloads the local embedding model. The API requires a populated vector store at `Backend/db/chroma_db`; run `1_ingestion_pipeline.py` after adding `.txt` documents to `Backend/docs`.

Add the key to `Backend/.env` using this variable name:

```env
GOOGLE_API_KEY=your_gemini_api_key
```

The API and ingestion script resolve data paths relative to their own location, so they can be started from any working directory.

Start the API from the project root with:

```powershell
.\Backend\.venv\Scripts\python.exe -m uvicorn api:app --app-dir Backend --host 127.0.0.1 --port 8000
```

The regular `POST /chat` endpoint returns a complete JSON response. `POST /chat/stream` streams newline-delimited JSON events (`metadata`, `token`, `done`, or `error`) for the web chat client. `GET /status` includes the configured model and the document names loaded in the vector store.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_API_KEY` | Required | Google Gemini API key |
| `GEMINI_CHAT_MODEL` | `gemini-3.5-flash-lite` | Gemini chat model |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated frontend origins |
| `SESSION_TTL_SECONDS` | `86400` | Inactive session lifetime |
| `MAX_SESSIONS` | `1000` | Maximum number of retained sessions |
| `MAX_SESSION_MESSAGES` | `100` | Maximum messages retained per session (even number, minimum 2) |

Chat sessions are stored in `Backend/db/chat_sessions.sqlite3` and survive API restarts. Expired sessions and sessions beyond the configured limit are pruned automatically.
