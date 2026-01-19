# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zaby is an AI-powered animatronic teddy bear that runs on a Raspberry Pi 5. The bear uses Google Cloud Speech-to-Text and Text-to-Speech APIs, powered by Gemini 3.0 Flash. Physical animatronics include speech envelope-tracked mouth movements and neck rotation controlled via GPIO pins.

## Development Commands

### Environment Setup
```bash
python -m venv zaby-env
source zaby-env/bin/activate
pip install -r requirements.txt
```

### Running the Application
```bash
source zaby-env/bin/activate
python main.py
```

### Testing Individual Components
The following modules can be run standalone for testing:
- `python ai_agent.py` - Tests AI agent with conversation flow and tool calls

### Systemd Service Management
```bash
# View logs
sudo journalctl -u zaby.service

# Start/stop service
sudo systemctl start zaby.service
sudo systemctl stop zaby.service

# Enable/disable auto-start on boot
sudo systemctl enable zaby.service
sudo systemctl disable zaby.service
```

## Architecture

### Core Components

The codebase follows a modular architecture with clear separation of concerns:

**main.py** - Application entry point and main loop
- Initializes all components (AI agent, synthesizer, recognizer, animatronics, state manager)
- Defines bear personality via `model_instr` system prompt
- Runs main conversation loop: state machine → speech recognition → AI interaction → speech synthesis
- Handles shutdown signals (SIGTERM, SIGINT)

**ai_agent.py** - AI conversation management using Gemini
- Uses Google GenAI SDK with Gemini 3.0 Flash (model: `gemini-3-flash-preview`)
- Provides tool/function calling capabilities: `reset_conversation()`, `get_the_time()`, `go_to_sleep()`
- Maintains conversation history until reset is triggered
- **IMPORTANT**: Gemini API key must be set in `self.API_KEY` (not using environment variable)

**bear_state.py** - State machine for pause/resume functionality
- Manages bear states: RUNNING, PAUSING, PAUSED, UNPAUSING, TERMINATING
- Handles paw button GPIO input (GPIO pin 2) with 500ms debounce
- Uses threading locks and condition variables for thread-safe state transitions
- Coordinates suspend/resume across synthesizer, recognizer, and animatronics
- Generates beep tones using pygame for state transitions

**speech_recognition.py** - Google Cloud Speech-to-Text integration
- Streaming recognition with interim results displayed
- Runs recognition in separate thread
- Uses PyAudio for audio capture (16kHz, mono, LINEAR16)
- Supports suspend/resume for state management integration
- Blocks until final transcript is received

**speech_synthesis.py** - Google Cloud Text-to-Speech integration
- Uses Neural2 voice (en-US-Neural2-I by default)
- Generates MP3 audio to temporary files
- Plays audio via pygame.mixer while coordinating with bear animatronics
- Text normalization: strips asterisks, converts "Zaby" → "Zabby" for correct pronunciation
- Supports suspend/resume for interruption handling

**bear_animatronics.py** - Physical motor control and speech animation
- Controls two motors via GPIO: mouth (pin 26) and neck (pin 19)
- Performs real-time audio envelope analysis (RMS calculation) at 200 Hz
- Uses threading to synchronize mouth movements with speech playback
- Mouth motor pulse duration maps to speech amplitude (0.0s, 0.1s, 0.2s based on RMS thresholds)
- Neck motor runs continuously during speech

### Threading Architecture

The application uses multiple threads that must coordinate:
- **Main thread**: Runs state machine and conversation loop
- **GPIO button thread**: Handles paw button callbacks
- **Recognition thread**: Streams audio to Google Speech API
- **Envelope tracking thread**: Analyzes audio and triggers mouth movements
- **Mouth motor thread**: Executes motor pulses based on envelope values

All components support `suspend()` and `resume()` to allow graceful interruption when the paw button is pressed.

### GPIO Pin Configuration

- GPIO 2: Paw button input (triggers pause/resume)
- GPIO 26: Mouth motor control (solid state relay)
- GPIO 19: Neck motor control (solid state relay)

### Audio Configuration

The system uses WaveShare USB sound card (ALSA card 2):
- Volume set to 90% in code (enforced on startup and paw button press due to drift issue)
- Audio output configured via `/etc/asound.conf` pointing to `hw:2,0`

## Important Implementation Notes

### Gemini API Key
The Gemini API key is hardcoded in `ai_agent.py` as `self.API_KEY`. This should be set to a valid key from aistudio.google.com before running.

### Google Cloud Authentication
Speech-to-Text and Text-to-Speech use Google Cloud Application Default Credentials. Must run:
```bash
gcloud init
gcloud config set project your-project-name
gcloud auth application-default login
```

### Personality Customization
Bear personality is defined in `main.py` via the `model_instr` variable. This system prompt controls the bear's behavior, tone, knowledge domain, and response style.

### Suspend/Resume Pattern
When adding new components, implement `suspend()` and `resume()` methods to integrate with the state machine. Components should check `self.suspended` flag in loops and exit gracefully.

### Motor Control
The mouth motor has high back EMF and is controlled with simple on/off pulses rather than PWM. Duration of pulses (not duty cycle) controls movement amplitude.
