import os
import tempfile
import pygame
from google.cloud import texttospeech

class SpeechSynthesizer:
    def __init__(self, bear, language_code="en-US", voice_name="en-US-Neural2-I", 
                 speaking_rate=1.0, pitch=7.0):
        self.client = texttospeech.TextToSpeechClient()

        self.voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
        )
    
        self.audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )

        self.bear = bear
        self.suspended = False

    def speak(self, text):
        if self.suspended:
            return
        
        print(text)
        text = self._normalize_text(text)
        synthesis_input = texttospeech.SynthesisInput(text=text)
    
        try:
            response = self.client.synthesize_speech(
                input=synthesis_input, voice=self.voice, audio_config=self.audio_config
            )
        
            # Create a temporary file to store the audio
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_audio_file:
                temp_audio_file.write(response.audio_content)
                temp_file_path = temp_audio_file.name

            self.bear.animate(temp_file_path)
            
            pygame.mixer.music.load(temp_file_path)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play()
        
            # Wait for the audio to finish playing
            while pygame.mixer.music.get_busy() and not self.suspended:
                pygame.time.Clock().tick(10)

            # Clean up
            pygame.mixer.music.stop()
            self.bear.wait_for_completion()
            os.unlink(temp_file_path)
        
        except Exception as e:
            print(f"Error occurred: {e}")      

    def suspend(self):
        self.suspended = True
        self.bear.suspend()

    def resume(self):
        self.suspended = False
        self.bear.resume()

    def stop_and_cleanup(self):
        self.suspend()
        self.bear.stop_and_cleanup()

    def _normalize_text(self, text):
        text = text.replace("*", "")
        text = text.replace("Zaby", "Zabby")  # correct pronounciation!
        return text