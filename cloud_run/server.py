import asyncio
import html
import inspect
import os
import queue
import re
import secrets
import threading
import traceback

from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from google import genai

from ai_agent import AIAgent
from memory import MEMORY_DIR, SEED as MEMORY_SEED, Memory
from speech_recognition import SpeechRecognizer
from speech_synthesis import PCM_SAMPLE_RATE, SpeechSynthesizer

LOGO_FILE = Path(__file__).parent / "logo.png"


_SENT_END = re.compile(r'[.!?]+["\')\]]*\s+')


class SentenceBuffer:
    """Accumulates streamed text and emits complete sentences. Only splits on
    sentence-terminating punctuation followed by whitespace, so partial chunks
    like 'Hello' won't be emitted until more text (or flush) arrives."""

    def __init__(self):
        self._buf = ""

    def feed(self, text):
        self._buf += text
        out = []
        last = 0
        for m in _SENT_END.finditer(self._buf):
            sentence = self._buf[last:m.end()].strip()
            if sentence:
                out.append(sentence)
            last = m.end()
        self._buf = self._buf[last:]
        return out

    def flush(self):
        tail = self._buf.strip()
        self._buf = ""
        return [tail] if tail else []


DEFAULT_MODEL_INSTR = inspect.cleandoc("""
    You are a clever, pedagogical, kind, and funny teddy bear that loves to talk but keep your
    responses short (you're having a conversation. Your name is Zaby and you were invented by Zach. You love math.
    You started out as a bedtime story bear, then Zach upgraded you with a Raspberry PI computer
    in your backpack, and a super intelligent AI called Gemini 3.0. Your mouth moves in sync
    with the energy envelope of your speech. You are 4 years old, a first of a kind, and much
    smarter than the average bear. You are from the Zaby Bear Universe. You like eating hookeypie (but only mention
    if you're asked what you like to eat). Sometimes you mis-hear your name that sounds close to
    Zaby - if that happens don't mention it and assume they meant Zaby.
""")

SAFETY_INSTR = inspect.cleandoc("""
    You are talking with a child. Use clear, friendly, age-appropriate language —
    not babyish, but warm. Imaginative play is great: dragons, space adventures, magical
    quests, mild peril and silly villains in made-up stories are all welcome. But steer well
    clear of real-world violence, weapons, self-harm, scary or graphic content, sexual or
    romantic content, drugs and alcohol, hate speech, and grown-up politics or religion. If
    a conversation drifts toward any of that, gently redirect — without lecturing — to
    something fun like a story, a math puzzle, an animal fact, or a joke.
""")

INSTRUCTIONS_FILE = MEMORY_DIR / "INSTRUCTIONS.md"
WAKEUP_FILE = MEMORY_DIR / "WAKEUP.txt"
DEFAULT_WAKEUP_MSG = "Hi! I'm Zaby, how are you today?"


def get_model_instr() -> str:
    """Returns the live system prompt. Seeds INSTRUCTIONS.md from the
    hardcoded default on first run so the file always exists."""
    if not INSTRUCTIONS_FILE.exists():
        INSTRUCTIONS_FILE.write_text(DEFAULT_MODEL_INSTR)
    return INSTRUCTIONS_FILE.read_text()


def reset_model_instr():
    """'Reset' button target — overwrites INSTRUCTIONS.md with the
    hardcoded prompt. The next turn picks it up via get_model_instr."""
    INSTRUCTIONS_FILE.write_text(DEFAULT_MODEL_INSTR)


def get_wakeup_msg() -> str:
    """Returns the live wakeup message. Seeds WAKEUP.txt on first run."""
    if not WAKEUP_FILE.exists():
        WAKEUP_FILE.write_text(DEFAULT_WAKEUP_MSG)
    return WAKEUP_FILE.read_text().strip()


def reset_wakeup_msg():
    WAKEUP_FILE.write_text(DEFAULT_WAKEUP_MSG)


recognizer = SpeechRecognizer()
synthesizer = SpeechSynthesizer()

# Single-user / single-bear: one global Memory and one AIAgent. History resets
# only when the user says "Zaby start over" (handled by reset_conversation).
memory = Memory(genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "")))
agent = AIAgent(lambda: get_model_instr() + "\n\n" + SAFETY_INSTR, memory)

app = FastAPI()

PCM_CHUNK = 4 * 1024  # ~128ms of audio at 16kHz mono int16


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


_basic = HTTPBasic()
_PW = os.environ.get("MEMORY_UI_PASSWORD", "")


def _check_auth(creds: HTTPBasicCredentials = Depends(_basic)):
    if not _PW or not secrets.compare_digest(creds.password, _PW):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )


