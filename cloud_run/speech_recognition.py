from google.cloud import speech

class SpeechRecognizer:
    def __init__(self, language_code="en-US", rate=16000):
        self.language_code = language_code
        self.rate = rate

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

    def recognize(self, audio_queue):
        """Performs one shot recognition. Reads PCM chunks from a blocking queue
        (sentinel None ends the stream). Blocks until a final transcript is
        received and returns it."""
        def request_gen():
            while True:
                chunk = audio_queue.get()
                if chunk is None:
                    return
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        print("Speech recognition started...")
        responses = self.client.streaming_recognize(self.streaming_config, request_gen())

        transcript = ""
        for response in responses:
            if not response.results:
                continue
            result = response.results[0]
            if not result.alternatives:
                continue
            transcript = result.alternatives[0].transcript

            if not result.is_final:
                print(f"\r{transcript}", end="")
            else:
                print(f"\rTranscribed: {transcript}")
                return transcript

        return transcript
