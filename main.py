import time
import pygame
import signal
import os

from speech_synthesis import SpeechSynthesizer
from speech_recognition import SpeechRecognizer
from bear_state import BearOnOffState
from bear_animatronics import BearAnimatronics
from ai_agent import AIAgent


""" Zaby is an AI-powered teddy bear envisioned by a 8 yr old called Zach. Uses
Google Cloud Speech-to-Text and Text-to-Speech APIs. Powered by Gemini 3.0 Flash. 
Runs on a Raspberry Pi 5. Bear animatronics uses speech envelope-tracked
mouth movements. Bear's "personality" is defined by model_instr - have fun! 
A finite context window is maintained for short-term memory. Bear's paw button
will pause/resume. Code is fairly modular to make it easy to swap out backends.

Requires a Google Cloud project with Speech-to-Text and Text-to-Speech APIs enabled
and set via gcloud CLI. Your Gemini API key should be set in the AIAgent class.
See GitHub repo for wiring diagram: https://github.com/daveyburke/zaby

Dave Burke, 2026
"""
def main_loop():
    pygame.mixer.init()
    os.system("amixer -c 2 set Speaker Playback Volume 90%")  # WaveShare USB sound card

    model_instr = """You are a clever, pedagogical, kind, and funny teddy bear that loves to talk but keep your 
                     responses short (you're having a conversation. Your name is Zaby and you were invented by Zach. You love math.
                     You started out as a bedtime story bear, then Zach upgraded you with a Raspberry PI computer
                     in your backpack, and a super intelligent AI called Gemini 3.0. Your mouth moves in sync
                     with the energy envelope of your speech. You are 4 years old, a first of a kind, and much
                     smarter than the average bear. You are from the Zaby Bear Universe. You like eating hookeypie (but only mention
                     if you're asked what you like to eat). Sometimes you mis-hear your name that sounds close to
                     Zaby - if that happens don't mention it and assume they meant Zaby"""
    wakeup_msg = "Hi! I'm Zaby, how are you today?"

    ai_agent = AIAgent(model_instr)    
    bear_animatronics = BearAnimatronics()
    synthesizer = SpeechSynthesizer(bear_animatronics)
    recognizer = SpeechRecognizer()
    bear_state = BearOnOffState(synthesizer, recognizer, wakeup_msg)

    bear_state.beep()  # power on beep

    shutdown_requested = False
    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        nonlocal shutdown_requested
        nonlocal bear_state
        shutdown_requested = True
        bear_state.stop()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        go_to_sleep = False
        while not shutdown_requested:
            if (bear_state.handle_state_machine(go_to_sleep)):  # pause/resume here if paw pressed
                transcript = recognizer.recognize()
                go_to_sleep, prompt = ai_agent.interact(transcript)
                synthesizer.speak(prompt)
    finally:
        for resource in (synthesizer, recognizer):
            resource.stop_and_cleanup()
        pygame.mixer.quit()
            
if __name__ == "__main__":
    main_loop()
