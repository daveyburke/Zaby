# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zaby is an AI-powered animatronic teddy bear. The heavy lifting (Google Cloud Speech-to-Text, Gemini 3 Flash agent, Google Cloud Text-to-Speech) runs on a GCP Cloud Run server. The Raspberry Pi 5 in the bear's backpack streams microphone PCM to the server over a WebSocket and plays back synthesized PCM as it arrives, while driving motors for envelope-tracked mouth movement and neck rotation.

## Development Commands

### Pi-side setup
```bash
python -m venv zaby-env
source zaby-env/bin/activate
pip install -r requirements.txt
export ZABY_SERVER_URL=https://zaby-server-xxx.run.app
python main.py
```

### Server deploy
```bash
cd cloud_run
GEMINI_API_KEY=<key> ./deploy.sh
```

### Mac-side smoke test (no GPIO)
```bash
export ZABY_SERVER_URL=https://zaby-server-xxx.run.app
python test_client.py [speak|converse|both]
```

### Systemd service (Pi)
```bash
sudo systemctl start zaby.service
sudo systemctl stop zaby.service
sudo journalctl -u zaby.service -f
```
`/etc/zaby.env` must contain `ZABY_SERVER_URL=...`.

## Architecture

### Pi side (`main.py`, `conversation_client.py`, `bear_state.py`, `bear_animatronics.py`)

**main.py** — entry point. Generates a fresh `client_id` (UUID) each boot and sends it on the WS. The server no longer keys *agent state* by it (single-user / single-bear), but does use a change in `client_id` as a "Pi rebooted" signal to wipe in-RAM conversation history. Long-term memory in `MEMORY.md` survives the reset. Wires up the animatronics, `ConversationClient`, and `BearOnOffState`, then runs the main loop.

**conversation_client.py** — WebSocket client for the server. `converse()` opens `/converse`, streams mic PCM up on a send thread, and receives four kinds of frames back: `transcript` (text), `response` (text), PCM binary frames, and `done` (with `go_to_sleep` / `power_down` flags). PCM bytes are drained into `pcm_q` at network speed by the recv loop; a separate `_playback_loop` thread pulls from the queue and writes to PyAudio at real-time speed. This decoupling keeps the WebSocket lifetime ≈ "Gemini+TTS production time" regardless of reply length, avoiding Cloud Run / LB idle timeouts during playback. `speak()` is a simpler HTTP POST to `/speak` used only for the wakeup line.

**bear_state.py** — paw-button state machine (RUNNING/PAUSING/PAUSED/UNPAUSING/TERMINATING). GPIO 2 via gpiozero with `hold_time=1.0` and `bounce_time=0.05`, two handlers: short-press (`when_released`, < 1 s) is the **engage** action — wake from PAUSED, or `client.barge_in()` mid-utterance, or no-op if the bear is already listening; long-press (`when_held`, ≥ 1 s) is **sleep**, equivalent to the voice command "Zaby go to sleep". Different beep tones distinguish short vs long press. Volume is forced to 90% on every short press (hardware drifts). Barge-in does NOT change state — the main loop just re-enters `converse()` with no wakeup line, so the user hears beep → silence → ready to talk.

**bear_animatronics.py** — streaming PCM consumer: `start_audio(rate)`, `feed_audio(pcm)`, `end_audio()`. Computes RMS envelope at 200 Hz in `feed_audio`; a dedicated mouth-motor thread converts RMS → pulse durations (0 / 0.15s / 0.25s). Neck motor runs continuously while audio is streaming.

### Server side (`cloud_run/`)

**server.py** — FastAPI app with `/healthz`, `/speak` (text → PCM bytes), `/converse` (WebSocket), `/wakeup` (plain-text wakeup phrase, fetched by the Pi at boot — unauth so the Pi can call it), and `/memory` (HTML + form POSTs, HTTP basic auth). `/converse` runs STT in a thread while draining incoming audio frames, then streams Gemini tokens through a `SentenceBuffer` so each completed sentence hits TTS before the rest of the reply is generated. First sentence's PCM usually reaches the Pi while later sentences are still synthesizing. A single global `AIAgent` is held at module level — no per-client_id keying.

