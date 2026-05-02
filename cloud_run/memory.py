"""Long-term memory for Zaby. MEMORY.md is the source of truth (human-
readable, editable from the web UI). It is mirrored into a sqlite-vec table
for semantic recall and an FTS5 table for keyword recall, then fused with
reciprocal rank fusion. Inspired by OpenClaw, simplified for one bear."""

import hashlib
import os
import re
import sqlite3
import threading
from pathlib import Path

import sqlite_vec
from google import genai

MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "/mnt/memory"))
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"
DB_FILE = MEMORY_DIR / "memory.db"
EMBED_MODEL = "gemini-embedding-001"  # the embedding model on the API-key (Gemini Developer) endpoint
EMBED_DIM = 3072                       # gemini-embedding-001's native dimensionality
COMPACT_MODEL = "gemini-3-flash-preview"  # used by _compact() to dedup MEMORY.md
COMPACT_EVERY = 5                      # every Nth append() triggers automatic compaction
RRF_K = 60                            # standard reciprocal rank fusion constant
SEED = (
    "# Zaby's Memory\n\n"
    "_Things I've learned about the user and the stories we've made up together._\n\n"
)

COMPACT_PROMPT = """Below is a markdown file of facts a teddy bear has remembered about its user.
Rewrite it as a clean, deduplicated bullet list.

Rules:
- Preserve EVERY unique fact. Do not invent or omit facts.
- Merge near-duplicates into one bullet (e.g. "user likes blue" and
  "user's favorite color is blue" become one bullet).
- One fact per bullet, phrased as a self-contained sentence.
- Keep the title and intro paragraph at the top.
- Output ONLY the rewritten markdown. No preamble, no code fences, no explanation."""


