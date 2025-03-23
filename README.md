# Zaby
Zaby is an AI-powered teddy bear envisioned by a 7 yr old called Zach. 
Zaby is a clever, pedagogical, and funny teddy bear that loves talking math (although
you can easily change his personality via model_instr in main.py). Press his paw
to start/stop the conversation.

Zaby uses Google Cloud Speech-to-Text and Text-to-Speech APIs and is powered by
Gemini 2.0 Flash. Runs on a Raspberry Pi 5. Bear animatronics use speech
envelope-tracked mouth movements. 

Here's a demo of the bear: TODO. 


## Parts list
- Raspberry Pi 5 CanaKit - https://www.amazon.com/dp/B0CRSNCJ6Y
- Story Time Teddy - https://www.cuddle-barn.com/products/storytime-teddie
- WaveShare USB sound card - https://www.amazon.com/dp/B0CN1C1VPR
- DC solid state relay x 2 - https://www.amazon.com/dp/B00B888WVC
- USB battery - https://www.amazon.com/dp/B08T8TDS8S
- Mini backpack - https://www.amazon.com/dp/B0DL2LTMPP

## Connections
The bear has two motors - one for his rotating next and one for his mouth (the motor turns to a stop,
resulting in a lot of back EMF and not suitable for a motor controller). The bear has a microswitch
in his paw which we use to start/stop the conversation. 

Raspberry PI GPIO's trigger the solid state DC-to-DC relays to turn on/off the motors (one for each motor).
The relays just apply the bear battery power to the mouth and neck motors. Speech envelope tracking converts
root mean square energy into delay times for the mouth motor so the movement approximately tracks the speech. 

Raspberry Pi 5 pinout: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#view-a-gpio-pinout-for-your-raspberry-pi

Connections:
- Paw button: connect pin 2 (GPIO 2) and pin 6 (ground) to the paw button. 
- Mouth: Connect pin 37 (GPIO 26) and pin 39 (ground) to the mouth relay input. 
- Next: Connect pin 35 (GPIO 19) and pin 39 (ground) to the next relay input. 
- Battery/relay output: Connect bear battery negative to yellow wire of mouth motor (polarity matters) and blue wire of neck motor. Connect red bear battery positive
  to one output of each relay. Connect the other output of each relay to white wire of the mouth motor and orange wire of the neck motor respectively.

## Python environment
```
python -m venv zaby-env
pip install -r requirements.txt
source zaby-env/bin/activate
```

## Google Cloud environment
Requires a Google Cloud project with Speech-to-Text and Text-to-Speech APIs enabled
from console.cloud.google.com.

Install gcloud on the Raspberry PI (see https://cloud.google.com/sdk/docs/install#deb)

Run these commands to set the project and login:
```
gcloud init
gcloud config set project your-project-name
gcloud auth application-default login
```
## Gemini
Get an API key from aistudio.google.com and paste into self.API_KEY in ai_agent.py 

## Run from command line
```source zaby-env/bin/activate
python main.py
```

## Systemd start on boot
Run these commands (assumes code lives in /Code/Zaby - edit accordingly):

```
cp zaby.service /etc/systemd/system/
cp asound.conf /etc/
```

Some useful commands to enable/disable, start/stop, view logs:
```
sudo systemctl enable zaby.service
sudo systemctl disable zaby.service

sudo systemctl start zaby.service
sudo systemctl stop zaby.service

sudo journalctl -u zaby.service
```

