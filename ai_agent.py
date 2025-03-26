from google import genai
from google.genai import types
from datetime import datetime

class AIAgent:
    def __init__(self, model_instr):
        self.model_instr = model_instr

        self.API_KEY = ""
        self.client = genai.Client(api_key=self.API_KEY)
        self.reset_conversation_flag = False
        self.suspend = False
        self._create_chat()

    def interact(self, prompt):
        if self.reset_conversation_flag:
            self._create_chat()
            self.reset_conversation_flag = False

        response = self.chat.send_message(prompt).text
        old_suspend, self.suspend = self.suspend, False
        return old_suspend, response

    def _create_chat(self):
        config = types.GenerateContentConfig(
            tools=[self.reset_conversation, self.get_the_time, self.go_to_sleep],
            system_instruction=self.model_instr)
        self.chat = self.client.chats.create(model="gemini-2.0-flash", config=config)

    # Agent tools:

    def reset_conversation(self):
        print("API called: reset_conversation()")
        self.reset_conversation_flag = True

    def get_the_time(self):
        print("API called: get_the_time")
        return datetime.now().strftime("%H:%M %p")

    def go_to_sleep(self):
        print("API called: go_to_sleep")
        self.suspend = True

if __name__ == "__main__":
    # Test the agent
    agent = AIAgent("You are a teddy bear named Zaby that likes math")
    
    msg = "Zaby can you start over?"
    print(msg)
    _, text = agent.interact(msg)
    print(text)

    msg = "What time is it?"
    print(msg)
    _, text = agent.interact(msg)
    print(text)

    msg = "Zaby go to sleep"
    print(msg)
    _, text = agent.interact(msg)
    print(text)
