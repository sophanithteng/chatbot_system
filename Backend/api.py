"""
RAG Chatbot API - FastAPI Backend
Exposes the history-aware RAG pipeline via REST API endpoints.
"""

import logging
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path
from threading import RLock
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import StreamingResponse

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

BACKEND_DIRECTORY = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIRECTORY / ".env")

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Global State
# ─────────────────────────────────────────────

db: Optional[Chroma] = None
llm: Optional[ChatGoogleGenerativeAI] = None
document_names: list[str] = []

PERSISTENT_DIRECTORY = BACKEND_DIRECTORY / "db" / "chroma_db"
VECTOR_COLLECTION = "rag_local_embeddings"
SESSION_DATABASE = BACKEND_DIRECTORY / "db" / "chat_sessions.sqlite3"


class SessionStore:
    def __init__(
        self,
        database_path: Path,
        ttl_seconds: int,
        max_sessions: int,
        max_messages: int,
    ):
        if (
            ttl_seconds < 1
            or max_sessions < 1
            or max_messages < 2
            or max_messages % 2 != 0
        ):
            raise ValueError(
                "Session TTL and session limits must be positive; "
                "MAX_SESSION_MESSAGES must be at least 2 and even"
            )
        self.database_path = database_path
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self._lock = RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY (session_id, position)
                )
                """
            )
            connection.execute("PRAGMA foreign_keys = ON")
            self._prune(connection, time.time())
            connection.commit()

    def _prune(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "DELETE FROM sessions WHERE updated_at < ?",
            (now - self.ttl_seconds,),
        )
        connection.execute(
            """
            DELETE FROM sessions
            WHERE id IN (
                SELECT id FROM sessions
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_sessions,),
        )

    def get_or_create(self, requested_id: Optional[str]) -> tuple[str, list]:
        now = time.time()
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._prune(connection, now)
            sid = requested_id or str(uuid.uuid4())
            exists = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            if exists:
                connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid)
                )
            else:
                connection.execute(
                    "INSERT INTO sessions (id, updated_at) VALUES (?, ?)", (sid, now)
                )

            rows = connection.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY position",
                (sid,),
            ).fetchall()
            connection.commit()

        history = [
            HumanMessage(content=content) if role == "user" else AIMessage(content=content)
            for role, content in rows
        ]
        return sid, history

    def save_turn(self, sid: str, user_message: str, answer: str) -> None:
        now = time.time()
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            session = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            if not session:
                return

            position = connection.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM messages WHERE session_id = ?",
                (sid,),
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO messages (session_id, position, role, content) VALUES (?, ?, ?, ?)",
                [
                    (sid, position, "user", user_message),
                    (sid, position + 1, "assistant", answer),
                ],
            )
            connection.execute(
                """
                DELETE FROM messages
                WHERE session_id = ? AND position < (
                    SELECT COALESCE(MAX(position) + 1, 0) - ? FROM messages
                    WHERE session_id = ?
                )
                """,
                (sid, self.max_messages, sid),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid)
            )
            self._prune(connection, now)
            connection.commit()

    def list_sessions(self) -> list[tuple[str, int]]:
        now = time.time()
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._prune(connection, now)
            rows = connection.execute(
                """
                SELECT sessions.id, COUNT(messages.position)
                FROM sessions LEFT JOIN messages ON messages.session_id = sessions.id
                GROUP BY sessions.id ORDER BY sessions.updated_at DESC
                """
            ).fetchall()
            connection.commit()
        return rows

    def delete(self, sid: str) -> bool:
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            connection.commit()
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM sessions")
            connection.commit()

    def count(self) -> int:
        now = time.time()
        with self._lock, closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self._prune(connection, now)
            count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            connection.commit()
        return count


session_store = SessionStore(
    database_path=SESSION_DATABASE,
    ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "86400")),
    max_sessions=int(os.getenv("MAX_SESSIONS", "1000")),
    max_messages=int(os.getenv("MAX_SESSION_MESSAGES", "100")),
)


# ─────────────────────────────────────────────
# Lifespan: initialise resources on startup
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, llm, document_names
    load_dotenv(BACKEND_DIRECTORY / ".env")
    logger.info("Starting RAG Chatbot API")

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is missing. Set it to your Gemini API key in Backend/.env."
        )

    try:
        session_store.initialize()
        chat_model = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")

        if not PERSISTENT_DIRECTORY.is_dir():
            raise FileNotFoundError(
                f"Vector store not found at {PERSISTENT_DIRECTORY}. Run the ingestion pipeline first."
            )

        db = Chroma(
            persist_directory=str(PERSISTENT_DIRECTORY),
            collection_name=VECTOR_COLLECTION,
            collection_metadata={"hnsw:space": "cosine"},
        )
        count = db._collection.count()
        if count == 0:
            raise RuntimeError("The vector store contains no document chunks")
        metadata = db.get(include=["metadatas"]).get("metadatas") or []
        document_names = sorted({
            Path(source).stem
            for item in metadata
            if item and isinstance((source := item.get("source")), str) and source
        })
        llm = ChatGoogleGenerativeAI(
            model=chat_model,
            temperature=0.3,
            api_key=api_key,
        )
        logger.info(
            "RAG API ready (chat=%s, local Chroma embeddings, chunks=%d)",
            chat_model,
            count,
        )
    except Exception:
        db = None
        llm = None
        document_names = []
        logger.exception("RAG API startup failed")
        raise

    yield
    logger.info("Shutting down RAG Chatbot API")



# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────

app = FastAPI(
    title="RAG Chatbot API",
    description="A history-aware Retrieval-Augmented Generation chatbot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank")
        return value


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[str]
    search_query: str


class SessionInfo(BaseModel):
    session_id: str
    message_count: int


class StatusResponse(BaseModel):
    status: str
    vector_store_loaded: bool
    document_count: int
    active_sessions: int
    documents: list[str]
    model: str


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

async def _ask_question(
    user_question: str, chat_history: list
) -> tuple[str, str, list[str]]:
    """
    Run one RAG turn. Returns (answer, search_query_used, source_list).
    """
    search_query, sources, messages = await _prepare_question(user_question, chat_history)
    result = await llm.ainvoke(messages)
    answer = result.content
    return answer, search_query, sources


async def _prepare_question(
    user_question: str, chat_history: list
) -> tuple[str, list[str], list]:
    if db is None or llm is None:
        raise HTTPException(
            status_code=503,
            detail="Service unavailable. Check that GOOGLE_API_KEY is set and the ingestion pipeline has been run.",
        )

    # Step 1 – Rewrite question for standalone search
    if chat_history:
        messages = [
            SystemMessage(
                content=(
                    "Given the chat history, rewrite the new question to be "
                    "standalone and searchable. Just return the rewritten question, "
                    "nothing else."
                )
            )
        ] + chat_history + [HumanMessage(content=f"New question: {user_question}")]
        result = await llm.ainvoke(messages)
        search_query = result.content.strip()
    else:
        search_query = user_question

    # Step 2 – Retrieve relevant documents
    retriever = db.as_retriever(search_kwargs={"k": 5})
    docs = await retriever.ainvoke(search_query)
    sources = sorted({doc.metadata.get("source", "unknown") for doc in docs})

    # Step 3 – Build prompt and generate answer
    context = "\n".join([f"- {doc.page_content}" for doc in docs])
    combined_input = (
        f"Based on the following documents, please answer this question: {user_question}\n\n"
        f"Documents:\n{context}\n\n"
        "Please provide a clear, helpful answer using only the information from these "
        "documents. If you can't find the answer in the documents, say "
        '"I don\'t have enough information to answer that question based on the provided documents."'
    )

    messages = [
        SystemMessage(
            content="You are a helpful assistant that answers questions based on provided documents and conversation history."
        )
    ] + chat_history + [HumanMessage(content=combined_input)]

    return search_query, sources, messages


def _chunk_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    return {"message": "RAG Chatbot API is running!", "docs": "/docs"}


@app.get("/status", response_model=StatusResponse, tags=["Health"])
async def status():
    """Health-check and info about the loaded vector store."""
    loaded = db is not None
    count = db._collection.count() if loaded else 0
    return StatusResponse(
        status="ok" if loaded and llm is not None else "unavailable",
        vector_store_loaded=loaded,
        document_count=count,
        active_sessions=session_store.count(),
        documents=document_names,
        model=os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite"),
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message and receive an AI answer grounded in your documents.
    Pass `session_id` to continue an existing conversation.
    Omit `session_id` (or pass null) to start a new session.
    """
    sid, history = session_store.get_or_create(request.session_id)

    try:
        answer, search_query, sources = await _ask_question(request.message, history)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to process chat request")
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    session_store.save_turn(sid, request.message, answer)

    return ChatResponse(
        session_id=sid,
        answer=answer,
        sources=sources,
        search_query=search_query,
    )


@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest):
    """Stream a grounded answer as newline-delimited JSON events."""
    sid, history = session_store.get_or_create(request.session_id)
    try:
        search_query, sources, messages = await _prepare_question(request.message, history)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to prepare streaming chat request")
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    async def events():
        yield json.dumps({
            "type": "metadata",
            "session_id": sid,
            "sources": sources,
            "search_query": search_query,
        }, ensure_ascii=False) + "\n"

        answer_parts = []
        try:
            async for chunk in llm.astream(messages):
                text = _chunk_text(chunk.content)
                if text:
                    answer_parts.append(text)
                    yield json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n"

            session_store.save_turn(sid, request.message, "".join(answer_parts))
            yield json.dumps({"type": "done"}) + "\n"
        except Exception as exc:
            logger.exception("Failed to stream chat response")
            yield json.dumps({
                "type": "error",
                "detail": f"{type(exc).__name__}: {exc}",
            }) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sessions", response_model=list[SessionInfo], tags=["Sessions"])
async def list_sessions():
    """List all active chat sessions."""
    return [
        SessionInfo(session_id=sid, message_count=message_count)
        for sid, message_count in session_store.list_sessions()
    ]


@app.delete("/sessions/{session_id}", tags=["Sessions"])
async def delete_session(session_id: str):
    """Clear a chat session's history."""
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"message": f"Session {session_id} deleted."}


@app.delete("/sessions", tags=["Sessions"])
async def clear_all_sessions():
    """Clear ALL chat sessions."""
    session_store.clear()
    return {"message": "All sessions cleared."}