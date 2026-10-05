#!/bin/sh
# Start this after the AI (district) server has finished booting. The bot toons log in
# through the server like players; see "Bot settings" in README.md to change how many.
cd ..

BASE_CHANNEL=402000000
MAX_CHANNELS=999999
STATE_SERVER=4002
MESSAGE_DIRECTOR_IP="127.0.0.1:7199"
EVENT_LOGGER_IP="127.0.0.1:7197"

python3 -m toontown.bots.BotAIStart --base-channel ${BASE_CHANNEL} \
               --max-channels ${MAX_CHANNELS} --stateserver ${STATE_SERVER} \
               --messagedirector-ip ${MESSAGE_DIRECTOR_IP} \
               --eventlogger-ip ${EVENT_LOGGER_IP}
