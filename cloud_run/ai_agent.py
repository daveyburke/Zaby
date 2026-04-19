import os
from google import genai
from google.genai import types
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class AIAgent:
    def __init__(self, model_instr):
        self.model_instr = model_instr

        self.API_KEY = os.environ.get("GEMINI_API_KEY", "")
        self.client = genai.Client(api_key=self.API_KEY)
        self.reset_conversation_flag = False
        self.suspend = False
        self.power_down_flag = False
        self.tz = "UTC"  # overridden per-request from the Pi's WS handshake
        self._create_chat()

    def interact(self, prompt):
        if self.reset_conversation_flag:
            self._create_chat()
            self.reset_conversation_flag = False

        response = self.chat.send_message(prompt).text
        old_suspend, self.suspend = self.suspend, False
        old_power_down, self.power_down_flag = self.power_down_flag, False
        return old_suspend, old_power_down, response

    def list_models(self):
        """Returns names of models that support generateContent."""
        names = []
        for m in self.client.models.list():
            actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or []
            if not actions or "generateContent" in actions:
                names.append(m.name)
        return names

    def _create_chat(self):
        # Optimized for fastest not smartest model for fluid conversation
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
        print(f"API called: get_the_time (tz={self.tz})")
        try:
            zone = ZoneInfo(self.tz)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        return datetime.now(zone).strftime("%I:%M %p")

    def go_to_sleep(self):
        """Puts Zaby to sleep (pause mode). Only call this function when the user explicitly says 'Zaby go to sleep' or 'Zaby sleep'. Do not call for general goodbye or end of conversation."""
        print("API called: go_to_sleep")
        self.suspend = True

    def power_down(self):
        """Shuts down the Raspberry Pi. Only call this function when the user explicitly says 'Zaby please power down' or 'Zaby shut down'. Do not call for general sleep or rest requests."""
        print("API called: power_down()")
        self.power_down_flag = True
        self.suspend = True
    
if __name__ == "__main__":
    # Test the agent
    agent = AIAgent("You are a teddy bear named Zaby that likes math")
    
    msg = "What is 2 + 2?"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)

    msg = "Zaby can you start over?"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)

    msg = "What sum did I ask you?"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)

    msg = "What time is it?"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)

    msg = "Zaby go to sleep"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)

    msg = "Zaby power down"
    print(msg)
    _, _, text = agent.interact(msg)
    print(text)
