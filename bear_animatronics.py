import threading
import time
import numpy as np
from gpiozero import OutputDevice

class BearAnimatronics:
    NECK_SILENCE_RMS = 0.03
    NECK_SILENCE_HOLDOFF = 40  # windows at 200 Hz ≈ 200 ms
    MOUTH_CLOSED_RMS = 0.1     # below this: no mouth pulse
    MID_MOUTH_RMS = 0.15       # below this: short pulse; above: long pulse

    def __init__(self):
        self.mouth_motor = OutputDevice(26)
        self.neck_motor = OutputDevice(19)

        self.envelope_refresh_rate = 200  # Hz
        self.suspended = False
        self._shutdown = False
        self.mouth_pulse_event = threading.Event()
        self._pulse_mouth_value = 0.0
        self._pcm_buffer = b""
        self._bytes_per_update = 0
        self._neck_silence_windows = 0

        self._mouth_thread = threading.Thread(target=self._mouth_thread_loop, daemon=True)
        self._mouth_thread.start()

    def start_audio(self, sample_rate):
        """Begin streaming an utterance. Follow with feed_audio() chunks, then end_audio()."""
        samples_per_update = int(sample_rate / self.envelope_refresh_rate)
        self._bytes_per_update = samples_per_update * 2  # 16-bit signed PCM
        self._pcm_buffer = b""
        self._neck_silence_windows = 0
        self.neck_motor.on()

    def feed_audio(self, pcm_chunk):
        """Process a chunk of mono 16-bit signed PCM: extract envelope, trigger mouth."""
        if self.suspended or self._bytes_per_update == 0:
            return
        self._pcm_buffer += pcm_chunk
        while len(self._pcm_buffer) >= self._bytes_per_update:
            window = self._pcm_buffer[:self._bytes_per_update]
            self._pcm_buffer = self._pcm_buffer[self._bytes_per_update:]
            samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples ** 2)))

            if rms < self.NECK_SILENCE_RMS:
                self._neck_silence_windows += 1
                if self._neck_silence_windows == self.NECK_SILENCE_HOLDOFF:
                    self.neck_motor.off()
            else:
                if self._neck_silence_windows >= self.NECK_SILENCE_HOLDOFF:
                    self.neck_motor.on()
                self._neck_silence_windows = 0

            self._pulse_mouth_value = rms
            self.mouth_pulse_event.set()
            # Trailing \r parks the cursor at col 0 after each update, so any
            # log line that prints mid-playback overwrites the indicator
            # cleanly instead of being prefixed by it.
            if rms < self.MOUTH_CLOSED_RMS:
                print("---\r", end="", flush=True)
            elif rms < self.MID_MOUTH_RMS:
                print("-o-\r", end="", flush=True)
            else:
                print("-O-\r", end="", flush=True)

    def end_audio(self):
        """Finish a streaming utterance."""
        self.neck_motor.off()
        # Wipe the last mouth indicator so it doesn't linger on the log.
        print("\r\033[K", end="", flush=True)

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False

    def stop_and_cleanup(self):
        self.suspend()
        self._shutdown = True
        self.mouth_pulse_event.set()
        self.neck_motor.off()

    def _mouth_thread_loop(self):
        while not self._shutdown:
            self.mouth_pulse_event.wait()
            self.mouth_pulse_event.clear()
            if self._shutdown:
                break
            if self.suspended:
                continue

            value = self._pulse_mouth_value
            if value < self.MOUTH_CLOSED_RMS:
                wait_time = 0.0
            elif value < self.MID_MOUTH_RMS:
                wait_time = 0.08
            else:
                wait_time = 0.25

            if value > 0.0:
                self.mouth_motor.on()
                time.sleep(wait_time)
                self.mouth_motor.off()
                time.sleep(wait_time)
