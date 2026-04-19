import numpy as np

from google.cloud import texttospeech

PCM_SAMPLE_RATE = 16000  # Must match the Pi-side conversation_client.py constant.
_FADE_MS = 30

class SpeechSynthesizer:
    def __init__(self, language_code="en-US", voice_name="en-US-Neural2-I",
                 speaking_rate=1.0, pitch=7.0):
        self.client = texttospeech.TextToSpeechClient()
        self.sample_rate = PCM_SAMPLE_RATE

        self.voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
        )

        self.audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=PCM_SAMPLE_RATE,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )

    def synthesize(self, text):
        """Synthesize text to raw 16-bit signed PCM bytes (mono, PCM_SAMPLE_RATE Hz).
        Fades in/out at the edges so sentences can be concatenated without clicks."""
        text = self._normalize_text(text)
        synthesis_input = texttospeech.SynthesisInput(text=text)
        response = self.client.synthesize_speech(
            input=synthesis_input, voice=self.voice, audio_config=self.audio_config
        )
        return _apply_edge_fades(response.audio_content)

    def _normalize_text(self, text):
        text = text.replace("*", "")
        text = text.replace("Zaby", "Zabby")  # correct pronounciation!
        return text


def _apply_edge_fades(pcm_bytes):
    if not pcm_bytes:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    n = min(PCM_SAMPLE_RATE * _FADE_MS // 1000, len(samples) // 2)
    if n <= 0:
        return pcm_bytes
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32))
    samples[:n] *= ramp
    samples[-n:] *= ramp[::-1]
    return samples.astype(np.int16).tobytes()
