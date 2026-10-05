"""The BOT AI process (README.md, How it works): all bot brains run here,
never in the game AI. Start it after the main AI (README.md, Run / Play).

    python -m toontown.bots.BotAIStart --base-channel 402000000 --max-channels 999999 \
        --stateserver 4002 --messagedirector-ip 127.0.0.1:7199 [prc ...]
"""
from panda3d.core import *
import builtins

import argparse

parser = argparse.ArgumentParser(description='Open Toontown - bot AI server')
parser.add_argument('--base-channel', default='402000000', help='First channel of the bot AI range.')
parser.add_argument('--max-channels', default='999999', help='Size of the bot AI channel range.')
parser.add_argument('--stateserver', default='4002', help='Control channel of the State Server.')
# 7199 = the message director port in astron/config/astrond.yml (the author's own server used another port)
parser.add_argument('--messagedirector-ip', default='127.0.0.1:7199', help='Message Director to connect to.')
parser.add_argument('--eventlogger-ip', help='Astron Event Logger to log to.')
parser.add_argument('config', nargs='*', default=['etc/Configrc.prc'], help='PRC file(s) to load.')
args = parser.parse_args()

for prc in args.config:
    loadPrcFile(prc)

localConfig = 'air-base-channel %s\nair-channel-allocation %s\nair-stateserver %s\nair-connect %s\n' % (
    args.base_channel, args.max_channels, args.stateserver, args.messagedirector_ip)
# owner 09-25 "rubber banding": the shared Configrc has collect-tcp 1 (datagrams held and sent together every
# 0.2 s+). For the bot AI that held setSmPosHpr samples up to 0.8 s and sent them in one burst (a client's smoother
# draws 0.2 s behind, so it froze the toon, then jumped), and skewed the bots' clock sync round trips (the stamps
# moved ~0.17 s at each 30 s resync). Every bot update goes out at once, as a real client's position does.
localConfig += 'collect-tcp 0\n'
# ... and every main-loop frame slept 40 ms (ai-sleep, AIBase), so the 0.05 s director pass ran every 0.09-0.13 s and
# a watched moving bot's samples came 0.17 s apart. 10 ms keeps the pass on time.
localConfig += 'ai-sleep 0.01\n'
if args.eventlogger_ip:
    localConfig += 'eventlog-host %s\n' % args.eventlogger_ip
loadPrcFileData('Bot AI Args Config', localConfig)


class game:
    name = 'toontown'
    process = 'server'


builtins.game = game

from otp.ai.AIBaseGlobal import *
from toontown.bots.BotAIRepository import BotAIRepository
from toontown.bots.BotDirector import BotDirector

simbase.air = BotAIRepository(int(args.base_channel), int(args.stateserver))
if config.GetBool('want-bot-toons', True):
    simbase.air.botDirector = BotDirector(simbase.air)
    simbase.air.botDirector.start()

host, port = args.messagedirector_ip, 7199
if ':' in host:
    host, port = host.split(':', 1)
    port = int(port)
simbase.air.connect(host, port)

try:
    run()
except SystemExit:
    raise
except Exception:
    from otp.otpbase import PythonUtil
    print(PythonUtil.describeException())
    raise