**ai_agent.py** — Gemini 3 Flash (`gemini-3-flash-preview`) with explicit history management. `interact_stream()` yields text chunks and drives the manual function-call loop: stream → collect any `function_call` parts → execute → append `function_response` → stream again. Automatic function calling is disabled (`AutomaticFunctionCallingConfig(disable=True)`) because automatic FC + streaming hangs (googleapis/python-genai#331). The system prompt + memory text are rebuilt fresh into `_build_config()` on every turn so live edits to `INSTRUCTIONS.md` and `MEMORY.md` take effect immediately. Tools: `reset_conversation`, `get_the_time` (uses `self.tz` from the WS `?tz=` param), `get_battery_voltage`, `go_to_sleep`, `power_down`, `save_memory`, `search_memory`. `GEMINI_API_KEY` is read from the environment.

**memory.py** — long-term memory. `MEMORY.md` on the GCS volume is the source of truth (human-readable, editable from the web UI). Mirrored into a sqlite-vec `chunks` table (paragraph chunking, no overlap, `text-embedding-004` 768-dim) and an FTS5 `chunks_fts` table; `search()` over-fetches top-N from each side and fuses with reciprocal rank fusion (RRF). `_reindex()` does per-chunk hash skipping — only paragraphs whose sha1 changed get re-embedded. A file-level hash in the `meta` table short-circuits the whole reindex when MEMORY.md is byte-identical to last index.

**speech_recognition.py** — Google Speech streaming recognition. Consumes a `queue.Queue` of PCM bytes fed by the WS handler, returns the final transcript.

**speech_synthesis.py** — Google TTS → raw 16-bit PCM at 16 kHz. `_apply_edge_fades` applies 30ms half-cosine ramps to each sentence so back-to-back sentences concatenate without clicks. Text normalization: strips `*`, rewrites `Zaby` → `Zabby` for pronunciation.

### Wire protocol

WS `/converse?tz=<IANA>&client_id=<uuid>`:
- Pi → server: binary PCM frames (16kHz mono int16) until EOS detected by STT.
- Server → Pi: `{event:"transcript", text}`, `{event:"response", text}`, binary PCM frames, `{event:"done", go_to_sleep, power_down, error}`.

### Pi audio stack

PyAudio → PortAudio (pulse hostapi) → pipewire-pulse → PipeWire → WaveShare USB (ALSA card 2). PortAudio's ALSA backend mis-probes the WaveShare under systemd, which is why we route through PipeWire. Required one-time setup on the Pi:
- `loginctl enable-linger zaby` — keeps `user@1000.service` (and PipeWire) running even when no one is logged in.
- `zaby.service` sets `XDG_RUNTIME_DIR=/run/user/1000` and orders `After=user@1000.service`.
- The WaveShare card profile must include output; confirm with `pactl list short sinks`.

### GPIO / hardware

- GPIO 2: paw button (input, pull-up). Short press = engage (wake / barge-in mid-utterance). Long press (≥ 1 s) = sleep (same as the "Zaby go to sleep" voice command).
- GPIO 26: mouth motor (on/off pulses via SSR — high back-EMF, duration encodes amplitude)
- GPIO 19: neck motor (on while audio is playing)
- WaveShare USB audio on `hw:2,0`; `asound.conf` sets it as `pcm.!default`.

## Important Implementation Notes

### Auth
- **Gemini**: `GEMINI_API_KEY` env var on the Cloud Run service (set by `cloud_run/deploy.sh`).
- **GCP STT/TTS**: Application Default Credentials inside the Cloud Run container (the service's runtime SA needs Speech + TTS roles).

### Personality
The hardcoded prompt `DEFAULT_MODEL_INSTR` lives in `cloud_run/server.py`, but the *live* prompt is whatever is in `INSTRUCTIONS.md` on the GCS volume. Same pattern for the wakeup phrase: `DEFAULT_WAKEUP_MSG` in code, live value in `WAKEUP.txt`, fetched by the Pi at boot via `GET /wakeup`. The web UI (`/memory`) lets you edit both; **Reset** overwrites the file with the hardcoded default. The next turn (or boot, for wakeup) picks up edits — no redeploy needed.

### Long-term memory
- `MEMORY.md` on the GCS volume is the source of truth — human-readable bullets the bear has saved, plus anything you add by hand via the web UI.
- It is mirrored into a sqlite-vec table + FTS5 table at `memory.db`, fused with RRF for hybrid semantic+keyword recall.
- The bear's two memory tools: `save_memory(fact)` appends a bullet and re-indexes; `search_memory(query)` runs the hybrid search.
- Eager-load + RAG: `MEMORY.md` is *also* injected into the system prompt every turn (small enough that this is cheap).
- Web UI at `/memory` — HTTP basic auth, password from `MEMORY_UI_PASSWORD` env var. Three textareas (system prompt, wakeup message, memory), each with Save and Reset buttons.
- **Deploy pre-req**: `gsutil mb -p zaby-453603 -l us-central1 gs://zaby-memory` once before the first deploy. The bucket is mounted at `/mnt/memory` via `--add-volume type=cloud-storage` in `deploy.sh`.

### Suspend/resume contract
Anything that drives audio or motors should expose `suspend()` and `resume()` and check `self.suspended` in its loops so paw-button presses can interrupt mid-utterance.

### History lifecycle
A single global `AIAgent` lives in server-side memory; in-context conversation history persists until any of: the user says "Zaby start over" (which calls `reset_conversation`), the Pi reboots (a fresh `client_id` triggers `AIAgent.note_client()` to wipe history), or the Cloud Run instance recycles. Durable facts should be written to long-term memory via `save_memory` instead of relying on conversation history.
