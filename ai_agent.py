from google import genai

class AIAgent:
    def __init__(self, model_instr):
        self.API_KEY = ""
        
        self.conversation = []
        self.max_entries = 10
        self.gemini = genai.Client(api_key=self.API_KEY)    
        self.conversation.append({"role": "model", "parts": [{"text": model_instr}]})

    def interact(self, prompt):
        if (len(self.conversation) > self.max_entries):  # trim context window and keep model instruction
            self.conversation = [self.conversation[0]] + self.conversation[-(self.max_entries-1):]

        self.conversation.append({"role": "user", "parts": [{"text": prompt}]})
        response = self.gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=self.conversation,
        )
        text = response.candidates[0].content.parts[0].text
        text = self._normalize_text(text)
        self.conversation.append({"role": "model", "parts": [{"text": text}]})

        return text
    
    def _normalize_text(self, text):
        text = text.replace("*", "")
        return text