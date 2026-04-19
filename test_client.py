"""Mac-side smoke test for conversation_client.py.

Usage:
    export ZABY_SERVER_URL=https://zaby-server-xxx.run.app
    python test_client.py [speak|converse|both]

Stubs out the bear (gpiozero) so the client can run on macOS. Audio plays
through the default output device; mic input uses the default input device.
"""
import os
import sys
import pygame

from conversation_client import ConversationClient


class StubBear:
    """No-op stand-in for BearAnimatronics. On macOS we have no GPIO motors."""
    def start_audio(self, sample_rate):
        pass

    def feed_audio(self, pcm_chunk):
        pass

    def end_audio(self):
        pass

    def suspend(self):
        pass

    def resume(self):
        pass

    def stop_and_cleanup(self):
        pass


def main():
    server_url = os.environ.get(
        "ZABY_SERVER_URL",
        "https://zaby-server-897054000877.us-central1.run.app",
    )

    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    pygame.mixer.init()
    client = ConversationClient(server_url, StubBear())

    try:
        if mode in ("speak", "both"):
            print("\n=== /speak ===")
            client.speak("Hi! I'm Zaby, how are you today?")

        if mode in ("converse", "both"):
            print("\n=== /converse (talk into the mic) ===")
            go_to_sleep, power_down = client.converse()
            print(f"go_to_sleep={go_to_sleep} power_down={power_down}")
    finally:
        client.stop_and_cleanup()
        pygame.mixer.quit()


if __name__ == "__main__":
    main()
