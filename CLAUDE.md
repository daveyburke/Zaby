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

**main.py** — entry point. Generates a fresh `client_id` (UUID) each boot so server-side conversation history resets on reboot but survives paw-button pauses. Wires up the animatronics, `ConversationClient`, and `BearOnOffState`, then runs the main loop.

**conversation_client.py** — WebSocket client for the server. `converse()` opens `/converse`, streams mic PCM up on a send thread, and receives four kinds of frames back: `transcript` (text), `response` (text), PCM binary frames, and `done` (with `go_to_sleep` / `power_down` flags). PCM bytes are drained into `pcm_q` at network speed by the recv loop; a separate `_playback_loop` thread pulls from the queue and writes to PyAudio at real-time speed. This decoupling keeps the WebSocket lifetime ≈ "Gemini+TTS production time" regardless of reply length, avoiding Cloud Run / LB idle timeouts during playback. `speak()` is a simpler HTTP POST to `/speak` used only for the wakeup line.

**bear_state.py** — paw-button state machine (RUNNING/PAUSING/PAUSED/UNPAUSING/TERMINATING). GPIO 2 with 500ms debounce; pygame beep on transitions; forces the WaveShare volume to 90% on every press (hardware drifts).

**bear_animatronics.py** — streaming PCM consumer: `start_audio(rate)`, `feed_audio(pcm)`, `end_audio()`. Computes RMS envelope at 200 Hz in `feed_audio`; a dedicated mouth-motor thread converts RMS → pulse durations (0 / 0.15s / 0.25s). Neck motor runs continuously while audio is streaming.

### Server side (`cloud_run/`)

**server.py** — FastAPI app with `/healthz`, `/speak` (text → PCM bytes), and `/converse` (WebSocket). `/converse` runs STT in a thread while draining incoming audio frames, then streams Gemini tokens through a `SentenceBuffer` so each completed sentence hits TTS before the rest of the reply is generated. First sentence's PCM usually reaches the Pi while later sentences are still synthesizing. `_agents: dict[str, AIAgent]` keys one agent per bear by the `client_id` query param.

**ai_agent.py** — Gemini 3 Flash (`gemini-3-flash-preview`) with explicit history management. `interact_stream()` yields text chunks and drives the manual function-call loop: stream → collect any `function_call` parts → execute → append `function_response` → stream again. Automatic function calling is disabled (`AutomaticFunctionCallingConfig(disable=True)`) because automatic FC + streaming hangs (googleapis/python-genai#331). Tools: `reset_conversation`, `get_the_time` (uses `self.tz` from the WS `?tz=` param), `go_to_sleep`, `power_down`. `GEMINI_API_KEY` is read from the environment.

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

- GPIO 2: paw button (input, pull-up)
- GPIO 26: mouth motor (on/off pulses via SSR — high back-EMF, duration encodes amplitude)
- GPIO 19: neck motor (on while audio is playing)
- WaveShare USB audio on `hw:2,0`; `asound.conf` sets it as `pcm.!default`.

## Important Implementation Notes

### Auth
- **Gemini**: `GEMINI_API_KEY` env var on the Cloud Run service (set by `cloud_run/deploy.sh`).
- **GCP STT/TTS**: Application Default Credentials inside the Cloud Run container (the service's runtime SA needs Speech + TTS roles).

### Personality
System prompt lives in `cloud_run/server.py` as `MODEL_INSTR`. Editing it requires redeploying the server.

### Suspend/resume contract
Anything that drives audio or motors should expose `suspend()` and `resume()` and check `self.suspended` in its loops so paw-button presses can interrupt mid-utterance.

### History lifecycle
Per-`client_id` `AIAgent` instances live in server-side memory. A Pi reboot → new UUID → fresh history. Paw-button pause/resume → same UUID → history preserved. No cross-container persistence — a Cloud Run instance recycle also drops history.
