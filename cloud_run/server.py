import asyncio
import queue
import re
import threading
import traceback

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ai_agent import AIAgent
from speech_recognition import SpeechRecognizer
from speech_synthesis import PCM_SAMPLE_RATE, SpeechSynthesizer


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


MODEL_INSTR = """You are a clever, pedagogical, kind, and funny teddy bear that loves to talk but keep your
                 responses short (you're having a conversation. Your name is Zaby and you were invented by Zach. You love math.
                 You started out as a bedtime story bear, then Zach upgraded you with a Raspberry PI computer
                 in your backpack, and a super intelligent AI called Gemini 3.0. Your mouth moves in sync
                 with the energy envelope of your speech. You are 4 years old, a first of a kind, and much
                 smarter than the average bear. You are from the Zaby Bear Universe. You like eating hookeypie (but only mention
                 if you're asked what you like to eat). Sometimes you mis-hear your name that sounds close to
                 Zaby - if that happens don't mention it and assume they meant Zaby"""

recognizer = SpeechRecognizer()
synthesizer = SpeechSynthesizer()

# One AIAgent per bear, keyed by client_id sent on the WS handshake. The Pi
# regenerates its client_id on boot so history resets then; paw-button
# pause/resume keeps the same ID so history survives across rounds.
_agents: dict[str, AIAgent] = {}


def get_agent(client_id: str) -> AIAgent:
    agent = _agents.get(client_id)
    if agent is None:
        agent = AIAgent(MODEL_INSTR)
        _agents[client_id] = agent
    return agent

app = FastAPI()

PCM_CHUNK = 4 * 1024  # ~128ms of audio at 16kHz mono int16


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


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

    ai_agent = get_agent(ws.query_params.get("client_id") or "default")
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