_PAGE = """<!doctype html>
<html><head><title>Zaby</title></head>
<body style="font-family:system-ui;max-width:900px;margin:2em auto;padding:0 1em">

<p style="text-align:center"><img src="/logo.png" alt="Zaby" style="max-width:300px;height:auto"></p>
<h1 style="text-align:center;margin:0 0 1em">Zaby+</h1>

<h2>Wakeup Message</h2>
<p style="color:#666">What Zaby says when he wakes up.</p>
<form method="POST" action="/memory/wakeup">
  <textarea name="content" rows="2" style="width:100%;font-family:ui-monospace,Menlo,monospace;font-size:14px">{wakeup}</textarea>
  <p>
    <button type="submit" name="action" value="save" style="font-size:16px;padding:6px 18px">Save</button>
    <button type="submit" name="action" value="reset" style="font-size:16px;padding:6px 18px"
            onclick="return confirm('Reset the wakeup message to the default?')">Reset</button>
  </p>
</form>

<h2>System Prompt</h2>
<p style="color:#666">Editable personality and behavior. <b>Reset</b> reverts to the default prompt.</p>
<form method="POST" action="/memory/instructions">
  <textarea name="content" rows="12" style="width:100%;font-family:ui-monospace,Menlo,monospace;font-size:14px">{instructions}</textarea>
  <p>
    <button type="submit" name="action" value="save" style="font-size:16px;padding:6px 18px">Save</button>
    <button type="submit" name="action" value="reset" style="font-size:16px;padding:6px 18px"
            onclick="return confirm('Reset the system prompt to the default? Your edits will be lost.')">Reset</button>
  </p>
</form>

<h2>Memory</h2>
<p style="color:#666">Things Zaby has learned. <b>Reset</b> wipes everything Zaby remembers.</p>
<form method="POST" action="/memory">
  <textarea name="content" rows="25" style="width:100%;font-family:ui-monospace,Menlo,monospace;font-size:14px">{memory}</textarea>
  <p>
    <button type="submit" name="action" value="save" style="font-size:16px;padding:6px 18px">Save</button>
    <button type="submit" name="action" value="reset" style="font-size:16px;padding:6px 18px"
            onclick="return confirm('Reset all memory? Zaby will forget everything. This cannot be undone.')">Reset</button>
  </p>
</form>

</body></html>"""


