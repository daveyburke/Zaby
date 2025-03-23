import threading
import pyaudio
from google.cloud import speech

class SpeechRecognizer:
    def __init__(self, language_code="en-US", rate=16000):
        self.language_code = language_code
        self.rate = rate
        self.chunk = int(rate / 10)  # 100ms chunks
        
        self.client = speech.SpeechClient()
        

        self.config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.rate,
            language_code=self.language_code,
            enable_automatic_punctuation=True,
        )
        
        self.streaming_config = speech.StreamingRecognitionConfig(
            config=self.config,
            interim_results=True
        )
        
        # Initialize audio interface
        self.pyaudio_instance = pyaudio.PyAudio()
        
        # Control flags
        self.is_running = False
        self.suspended = False
        
        # Stream and thread objects
        self.stream = None
        self.recognition_thread = None

        self.results_event = threading.Event()
        self.transcript = ""
        
    def recognize(self):
        """ Performs one shot recognition, blocks until final transcript """
        if self.suspended:
            return

        self._start()
        self.results_event.wait()
        self._stop()

        return self.transcript
    
    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False

    def _start(self):                    
        self.stream = self.pyaudio_instance.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )
        
        # Start recognition in a separate thread
        self.results_event.clear()
        self.recognition_thread = threading.Thread(target=self._run_recognition)
        self.recognition_thread.daemon = True
        self.recognition_thread.start()
        
        print("Speech recognition started...")

    def _generate_requests(self):
        """Generate audio chunks for the API stream"""
        while not self.suspended and not self.results_event.is_set():
            try:
                data = self.stream.read(self.chunk, exception_on_overflow=False)
                yield speech.StreamingRecognizeRequest(audio_content=data)
            except Exception as e:
                print(f"Error reading audio: {e}")
                break

        self.results_event.set()

    def _listen_print_loop(self, responses):
        for response in responses:
            if self.suspended:
                self.results_event.set()
                break
                
            # Skip empty results
            if not response.results:
                continue
                
            result = response.results[0]
            if not result.alternatives:
                continue
                
            self.transcript = result.alternatives[0].transcript
        
            # Display interim or final results
            if not result.is_final:
                print(f"\r{self.transcript}", end="")
            else:
                print(f"\rTranscribed: {self.transcript}")
                self.results_event.set()

    def _run_recognition(self):
        """Main recognition loop running on a thread"""
        try:
            responses = self.client.streaming_recognize(
                self.streaming_config,
                self._generate_requests()
            )
            self._listen_print_loop(responses)
        except Exception as e:
            print(f"\nRecognition error: {e}")

    def _stop(self):
        if self.recognition_thread and self.recognition_thread.is_alive():
            self.recognition_thread.join(timeout=2.0)
        
        # Clean up the audio stream
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        print("Speech recognition stopped")

    def stop_and_cleanup(self):
        self.suspended = True
        self._stop()
        self.pyaudio_instance.terminate()
