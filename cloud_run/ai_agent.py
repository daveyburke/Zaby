import os
from google import genai
from google.genai import types
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODEL = "gemini-3-flash-preview"


class AIAgent:
    def __init__(self, model_instr):
        self.model_instr = model_instr

        self.API_KEY = os.environ.get("GEMINI_API_KEY", "")
        self.client = genai.Client(api_key=self.API_KEY)
        self.reset_conversation_flag = False
        self.suspend = False
        self.power_down_flag = False
        self.tz = "UTC"  # overridden per-request from the Pi's WS handshake
        self.history: list[types.Content] = []
        self._config = types.GenerateContentConfig(
            tools=[self.reset_conversation, self.get_the_time, self.go_to_sleep, self.power_down],
            system_instruction=self.model_instr,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def interact_stream(self, prompt):
        """Yields response text chunks as Gemini emits them. After exhaustion,
        self.suspend / self.power_down_flag reflect any tool calls made.

        Manual function-call dispatch — automatic function calling hangs under
        streaming (see googleapis/python-genai#331), so we drive the tool loop
        ourselves and manage chat history explicitly."""
        self.suspend = False
        self.power_down_flag = False

        self.history.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

        while True:
            model_parts: list[types.Part] = []
            function_calls = []
            stream = self.client.models.generate_content_stream(
                model=MODEL, contents=self.history, config=self._config,
            )
            for chunk in stream:
                if not chunk.candidates or not chunk.candidates[0].content:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    model_parts.append(part)
                    if getattr(part, "function_call", None):
                        function_calls.append(part.function_call)
                    elif getattr(part, "text", None) and not getattr(part, "thought", False):
                        yield part.text

            self.history.append(types.Content(role="model", parts=model_parts))

            if not function_calls:
                # Apply reset after the acknowledgment streams — we can't wipe
                # history mid-turn without breaking the function_call /
                # function_response pairing the API requires.
                if self.reset_conversation_flag:
                    self.history = []
                    self.reset_conversation_flag = False
                return

            response_parts = [
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": self._dispatch(fc.name, dict(fc.args or {}))},
                )
                for fc in function_calls
            ]
            self.history.append(types.Content(role="user", parts=response_parts))

    def interact(self, prompt):
        """Non-streaming wrapper — collects the full response. Used by tests."""
        text = "".join(self.interact_stream(prompt))
        return self.suspend, self.power_down_flag, text

    def list_models(self):
        """Returns names of models that support generateContent."""
        names = []
        for m in self.client.models.list():
            actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or []
            if not actions or "generateContent" in actions:
                names.append(m.name)
        return names

    def _dispatch(self, name, args):
        fn = getattr(self, name, None)
        if not callable(fn):
            return f"unknown function: {name}"
        result = fn(**args)
        return result if result is not None else "ok"

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