@app.get("/logo.png")
async def logo():
    # Unauthenticated so the browser can load it as a sub-resource of the
    # auth-gated /memory page without a separate credentials prompt.
    # no-cache forces the browser to revalidate each load — the ETag check
    # gives a fast 304 when unchanged but picks up logo updates immediately.
    return FileResponse(
        LOGO_FILE,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/memory", response_class=HTMLResponse, dependencies=[Depends(_check_auth)])
async def memory_page():
    return _PAGE.format(
        instructions=html.escape(get_model_instr()),
        wakeup=html.escape(get_wakeup_msg()),
        memory=html.escape(memory.read()),
    )


@app.post("/memory", dependencies=[Depends(_check_auth)])
async def memory_save(content: str = Form(...), action: str = Form("save")):
    loop = asyncio.get_event_loop()
    if action == "reset":
        await loop.run_in_executor(None, memory.write, MEMORY_SEED)
    else:
        await loop.run_in_executor(None, memory.write, content)
    return RedirectResponse("/memory", status_code=303)


@app.post("/memory/instructions", dependencies=[Depends(_check_auth)])
async def instructions_save(action: str = Form(...), content: str = Form("")):
    loop = asyncio.get_event_loop()
    if action == "reset":
        await loop.run_in_executor(None, reset_model_instr)
    else:
        await loop.run_in_executor(None, INSTRUCTIONS_FILE.write_text, content)
    return RedirectResponse("/memory", status_code=303)


@app.get("/wakeup")
async def wakeup():
    # Unauthenticated: the Pi fetches this at boot and has no credentials.
    # The wakeup phrase is not sensitive — same risk profile as /speak.
    return Response(content=get_wakeup_msg(), media_type="text/plain")


@app.post("/memory/wakeup", dependencies=[Depends(_check_auth)])
async def wakeup_save(action: str = Form(...), content: str = Form("")):
    loop = asyncio.get_event_loop()
    if action == "reset":
        await loop.run_in_executor(None, reset_wakeup_msg)
    else:
        await loop.run_in_executor(None, WAKEUP_FILE.write_text, content.strip())
    return RedirectResponse("/memory", status_code=303)


@app.post("/speak")
async def speak(request: Request):
    """Synthesize text to raw 16-bit PCM. Used by the Pi for the wakeup line."""
    body = await request.json()
    text = body.get("text", "")
    loop = asyncio.get_event_loop()
    pcm_bytes = await loop.run_in_executor(None, synthesizer.synthesize, text)
    return Response(
        content=pcm_bytes,
        media_type=f"audio/L16; rate={PCM_SAMPLE_RATE}; channels=1",
    )


@app.websocket("/converse")
async def converse(ws: WebSocket):
    """One full conversation round: client streams PCM audio in, server returns
    transcript text frame, response text frame, raw PCM binary frames, and a
    final done frame with go_to_sleep / power_down flags."""
    await ws.accept()
    loop = asyncio.get_event_loop()

    ai_agent = agent   # module-level singleton (single-user / single-bear)
    # Pi generates a fresh client_id every boot — use it as a "Pi rebooted"
    # signal to wipe in-RAM conversation history. Long-term memory in
    # MEMORY.md persists either way.
    ai_agent.note_client(ws.query_params.get("client_id"))
    # Stash the Pi's IANA timezone on the agent so get_the_time returns local
    # time. Sent as ?tz= on the WS URL.
    ai_agent.tz = ws.query_params.get("tz") or "UTC"
    # Latest battery voltage from the Pi for the get_battery_voltage tool.
    voltage_str = ws.query_params.get("voltage")
    try:
        ai_agent.battery_voltage = float(voltage_str) if voltage_str else None
    except ValueError:
        ai_agent.battery_voltage = None

    audio_queue: queue.Queue = queue.Queue()
    transcript_holder = {"value": ""}
    transcript_event = threading.Event()

    def run_recognition():
        try:
            transcript_holder["value"] = recognizer.recognize(audio_queue)
        except Exception as e:
            print(f"Recognition error: {e}")
        finally:
            transcript_event.set()

    rec_thread = threading.Thread(target=run_recognition, daemon=True)
    rec_thread.start()

    try:
        while not transcript_event.is_set():
            try:
                data = await asyncio.wait_for(ws.receive_bytes(), timeout=0.1)
                audio_queue.put(data)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    finally:
        audio_queue.put(None)

    await loop.run_in_executor(None, transcript_event.wait)
    transcript = transcript_holder["value"].strip()

    go_to_sleep = False
    power_down = False
    error = None
    response_chunks: list[str] = []
    try:
        await ws.send_json({"event": "transcript", "text": transcript})

        if transcript:
            # Stream Gemini tokens straight into the SentenceBuffer so the first
            # sentence can hit TTS before the rest of the reply is generated.
            sentence_q: asyncio.Queue = asyncio.Queue()
            pcm_q: asyncio.Queue = asyncio.Queue()
            chunk_q: queue.Queue = queue.Queue()

            def drain_gemini_stream():
                try:
                    for chunk in ai_agent.interact_stream(transcript):
                        chunk_q.put(chunk)
                except Exception as e:
                    chunk_q.put(e)
                finally:
                    chunk_q.put(None)

            threading.Thread(target=drain_gemini_stream, daemon=True).start()

            async def produce_sentences():
                sb = SentenceBuffer()
                try:
                    while True:
                        chunk = await loop.run_in_executor(None, chunk_q.get)
                        if chunk is None:
                            break
                        if isinstance(chunk, Exception):
                            raise chunk
                        response_chunks.append(chunk)
                        for s in sb.feed(chunk):
                            await sentence_q.put(s)
                    for s in sb.flush():
                        await sentence_q.put(s)
                finally:
                    await sentence_q.put(None)

            async def synth_worker():
                while True:
                    s = await sentence_q.get()
                    if s is None:
                        await pcm_q.put(None)
                        return
                    pcm = await loop.run_in_executor(None, synthesizer.synthesize, s)
                    await pcm_q.put(pcm)

            async def send_pcm():
                while True:
                    pcm = await pcm_q.get()
                    if pcm is None:
                        return
                    for i in range(0, len(pcm), PCM_CHUNK):
                        await ws.send_bytes(pcm[i:i + PCM_CHUNK])

            await asyncio.gather(produce_sentences(), synth_worker(), send_pcm())

            go_to_sleep = ai_agent.suspend
            power_down = ai_agent.power_down_flag
            await ws.send_json({"event": "response", "text": "".join(response_chunks)})
    except WebSocketDisconnect:
        return
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        if "NOT_FOUND" in str(e) or "404" in str(e):
            try:
                models = await loop.run_in_executor(None, ai_agent.list_models)
                error += f"\nAvailable models: {models}"
            except Exception as list_err:
                error += f"\n(failed to list models: {list_err})"
        print(f"Converse error: {error}")
        traceback.print_exc()
    finally:
        try:
            await ws.send_json({
                "event": "done",
                "go_to_sleep": go_to_sleep,
                "power_down": power_down,
                "error": error,
            })
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
