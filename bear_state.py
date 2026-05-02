import threading
import time
import numpy as np
import pygame
import os
from gpiozero import Button

class BearOnOffState:
    """Paw-button state machine for the bear. Two paw gestures, both routed
    through gpiozero's built-in hold-time / bounce-time:
      - short press (release < 1s)  → engage:
          PAUSED → wake (resume + wakeup line)
          RUNNING + bear talking → barge-in (interrupt mid-utterance)
          RUNNING + bear listening → no-op (already engaged)
      - long press (≥ 1s held)      → sleep (same as 'Zaby go to sleep')
    """
    def __init__(self, client, wakeup_msg):
        self.client = client
        self.wakeup_msg = wakeup_msg

        self.RUNNING = 0
        self.PAUSING = 1
        self.PAUSED = 2
        self.UNPAUSING = 3
        self.TERMINATING = 4
        self.state = self.PAUSED

        self.lock = threading.Lock()
        self.pause_event = threading.Condition(self.lock)

        # gpiozero's hold_time fires when_held automatically after 1s held.
        # bounce_time covers electrical bounce — replaces the manual stopwatch.
        self.button = Button(2, hold_time=1.0, bounce_time=0.05)
        self.button.when_held = self._paw_held
        self.button.when_released = self._paw_released
        self._was_held = False  # set by when_held; consumed by when_released

    def _paw_held(self):
        """Long-press (≥ 1s) — bear says 'Going to sleep' then transitions
        to PAUSED. Same end state as the voice command 'Zaby go to sleep'.
        Runs on a thread because the spoken announcement is ~1s and we
        don't want to block the GPIO callback."""
        self._was_held = True
        self.beep()  # immediate "long-press registered" feedback before the announcement
        threading.Thread(
            target=lambda: self._goto_sleep(announce=True), daemon=True,
        ).start()

    def _paw_released(self):
        if self._was_held:
            self._was_held = False  # consume the long-press flag
            return
        # Hack - force volume level (sometimes lowers by itself!)
        os.system("amixer -c 2 set Speaker Playback Volume 90%")  # WaveShare USB sound card
        with self.lock:  # runs on gpio thread
            if self.state == self.PAUSED:
                self.beep()
                self.state = self.UNPAUSING
                self.client.resume()
                self.pause_event.notify()
            elif self.state == self.RUNNING and self.client.is_speaking():
                # Mid-utterance interrupt — abort speaker + close WS but stay
                # in RUNNING so handle_state_machine re-enters converse()
                # without playing the wakeup line. No beep — the abrupt
                # silence is the audible signal that the press registered.
                self.client.barge_in()
            # else: RUNNING + listening → already engaged; no-op, no beep.

    def _goto_sleep(self, announce):
        with self.lock:
            if self.state != self.RUNNING:
                return  # already paused or transitioning
            self.state = self.PAUSING
            # Refresh wakeup_msg in the background so any edit made via the
            # web UI takes effect on the NEXT wake — without adding network
            # latency to the wake transition itself.
            threading.Thread(target=self._refresh_wakeup_msg, daemon=True).start()
        if announce:
            # Stop any in-flight bear utterance so the announcement isn't
            # talked over. barge_in (not suspend) — suspend would block speak().
            self.client.barge_in()
            # Small gap so the beep and the announcement don't run together.
            time.sleep(0.2)
            self.client.speak("Going to sleep")
        # Officially suspend now — closes WS (no-op if barged), suspends
        # animatronics, sets self.suspended.
        self.client.suspend()

    def _refresh_wakeup_msg(self):
        new_msg = self.client.get_wakeup_msg()
        if new_msg and new_msg != self.wakeup_msg:
            print(f"wakeup_msg refreshed: {new_msg!r}")
            self.wakeup_msg = new_msg

    def handle_state_machine(self, go_to_sleep):
        if go_to_sleep:
            # Voice-triggered: bear already said its goodbye in the response,
            # so don't announce again. Synchronous (no thread) — no audio to wait on.
            self._goto_sleep(announce=False)

        speak_wakeup = False
        with self.lock:  # runs on main thread
            if self.state == self.PAUSING:
                self.state = self.PAUSED
            elif self.state == self.PAUSED:
                print("Bear paused")
                self.pause_event.wait()
            elif self.state == self.UNPAUSING:
                self.state = self.RUNNING
                speak_wakeup = True
                print("Bear running")

        if speak_wakeup:  # outside of lock so it can be interrupted
            self.client.speak(self.wakeup_msg)

        return self.state == self.RUNNING
        
    def beep(self, frequency=1000, duration=0.1, volume=0.5):
        sample_rate = 44100
        samples = int(duration * sample_rate)
        t = np.linspace(0, duration, samples, False)
        sine_wave = np.sin(2 * np.pi * frequency * t)
        sine_wave = (sine_wave * 32767 * volume).astype(np.int16)
        stereo = np.column_stack((sine_wave, sine_wave))
        sound = pygame.sndarray.make_sound(stereo)
        sound.play()

    def stop(self):
        with self.lock:
            self.state = self.TERMINATING
            self.client.suspend()
            self.pause_event.notify()
