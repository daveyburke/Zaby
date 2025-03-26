import threading
import time
import numpy as np
import pygame
from gpiozero import Button

class BearOnOffState:
    """Class to handle start/stop of the bear via paw button. A bit gnarly to handle
    the asnychronous nature of button press. Synthesizer/recognizer/animatronics need
    to be able to be suspended/resumed at any time
    """
    def __init__(self, synthesizer, recognizer, wakeup_msg):
        self.synthesizer = synthesizer
        self.recognizer = recognizer
        self.wakeup_msg = wakeup_msg

        self.RUNNING = 0
        self.PAUSING = 1
        self.PAUSED = 2
        self.UNPAUSING = 3
        self.TERMINATING = 4
        self.state = self.PAUSED

        self.lock = threading.Lock()
        self.pause_event = threading.Condition(self.lock)

        self.DEBOUNCE_TIME = 500
        self.last_button_press_time = 0
        self.button = Button(2)
        self.button.when_pressed = self.paw_button_callback

    def paw_button_callback(self):
        current_time = int(time.time() * 1000)
        if (current_time - self.last_button_press_time) < self.DEBOUNCE_TIME:
            return
        self.last_button_press_time = current_time
        
        self.beep()
        with self.lock:  # runs on gpio thread
            if self.state == self.RUNNING:
                self.state = self.PAUSING
                self.synthesizer.suspend()
                self.recognizer.suspend()
            elif self.state == self.PAUSED:
                self.state = self.UNPAUSING
                self.synthesizer.resume()
                self.recognizer.resume()
                self.pause_event.notify()
    
    def handle_state_machine(self, go_to_sleep):
        if (go_to_sleep): self.paw_button_callback()

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
            self.synthesizer.speak(self.wakeup_msg) 

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
            self.synthesizer.suspend()
            self.recognizer.suspend()
            self.pause_event.notify()
