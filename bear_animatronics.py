import threading
import time
import numpy as np
from pydub import AudioSegment
from gpiozero import OutputDevice
import pygame

class BearAnimatronics:
    def __init__(self):
        self.mouth_motor = OutputDevice(26)
        self.neck_motor = OutputDevice(19)

        self.envelope_refresh_rate = 200  # Hz
        self.chunk_size = 1024  # Audio chunk size for analysis
        self.suspended = False
        self.tracking_thread = None
        self.mouth_thread = None
        self.mouth_pulse_event = threading.Event()
        self._pulse_mouth_value = 0.0
        
    def animate(self, audio_file_path):
        # Load audio to calculate duration
        audio = AudioSegment.from_mp3(audio_file_path)
        duration = len(audio) / 1000.0  # Duration in seconds
        
        # Start tracking thread for mouth movements
        self.tracking_thread = threading.Thread(
            target=self._track_and_animate, 
            args=(audio_file_path, duration)
        )
        self.tracking_thread.start()

        # Mouth thread runs the mouth motor
        self.mouth_thread = threading.Thread(
            target=self._mouth_thread
        )
        self.mouth_thread.start()

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False

    def wait_for_completion(self):
        if self.tracking_thread:
            self.tracking_thread.join()
            self.tracking_thread = None
        
    def stop_and_cleanup(self):
        self.suspend()
        self.mouth_pulse_event.set()
        self.wait_for_completion()
    
    def _analyze_audio_envelope(self, audio_file_path):
        try:
            # Load MP3 file
            audio = AudioSegment.from_mp3(audio_file_path)
            
            # Convert to raw audio data
            samples = np.array(audio.get_array_of_samples())
            
            # Normalize samples
            if audio.channels == 2:
                # Convert stereo to mono by averaging channels
                samples = np.mean(samples.reshape(-1, 2), axis=1)
            samples = samples / np.max(np.abs(samples))
            
            # Calculate time step between envelope updates (in samples)
            sample_rate = audio.frame_rate
            samples_per_update = int(sample_rate / self.envelope_refresh_rate)
            
            # Extract envelope (amplitude) at regular intervals
            envelopes = []
            for i in range(0, len(samples), samples_per_update):
                chunk = samples[i:i+samples_per_update]
                if len(chunk) > 0:
                    # Use RMS (root mean square) as amplitude measure
                    rms = np.sqrt(np.mean(chunk**2))
                    envelopes.append(rms)
            
            return envelopes, sample_rate
        except Exception as e:
            print(f"Error analyzing audio envelope: {e}")
            return [], 44100  # Return empty envelope with default sample rate

    def _track_and_animate(self, audio_file_path, duration):
        envelopes, sample_rate = self._analyze_audio_envelope(audio_file_path)
        time_per_update = 1.0 / self.envelope_refresh_rate
        
        self.neck_motor.on()

        start_time = time.time()
        for i, env_value in enumerate(envelopes):
            if self.suspended:
                break
                
            # Calculate current position in playback
            elapsed = time.time() - start_time
            target_time = i * time_per_update
            
            # If we're ahead of the audio, wait
            if elapsed < target_time:
                time.sleep(target_time - elapsed)
            
            # Pulse mouth according to envelope value    
            self._pulse_mouth_value = env_value
            self.mouth_pulse_event.set()     
            if env_value < 0.1:
                print(f"\r---", end="")
            elif env_value < 0.3:
                print(f"\r-o-", end="")
            else:
                print(f"\r-O-", end="")

        self.neck_motor.off()
        print("")

    def _mouth_thread(self):
        while not self.suspended:
            self.mouth_pulse_event.wait()
            self.mouth_pulse_event.clear()

            if not self.suspended:
                # Convert amplitude to duration of motor application
                #print(f"Mouth on {self._pulse_mouth_value}")
                wait_time = 0.0
                if self._pulse_mouth_value < 0.1:
                    wait_time = 0.0
                elif self._pulse_mouth_value < 0.3:
                    wait_time = 0.1
                else:
                    wait_time = 0.2

                #print(f"Mouth on {wait_time}")
                if self._pulse_mouth_value > 0.0:
                    self.mouth_motor.on()
                    time.sleep(wait_time)
                    self.mouth_motor.off()
                    time.sleep(wait_time)