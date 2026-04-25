import json
import os
import queue
import socket
import threading
from urllib.parse import urlencode

import pyaudio
import requests
import websocket  # websocket-client


PCM_SAMPLE_RATE = 16000  # Must match cloud_run/speech_synthesis.py.
PCM_CHUNK = 4 * 1024


def _local_tz_name():
    """IANA timezone name for this machine. Linux reads /etc/timezone; macOS
    dereferences the /etc/localtime symlink. Falls back to UTC."""
    try:
        with open("/etc/timezone") as f:
            tz = f.read().strip()
            if tz:
                return tz
    except OSError:
        pass
    try:
        link = os.readlink("/etc/localtime")
        parts = link.split("/zoneinfo/")
        if len(parts) == 2:
            return parts[1]
    except OSError:
        pass
    return "UTC"


class ConversationClient:
    """Pi-side client. Streams microphone PCM to the Cloud Run server, receives
    raw 16-bit PCM reply chunks, and plays them back through the bear animatronics
    as they arrive."""

    def __init__(self, server_url, bear_animatronics, client_id, rate=16000):
        self.server_url = server_url.rstrip("/")
        self.bear_animatronics = bear_animatronics
        self.client_id = client_id  # identifies this bear's history on the server
        self.rate = rate
        self.chunk = int(rate / 10)  # 100ms chunks

        self.pyaudio_instance = pyaudio.PyAudio()
        self.suspended = False

        self.stream = None
        self.ws = None

    def converse(self):
        """Runs one full round: capture audio → recognize → AI reply → speak.
        Returns (go_to_sleep, power_down)."""
        if self.suspended:
            return False, False

        go_to_sleep = False
        power_down = False
        done_streaming = threading.Event()
        send_thread = None

        # Decouple WS recv from audio playback: recv drains PCM into pcm_q at
        # network speed (lets the WS close in seconds even for multi-minute
        # replies — Cloud Run was timing out the WS otherwise) while a separate
        # thread plays from the queue at real-time speed.
        pcm_q: queue.Queue = queue.Queue()
        playback_thread = threading.Thread(target=self._playback_loop, args=(pcm_q,), daemon=True)
        playback_thread.start()

        try:
            ws_url = self._http_to_ws(self.server_url) + "/converse?" + urlencode({
                "tz": _local_tz_name(),
                "client_id": self.client_id,
            })
            self.ws = websocket.create_connection(ws_url, timeout=30)
            self.ws.settimeout(0.5)  # short recv timeout so we can notice suspend

            self.stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
            )

            def send_audio():
                while not self.suspended and not done_streaming.is_set():
                    try:
                        data = self.stream.read(self.chunk, exception_on_overflow=False)
                        self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                    except Exception:
                        break

            send_thread = threading.Thread(target=send_audio, daemon=True)
            send_thread.start()

            got_done = False
            while not self.suspended:
                try:
                    opcode, data = self.ws.recv_data()
                except (websocket.WebSocketTimeoutException, socket.timeout):
                    continue
                except websocket.WebSocketException as e:
                    print(f"WebSocket closed unexpectedly: {e}")
                    break

                if opcode == websocket.ABNF.OPCODE_TEXT:
                    msg = json.loads(data.decode("utf-8"))
                    event = msg.get("event")
                    if event == "transcript":
                        done_streaming.set()
                        print(f"Transcribed: {msg.get('text', '')}")
                    elif event == "response":
                        print(msg.get("text", ""))
                    elif event == "done":
                        got_done = True
                        if msg.get("error"):
                            print(f"Server error: {msg['error']}")
                        go_to_sleep = msg.get("go_to_sleep", False)
                        power_down = msg.get("power_down", False)
                        break
                elif opcode == websocket.ABNF.OPCODE_BINARY:
                    pcm_q.put(data)
                elif opcode == websocket.ABNF.OPCODE_CLOSE:
                    break

            if not got_done:
                print("Conversation ended without 'done' event")
        except Exception as e:
            print(f"Conversation error: {e}")
        finally:
            done_streaming.set()
            if send_thread:
                send_thread.join(timeout=1.0)
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None
            pcm_q.put(None)
            playback_thread.join()
            print("Conversation round complete")

        if power_down:
            os.system("(sleep 15 && sudo shutdown -h now) &")

        return go_to_sleep, power_down

    def _playback_loop(self, pcm_q):
        """Pulls PCM chunks from pcm_q and plays them in real time. Runs until
        a None sentinel arrives or self.suspended flips True."""
        out_stream = None
        audio_started = False
        try:
            while True:
                try:
                    data = pcm_q.get(timeout=0.5)
                except queue.Empty:
                    if self.suspended:
                        break
                    continue
                if data is None or self.suspended:
                    break
                if not audio_started:
                    out_stream = self._open_output_stream()
                    self.bear_animatronics.start_audio(PCM_SAMPLE_RATE)
                    audio_started = True
                # Write first, THEN feed the envelope. write() blocks until
                # PortAudio's buffer accepts the data, so by the time we drive
                # the mouth, the chunk is queued ~ where it will play. Calling
                # feed_audio first leads the audio by one chunk (~128 ms).
                out_stream.write(data)
                self.bear_animatronics.feed_audio(data)
        finally:
            if audio_started:
                self.bear_animatronics.end_audio()
            if out_stream:
                out_stream.stop_stream()
                out_stream.close()

    def speak(self, text):
        """Synthesize and play a single utterance (used for the wakeup line)."""
        if self.suspended:
            return

        try:
            with requests.post(
                f"{self.server_url}/speak",
                json={"text": text},
                timeout=30,
                stream=True,
            ) as r:
                r.raise_for_status()
                print(text)
                self._stream_pcm(r.iter_content(chunk_size=PCM_CHUNK))
        except Exception as e:
            print(f"Error occurred: {e}")

    def suspend(self):
        self.suspended = True
        self.bear_animatronics.suspend()
        # Don't close self.ws here. suspend() is reached from the SIGTERM
        # handler on the main thread, which may itself be inside
        # self.ws.recv_data() — closing the socket from the same thread
        # already in recv deadlocks websocket-client. The recv loop's 0.5s
        # timeout picks up self.suspended and exits; converse()'s finally
        # block then closes the socket cleanly.

    def resume(self):
        self.suspended = False
        self.bear_animatronics.resume()

    def stop_and_cleanup(self):
        self.suspend()
        self.bear_animatronics.stop_and_cleanup()
        self.pyaudio_instance.terminate()

    def _open_output_stream(self):
        return self.pyaudio_instance.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=PCM_SAMPLE_RATE,
            output=True,
        )

    def _stream_pcm(self, chunks):
        out_stream = self._open_output_stream()
        self.bear_animatronics.start_audio(PCM_SAMPLE_RATE)
        try:
            for chunk in chunks:
                if self.suspended or not chunk:
                    break
                out_stream.write(chunk)
                self.bear_animatronics.feed_audio(chunk)
        finally:
            self.bear_animatronics.end_audio()
            out_stream.stop_stream()
            out_stream.close()

    @staticmethod
    def _http_to_ws(url):
        if url.startswith("https://"):
            return "wss://" + url[len("https://"):]
        if url.startswith("http://"):
            return "ws://" + url[len("http://"):]
        return url