class Memory:
    def __init__(self, client: genai.Client):
        self.client = client
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if not MEMORY_FILE.exists():
            MEMORY_FILE.write_text(SEED)

        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)

        self.conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")

        # Schema versioning: vec0's embedding dim is locked at table creation,
        # so if EMBED_MODEL/EMBED_DIM ever changes we have to drop and rebuild.
        # Track the live dim in `meta`; missing row OR mismatch => self-heal.
        # (Missing row covers both fresh init — DROPs are no-ops — and legacy
        # DBs from before this versioning was added.)
        cur = self.conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
        if not cur or int(cur[0]) != EMBED_DIM:
            self.conn.execute("DROP TABLE IF EXISTS chunks")
            self.conn.execute("DROP TABLE IF EXISTS chunks_fts")
            self.conn.execute("DELETE FROM meta WHERE key='hash'")  # force a reindex

        self.conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING vec0(
                id INTEGER PRIMARY KEY,
                +hash TEXT,
                +text TEXT,
                embedding FLOAT[{EMBED_DIM}]
            )
        """)
        # FTS5 keyword index, parallel to vec0. We manage inserts ourselves
        # so the same integer id is the rowid in both tables.
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(text, tokenize='porter unicode61')"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('embed_dim', ?)",
            (str(EMBED_DIM),),
        )
        self.conn.commit()
        # Index sync is deferred until first search/append/write so module
        # import (and Cloud Run startup) doesn't block on a Gemini API call.

        # Counter for automatic compaction. In-memory only — resets on
        # container recycle, which is fine; compaction is opportunistic.
        self._appends_since_compact = 0
        # Serializes file writes, SQLite writes, and the (background) compact
        # thread. RLock so a single thread can call internals that re-enter.
        self._lock = threading.RLock()

    # ---- public API ----------------------------------------------------

    def read(self) -> str:
        return MEMORY_FILE.read_text()

    def write(self, text: str):
        with self._lock:
            MEMORY_FILE.write_text(text)
            self._reindex()

    def append(self, fact: str):
        line = fact if fact.startswith("- ") else f"- {fact}"
        with self._lock:
            with MEMORY_FILE.open("a") as f:
                f.write(line.rstrip() + "\n")
            self._appends_since_compact += 1
            kick_off_compact = self._appends_since_compact >= COMPACT_EVERY
            if kick_off_compact:
                self._appends_since_compact = 0
            self._reindex()
        # Compaction runs in a daemon thread so save_memory returns immediately
        # and the bear's response stream isn't paused on the LLM rewrite. The
        # thread takes the same lock for the rewrite+reindex; concurrent
        # save_memory / search_memory calls in the same turn briefly wait, but
        # those are rare. Daemon=True so a Cloud Run shutdown isn't blocked.
        if kick_off_compact:
            threading.Thread(target=self._run_compact, daemon=True).start()

    def search(self, query: str, k: int = 3) -> list[str]:
        """Hybrid retrieval: take top-N from each of (vector, FTS5) and fuse
        with reciprocal rank fusion. Catches both 'semantically similar but
        no shared words' (vector wins) and 'rare proper noun like a story
        character name' (FTS5 wins)."""
        with self._lock:
            self._reindex_if_changed()  # lazy catch-up; cheap when nothing changed
            n = k * 3   # over-fetch per side so the fused top-k has room to mix
            emb = self._embed([query])[0]

            vec_ids = [
                r[0] for r in self.conn.execute(
                    "SELECT id, distance FROM chunks "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (sqlite_vec.serialize_float32(emb), n),
                ).fetchall()
            ]

            fts_q = self._fts_query(query)
            fts_ids = [
                r[0] for r in self.conn.execute(
                    "SELECT rowid, bm25(chunks_fts) FROM chunks_fts "
                    "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                    (fts_q, n),
                ).fetchall()
            ] if fts_q else []

            scores: dict[int, float] = {}
            for rank, cid in enumerate(vec_ids):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            for rank, cid in enumerate(fts_ids):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

            top = sorted(scores, key=scores.get, reverse=True)[:k]
            if not top:
                return []
            rows = self.conn.execute(
                f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(top))})",
                top,
            ).fetchall()
            by_id = {r[0]: r[1] for r in rows}
            return [by_id[i] for i in top if i in by_id]

    def _run_compact(self):
        """Background-thread entry point for compaction. Holds the lock for
        the LLM call + file write + reindex so no foreground operation can
        race with the rewrite. Foreground work briefly blocks if it lands
        during the 1–2s window — rare in practice."""
        with self._lock:
            self._compact()
            self._reindex()

    # ---- internals -----------------------------------------------------

    def _hash(self) -> str:
        return hashlib.sha1(MEMORY_FILE.read_bytes()).hexdigest()

    def _reindex_if_changed(self):
        cur = self.conn.execute("SELECT value FROM meta WHERE key='hash'").fetchone()
        if cur and cur[0] == self._hash():
            return
        self._reindex()

    def _chunk(self, text: str) -> list[str]:
        return [p.strip() for p in text.split("\n\n") if p.strip()]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        result = self.client.models.embed_content(model=EMBED_MODEL, contents=texts)
        return [e.values for e in result.embeddings]

    def _fts_query(self, query: str) -> str:
        # FTS5 MATCH is its own mini-language. Strip to alphanumeric tokens,
        # quote each, OR them together so any term hit returns the chunk.
        toks = [t for t in (re.sub(r"\W+", "", w) for w in query.split()) if t]
        return " OR ".join(f'"{t}"' for t in toks)

    def _compact(self):
        """Asks Gemini to rewrite MEMORY.md as a deduplicated bullet list.
        Strict prompt preserves every unique fact; only near-duplicates merge.
        Writes the file directly — the caller's _reindex() picks up the
        rewritten content, with hash-skipping reusing embeddings for any
        paragraph the LLM left untouched."""
        current = MEMORY_FILE.read_text()
        before_chunks = len(self._chunk(current))
        try:
            response = self.client.models.generate_content(
                model=COMPACT_MODEL,
                contents=COMPACT_PROMPT + "\n\n--- Current memory ---\n\n" + current,
            )
            rewritten = (response.text or "").strip()
        except Exception as e:
            # Compaction is opportunistic — never let it break a save_memory call
            print(f"compact: LLM call failed ({e}); leaving memory untouched")
            return
        if not rewritten:
            print("compact: empty model response; leaving memory untouched")
            return
        MEMORY_FILE.write_text(rewritten + "\n")
        after_chunks = len(self._chunk(rewritten))
        print(f"compact: {before_chunks} -> {after_chunks} chunks")

    def _reindex(self):
        chunks = self._chunk(MEMORY_FILE.read_text())
        new_hashes = [hashlib.sha1(c.encode()).hexdigest() for c in chunks]

        # Per-chunk hash skipping: keep existing embeddings as BLOBs keyed by
        # chunk hash, so unchanged paragraphs cost zero embedding API calls.
        cached: dict[str, bytes] = {
            h: blob for h, blob in self.conn.execute(
                "SELECT hash, embedding FROM chunks"
            ).fetchall()
        }
        missing_idx = [i for i, h in enumerate(new_hashes) if h not in cached]
        if missing_idx:
            fresh = self._embed([chunks[i] for i in missing_idx])
            for idx, emb in zip(missing_idx, fresh):
                cached[new_hashes[idx]] = sqlite_vec.serialize_float32(emb)

        self.conn.execute("DELETE FROM chunks")
        self.conn.execute("DELETE FROM chunks_fts")
        for i, (chunk, h) in enumerate(zip(chunks, new_hashes)):
            self.conn.execute(
                "INSERT INTO chunks(id, hash, text, embedding) VALUES (?, ?, ?, ?)",
                (i, h, chunk, cached[h]),
            )
            self.conn.execute(
                "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                (i, chunk),
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('hash', ?)",
            (self._hash(),),
        )
        self.conn.commit()
