import os
import signal
import threading
import uuid

import pygame

from bear_animatronics import BearAnimatronics
from bear_state import BearOnOffState
from conversation_client import ConversationClient


""" Zaby is an AI-powered teddy bear envisioned by a 8 yr old called Zach. The
heavy lifting (speech recognition, Gemini agent, speech synthesis) runs on a
Cloud Run service; the Raspberry Pi streams microphone audio over a WebSocket
and plays back the synthesized MP3 response while driving the bear animatronics.
Bear's paw button will pause/resume.

Set ZABY_SERVER_URL to the Cloud Run service URL (e.g. https://zaby-server-xxx.run.app).
See cloud_run/ for the server and deploy script.

Dave Burke, 2026
"""
def main_loop():
    pygame.mixer.init()
    os.system("amixer -c 2 set Speaker Playback Volume 90%")  # WaveShare USB sound card

    server_url = os.environ.get("ZABY_SERVER_URL")
    if not server_url:
        raise RuntimeError("ZABY_SERVER_URL environment variable is not set")

    wakeup_msg = "Hi! I'm Zaby, how are you today?"

    # Fresh client_id every boot → server drops prior conversation history;
    # paw-button pause/resume keeps the same ID so history survives.
    client_id = uuid.uuid4().hex

    bear_animatronics = BearAnimatronics()
    client = ConversationClient(server_url, bear_animatronics, client_id=client_id)
    bear_state = BearOnOffState(client, wakeup_msg)

    bear_state.beep()  # power on beep

    shutdown_requested = False
    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        nonlocal bear_state
        if shutdown_requested:
            print("\nForce exit")
            os._exit(1)
        print("\nShutdown signal received...")
        shutdown_requested = True
        # Run on a worker thread — bear_state.stop() ends up calling
        # ws.close(), which deadlocks if invoked on the same thread that's
        # inside ws.recv_data(). The paw-button path works because gpiozero
        # already runs its callback on a separate thread; this gives the
        # SIGTERM path the same property.
        threading.Thread(target=bear_state.stop, daemon=True).start()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        go_to_sleep = False
        while not shutdown_requested:
            if (bear_state.handle_state_machine(go_to_sleep)):  # pause/resume here if paw pressed
                go_to_sleep, _ = client.converse()
    finally:
        client.stop_and_cleanup()
        pygame.mixer.quit()

if __name__ == "__main__":
    main_loop()
