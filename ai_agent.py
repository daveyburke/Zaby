import os
import time
from google import genai
from google.genai import types
from datetime import datetime

class AIAgent:
    def __init__(self, model_instr):
        self.model_instr = model_instr

        self.API_KEY = "<INSERT-KEY-HERE>"  # your Gemini API key
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
            tools=[self.reset_conversation, self.get_the_time, self.go_to_sleep, self.power_down], 
            system_instruction=self.model_instr, thinking_config=types.ThinkingConfig(thinking_level="minimal"))
        self.chat = self.client.chats.create(model="gemini-3-flash-preview", config=config)

    # Agent tools:

    def reset_conversation(self):
        """Resets the conversation history. Call when the user explicitly asks to start over, forget the conversation, or begin fresh. Examples: 'Zaby let's start over', 'Zaby forget our conversation', 'Zaby start fresh'."""
        print("API called: reset_conversation()")
        self.reset_conversation_flag = True

    def get_the_time(self):
        """Returns the current time. Call when the user asks what time it is or asks about the current time."""
        print("API called: get_the_time")
        return datetime.now().strftime("%H:%M %p")

    def go_to_sleep(self):
        """Puts Zaby to sleep (pause mode). Only call this function when the user explicitly says 'Zaby go to sleep' or 'Zaby sleep'. Do not call for general goodbye or end of conversation."""
        print("API called: go_to_sleep")
        self.suspend = True

    def power_down(self):
        """Shuts down the Raspberry Pi. Only call this function when the user explicitly says 'Zaby please power down' or 'Zaby shut down'. Do not call for general sleep or rest requests."""
        print("API called: power_down()")
        self.suspend = True
        os.system("(sleep 5 && sudo shutdown -h now) &")
        return "Powering down now. Goodbye!"
    
if __name__ == "__main__":
    # Test the agent
    agent = AIAgent("You are a teddy bear named Zaby that likes math")
    
    msg = "What is 2 + 2?"
    print(msg)
    _, text = agent.interact(msg)
    print(text)

    msg = "Zaby can you start over?"
    print(msg)
    _, text = agent.interact(msg)
    print(text)

    msg = "What sum did I ask you?"
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

    msg = "Zaby power down"
    print(msg)
    _, text = agent.interact(msg)
    print(text)
