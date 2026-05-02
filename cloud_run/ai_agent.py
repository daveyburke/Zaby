import os
from google import genai
from google.genai import types
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODEL = "gemini-3-flash-preview"


class AIAgent:
    def __init__(self, get_model_instr, memory):
        # get_model_instr is a callable so the agent picks up edits to
        # INSTRUCTIONS.md between turns without anyone holding stale strings.
        self._get_model_instr = get_model_instr
        self.memory = memory

        self.API_KEY = os.environ.get("GEMINI_API_KEY", "")
        self.client = genai.Client(api_key=self.API_KEY)
        self.reset_conversation_flag = False
        self.suspend = False
        self.power_down_flag = False
        self.tz = "UTC"  # overridden per-request from the Pi's WS handshake
        self.battery_voltage = None  # overridden per-request from the Pi's WS handshake
        self.last_client_id = None   # tracks Pi boot identity for history reset
        self.history: list[types.Content] = []

    def note_client(self, client_id):
        """Called at the start of each conversation. The Pi generates a fresh
        client_id (UUID) every boot, so a change here means 'Pi just rebooted'
        — wipe in-RAM history so the new session starts fresh. Same id (e.g.
        paw-button pause/resume) preserves history."""
        if not client_id or client_id == self.last_client_id:
            return
        if self.last_client_id is not None:
            print(f"client_id changed ({self.last_client_id} -> {client_id}); resetting history")
            self.history = []
        self.last_client_id = client_id

    def _build_config(self):
        # Built fresh each turn so live edits to INSTRUCTIONS.md and
        # MEMORY.md (web UI or save_memory) take effect immediately.
        full_instr = (
            self._get_model_instr()
            + "\n\n=== Things you remember ===\n"
            + self.memory.read()
        )
        # Strictest blocking on all four categories — the audience is a child,
        # so prefer the rare false-positive over any false-negative. The
        # interact_stream loop catches the resulting empty turn and substitutes
        # a kid-friendly redirect so the bear never goes silent.
        kid_safe = [
            types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE)
            for c in (
                types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            )
        ]
        return types.GenerateContentConfig(
            tools=[
                self.reset_conversation,
                self.get_the_time,
                self.get_battery_voltage,
                self.go_to_sleep,
                self.power_down,
                self.save_memory,
                self.search_memory,
            ],
            system_instruction=full_instr,
            safety_settings=kid_safe,
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

        config = self._build_config()
        text_yielded = False
        while True:
            model_parts: list[types.Part] = []
            function_calls = []
            stream = self.client.models.generate_content_stream(
                model=MODEL, contents=self.history, config=config,
            )
            for chunk in stream:
                if not chunk.candidates or not chunk.candidates[0].content:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    model_parts.append(part)
                    if getattr(part, "function_call", None):
                        function_calls.append(part.function_call)
                    elif getattr(part, "text", None) and not getattr(part, "thought", False):
                        text_yielded = True
                        yield part.text

            self.history.append(types.Content(role="model", parts=model_parts))

            if not function_calls:
                # If nothing was ever yielded across all tool-call rounds, the
                # model was almost certainly safety-blocked (or finished with
                # no content for some other reason). Substitute a kid-friendly
                # redirect so the bear doesn't go silent on the user.
                if not text_yielded:
                    print("interact_stream: no text yielded — emitting safety fallback")
                    yield "Hmm, let's talk about something else! What kind of story should we make up?"
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

    def get_battery_voltage(self):
        """Returns the bear's battery voltage in Volts. Call when the user asks about the battery, voltage, charge level, or how much power is left. Examples: 'Zaby what's your battery voltage?', 'Zaby how charged are you?', 'Zaby is your battery low?'."""
        print(f"API called: get_battery_voltage (V={self.battery_voltage})")
        if self.battery_voltage is None:
            return "unknown"
        return f"{self.battery_voltage:.2f}V"

    def go_to_sleep(self):
        """Puts Zaby to sleep (pause mode). Only call this function when the user explicitly says 'Zaby go to sleep' or 'Zaby sleep'. Do not call for general goodbye or end of conversation. Keep response short and concise."""
        print("API called: go_to_sleep")
        self.suspend = True

    def power_down(self):
        """Shuts down the Raspberry Pi. Only call this function when the user explicitly says 'Zaby please power down' or 'Zaby shut down'. Do not call for general sleep or rest requests."""
        print("API called: power_down()")
        self.power_down_flag = True
        self.suspend = True

    def save_memory(self, fact: str):
        """Saves a durable fact to your long-term memory. Use when you learn something worth remembering across conversations:
         - About the user: name, preferences, family, pets, things they care about.
         - About stories you're brainstorming together: character names and traits, places, planets, magical objects, plot points, ongoing plans.
        Phrase it as a single self-contained sentence so it makes sense out of context later (e.g. 'In the Bramblewick story, the dragon's name is Fenn and she lives on the planet Quill'). Don't save fleeting chit-chat like 'we just said hello'."""
        print(f"API called: save_memory({fact!r})")
        self.memory.append(fact)
        return "saved"

    def search_memory(self, query: str):
        """Searches your long-term memory for facts related to the query. Use when the user asks about something they may have told you before, references a name or place from an earlier story, or you need to recall a preference or past event. Returns the most relevant memory snippets."""
        print(f"API called: search_memory({query!r})")
        results = self.memory.search(query, k=3)
        return "\n---\n".join(results) if results else "no relevant memories found"


if __name__ == "__main__":
    # Test the agent
    from memory import Memory
    _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    _memory = Memory(_client)
    agent = AIAgent(lambda: "You are a teddy bear named Zaby that likes math", _memory)
    
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
