"""Shared kit for the P4 (playground life) and P7 (street) activities. Not an activity itself.

  - chat:      SpeedChat lines a 2013 player says, with a per-zone budget (few bots talk at once)
               and bot-to-bot answers (a question gets an answer from a toon standing near);
  - emotes:    real emote indices (TTEmote.EmoteFunc) the toon has access to; never Belly Flop /
               Banana Peel (a jump or a fall);
  - Visit:     a building visit through the REAL door protocol: exterior Door.requestEnter,
               setOtherZoneIdAndDoId reply, into the interior zone, interior Door.requestExit (every
               client plays the walk-out), stand about / buy gags from a clerk (NPCClerk.avatarEnter +
               a VALID setInventory) / talk to a shopkeeper or HQ officer (NPCToon.avatarEnter and its
               movie answers), then the interior door's requestEnter and the exterior door's requestExit;
  - SuitWatch: where the street's Cogs are right now (their path legs replayed from the street DNA,
               the same SuitLegList the clients use) so bots keep clear of them (no battles, P8);
  - Life stats: counters + per-activity samples in <run>/bots-life.json/.txt (proof numbers).
Interior stand spots come from interiors.json (tools/bake_interiors.py: floor under the spot, no wall
between the spot and the point just inside the door).
"""
import json
import math
import os
import random
import time

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.ClockDelta import globalClockDelta
from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots import space
from toontown.bots.BotToon import WALK_SPEED, replyTo, tripSpeed

notify = DirectNotifyGlobal.directNotify.newCategory('BotLife')
QUIET = OTPGlobals.QuietZone

# ---- SpeedChat (OTPLocalizer SpeedChatStaticText ids) -------------------------------------------
GREET = (100, 101, 102, 103, 104, 105, 107, 108)
GENERAL = (1102, 1102, 1100, 1117, 1000, 1009, 508, 1200, 1299, 303, 309, 304, 306, 315, 600, 507, 1413, 1203,
           1017, 506, 509)
AT_PLACE = {
    'trolley': (1100, 1100, 1100, 1017, 1015, 1000, 1007),
    'gagshop': (1413, 1413, 1102, 803),
    'hq': (1299, 1299, 1200, 1201, 1203, 508),
    'fishing': (1117, 1117, 811),
    'fountain': (105, 106, 1102, 1100),
    'gazebo': (105, 1100, 1102),
    'partygate': (1112, 1113),
    'racingpad': (1111, 1000),
    'kartshop': (1111, 303),
    'golfkart': (1126, 1000),
    'gametable': (1000, 303),
    'picnictable': (1000, 303),
}
HOOD_TRIP = {2000: 1105, 1000: 1106, 4000: 1107, 5000: 1108, 3000: 1109, 9000: 1110, 8000: 1111, 6000: 1125,
             17000: 1126}
STREET = (1102, 1102, 1103, 508, 1203, 1413, 1414, 1000, 1007, 505, 1101, 1104)
INSIDE = {'gagshop': (1413, 303, 309), 'hq': (1299, 1200, 1203, 1201), 'petshop': (303, 308, 600),
          'toon': (100, 101, 303), 'toonhall': (308, 303, 105)}
# a line that asks for an answer -> what a toon standing near says back
ANSWERS = {1100: (1000, 1000, 305, 2, 1011, 1017), 1102: (1000, 508, 305, 304), 1117: (1000, 303, 2),
           1200: (1203, 1299, 1201), 1299: (1201, 3), 507: (3, 301, 1), 508: (1000, 305, 3), 1413: (3, 1203),
           1103: (1000, 305), 1105: (1000, 3), 1106: (1000, 3), 1107: (1000, 3), 1108: (1000, 3), 1109: (1000, 2),
           1110: (1000, 3), 1111: (1000, 303), 1125: (1000, 3), 1126: (1000, 303), 600: (500,), 1009: (1000, 3)}
CHAT_GAP = {'playground': 3.5, 'street': 8.0, 'interior': 6.0}     # s between two lines in one zone
BOT_GAP = (25.0, 70.0)                                              # s between two lines of one bot

# ---- emotes (TTEmote.EmoteFunc indices; 12 Belly Flop and 14 Banana Peel fall over: never) --------
EMOTE_W = {0: 5, 1: 4, 6: 3, 24: 3, 9: 2, 13: 1.5, 7: 1.5, 11: 1, 20: 1, 22: 1.5, 5: 1, 2: 0.3, 4: 0.2}
# catalog emotes a 2013 regular had bought, and how many had each (granted once per bot, seeded)
CATALOG = {5: 0.4, 6: 0.65, 7: 0.35, 8: 0.2, 9: 0.45, 10: 0.2, 11: 0.3, 13: 0.35, 20: 0.3, 22: 0.3, 24: 0.55}
EMOTE_GAP = 4.0


class Life:
    """Counters and samples for the proof; one per process."""
    def __init__(self):
        self.t0 = time.time()
        self.ev = {}
        self.byKind = {'door_enter': {}, 'door_exit': {}}
        self.samples = {}
        self.nSamples = 0
        self.lines = []            # (time, zone)
        self.zoneLast = {}
        self.zoneEmote = {}
        self.suitNear = [0, 0]     # [samples of street bots, samples with a Cog within 6 ft]
        self.hqHist = {}           # bots inside one Toon HQ -> samples (HQs with nobody inside are not listed)
        self.hqNow = {}
        self.task = None
        self.director = None

    def count(self, key, n=1):
        self.ev[key] = self.ev.get(key, 0) + n

    def kind(self, table, kind):
        d = self.byKind[table]
        d[kind] = d.get(kind, 0) + 1

    def ensureTask(self, director):
        if self.task is None:
            self.director = director
            self.task = taskMgr.doMethodLater(5.0, self.__sample, 'bots-life-sample')

    def __sample(self, task):
        try:
            self.__doSample()
        except Exception:
            import traceback
            notify.warning('[TTBOTS] life sample: %s' % traceback.format_exc())
        return task.again

    def __doSample(self):
        d = self.director
        now = time.time()
        nowLabels = {}
        hq = {}
        for bot in d.bots.values():
            if bot.state != 'present':
                continue
            if bot.travel is not None:
                lab = 'traveling'
            elif bot.activity is None:
                lab = 'free'
            else:
                lab = getattr(bot.activity, 'label', None) or bot.activity.name
                if lab == 'stroll':
                    lab = 'strolling'
            nowLabels[lab] = nowLabels.get(lab, 0) + 1
            if lab == 'in-HQ':
                hq[bot.zoneId] = hq.get(bot.zoneId, 0) + 1
            if bot.area is not None and bot.area.kind == 'street':
                nowLabels['on-street'] = nowLabels.get('on-street', 0) + 1
                if bot.zoneId in bot.area.zones:
                    self.suitNear[0] += 1
                    near = SUITS.nearest(d, bot.area, bot.pos)
                    if near is not None and near < 6.0:
                        self.suitNear[1] += 1
        for k, v in nowLabels.items():
            self.samples[k] = self.samples.get(k, 0) + v
        for z, n in hq.items():
            h = self.hqHist.setdefault(n, 0)
            self.hqHist[n] = h + 1
        self.hqNow = hq
        self.nSamples += 1
        self.lines = [x for x in self.lines if now - x[0] < 900]
        if self.nSamples % 2 == 0:
            self.__write(now, nowLabels)

    def __write(self, now, nowLabels):
        d = self.director
        per = {}
        for t, z in self.lines:
            if now - t <= 600:
                per[z] = per.get(z, 0) + 1
        span = min(600.0, now - self.t0)
        perMin = {z: round(n * 60.0 / max(span, 1.0), 2) for z, n in sorted(per.items())}
        data = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'uptime': int(now - self.t0),
                'samples': self.nSamples, 'labelsNow': nowLabels,
                'labelsAvg': {k: round(v / float(self.nSamples), 1) for k, v in sorted(self.samples.items())},
                'events': self.ev, 'doorEnter': self.byKind['door_enter'], 'doorExit': self.byKind['door_exit'],
                'scPerMinByZone_last10min': perMin, 'streetSuitSamples': self.suitNear,
                'hqInsideNow': self.hqNow, 'hqInsideHist': self.hqHist}
        lines = ['TTBOTS life  %s  up %ds  %d samples' % (data['time'], data['uptime'], self.nSamples),
                 'now:  ' + '  '.join('%s %d' % kv for kv in sorted(nowLabels.items())),
                 'avg:  ' + '  '.join('%s %.1f' % kv for kv in sorted(data['labelsAvg'].items())),
                 'events: ' + '  '.join('%s %d' % kv for kv in sorted(self.ev.items())),
                 'door enter: ' + '  '.join('%s %d' % kv for kv in sorted(data['doorEnter'].items())),
                 'door exit:  ' + '  '.join('%s %d' % kv for kv in sorted(data['doorExit'].items())),
                 'SpeedChat lines/min (last 10 min) by zone: ' + '  '.join('%s:%s' % kv for kv in perMin.items()),
                 'street bots within 6 ft of a Cog: %d of %d samples' % (self.suitNear[1], self.suitNear[0]),
                 'Toon HQ interiors now: %s  (bots inside -> samples: %s)' % (self.hqNow, self.hqHist)]
        # a street a real player is on: where its bots are, seen from him (zone, distance, what they do)
        for a in d.world.areas.values():
            if a.kind != 'street' or not a.players:
                continue
            for avId in a.players:
                v = d.viewOf(d.players.get(avId))
                o = v.objects.get(avId) if v is not None else None
                if o is None:
                    continue
                rows = []
                for b in d.bots.values():
                    if b.area is a and b.state == 'present':
                        lab = 'travel' if b.travel else (getattr(b.activity, 'label', None) if b.activity else 'free')
                        rows.append('%s:%d:%s' % (b.zoneId, int((b.pos - o.pos).length()), lab))
                lines.append('watched %s: player in %s at (%d,%d); bots zone:dist:label %s' % (
                    a.name, d.players.get(avId), o.pos[0], o.pos[1], ' '.join(sorted(rows))))
        try:
            for name, text in (('bots-life.txt', '\n'.join(lines) + '\n'), ('bots-life.json', json.dumps(data))):
                tmp = os.path.join(d.runDir, name + '.new')
                with open(tmp, 'w') as f:
                    f.write(text)
                os.replace(tmp, os.path.join(d.runDir, name))
        except Exception:
            import traceback
            notify.warning('[TTBOTS] life status file: %s' % traceback.format_exc())


LIFE = Life()


# ---- chat and emotes --------------------------------------------------------------------------------
def zoneKind(bot):
    if bot.area is None:
        return 'playground'
    if bot.zoneId not in bot.area.zones:
        return 'interior'
    return bot.area.kind


def canSay(bot, now):
    if now < getattr(bot, '_lifeNextSay', 0.0):
        return False
    return now - LIFE.zoneLast.get(bot.zoneId, 0.0) >= CHAT_GAP.get(zoneKind(bot), 4.0)


def say(bot, msgId, now=None, force=False, answer=True):
    """One SpeedChat line from the bot, if its zone's budget allows (force: an answer to a real player)."""
    now = now if now is not None else globalClock.getRealTime()
    if not force and not canSay(bot, now):
        return False
    bot.say(msgId)
    bot._lifeNextSay = now + random.uniform(*BOT_GAP)
    LIFE.zoneLast[bot.zoneId] = now
    LIFE.lines.append((time.time(), bot.zoneId))
    LIFE.count('sc_lines')
    if answer:
        __answerLater(bot, msgId)
    return True


def __answerLater(speaker, msgId):
    """A question or a greeting: a toon standing near (a bot) may answer it."""
    if msgId in GREET:
        choices, p = None, 0.45
    elif msgId in ANSWERS:
        choices, p = ANSWERS[msgId], 0.55
    else:
        return
    if random.random() > p:
        return
    d = speaker.director
    near = [b for b in d.bots.values() if b is not speaker and b.state == 'present' and b.travel is None
            and b.zoneId == speaker.zoneId and b.activity is not None and getattr(b.activity, 'chatty', False)
            and (b.pos - speaker.pos).length() < 22.0]
    if not near:
        return
    b = random.choice(near)
    reply = random.choice(choices) if choices else replyTo(msgId)

    def answer(b=b, speaker=speaker, reply=reply):
        if b.state == 'present' and b.travel is None and b.zoneId == speaker.zoneId and not b.path:
            b.faceTo(speaker.pos)
            if msgId == ASK_TASK and taskLine(b):
                return
            say(b, reply, force=True, answer=False)
            LIFE.count('sc_answers_bot')
    b.later(1.2, 3.5, 'lifeanswer', answer)


NO_ANSWER = set()       # P9: player commands (activities/commands.py answers those itself)
ASK_TASK = 1200         # "What ToonTask are you working on?"


def taskLine(bot):
    """PROGRESSION: its own ToonTask from the SpeedChat ToonTask menu, as a player answers that question."""
    from toontown.bots import progress
    if getattr(bot, 'crew', False):
        return False
    goal = getattr(bot, 'goal', None)
    return progress.sayTask(bot, goal.get('qid') if goal else None)


def answerReal(bot, speaker, msgId):
    """A REAL player said msgId near this bot: face him and answer (and wave at a greeting)."""
    if msgId in NO_ANSWER:
        return False
    if (bot.pos - speaker.pos).length() > 30.0 or bot.path:
        return False
    if now_() < getattr(bot, '_lifeAnsweredUntil', 0.0):
        return False
    bot._lifeAnsweredUntil = now_() + 4.0
    reply = random.choice(ANSWERS[msgId]) if msgId in ANSWERS else replyTo(msgId)

    def answer():
        if bot.state == 'present' and bot.travel is None:
            bot.faceTo(speaker.pos)
            if msgId == ASK_TASK and taskLine(bot):
                LIFE.count('sc_answers_real')
                return
            say(bot, reply, force=True, answer=False)
            LIFE.count('sc_answers_real')
            if msgId in GREET and random.random() < 0.4:
                emote(bot, 0, force=True)
    bot.later(1.0, 2.8, 'lifereal', answer)
    return True


def now_():
    return globalClock.getRealTime()


def emoteAccess(bot):
    acc = list((bot.ownFields.get('setEmoteAccess') or ([1] * 5 + [0] * 15,))[0])
    if not getattr(bot, '_lifeGranted', False) and not any(acc[i] for i in CATALOG if i < len(acc)):
        # the catalog emotes this regular had bought (seeded by the toon, the same every login)
        bot._lifeGranted = True
        rng = random.Random(bot.avId * 7 + 3)
        acc = acc + [0] * (25 - len(acc))
        for i, p in CATALOG.items():
            if rng.random() < p:
                acc[i] = 1
        try:
            bot.air.sendInternal(bot.avId, 'DistributedToon', 'setEmoteAccess', [acc])
            bot.ownFields['setEmoteAccess'] = (acc,)
            LIFE.count('emote_access_granted')
        except Exception:
            pass
    return acc


def emote(bot, index=None, now=None, force=False):
    now = now if now is not None else globalClock.getRealTime()
    if bot.path:
        return False
    if not force and now - LIFE.zoneEmote.get(bot.zoneId, 0.0) < EMOTE_GAP:
        return False
    if index is None:
        acc = emoteAccess(bot)
        opts = [(i, w) for i, w in EMOTE_W.items() if i < len(acc) and acc[i]]
        if not opts:
            return False
        r = random.uniform(0, sum(w for _, w in opts))
        for index, w in opts:
            r -= w
            if r <= 0:
                break
    bot.emote(index)
    LIFE.zoneEmote[bot.zoneId] = now
    LIFE.count('emotes')
    LIFE.count('emote_%d' % index)
    return True


def placeLine(bot, placeKind=None):
    """A line that fits where the bot stands."""
    r = random.random()
    if r < 0.25:
        return random.choice(GREET)
    if placeKind in AT_PLACE and r < 0.65:
        return random.choice(AT_PLACE[placeKind])
    if r < 0.72 and bot.area is not None:
        trips = [v for k, v in HOOD_TRIP.items() if k != bot.area.hood]
        return random.choice(trips)
    return random.choice(GENERAL)


# ---- interiors --------------------------------------------------------------------------------------
_INT = None


def interiors():
    global _INT
    if _INT is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'interiors.json')) as f:
            _INT = json.load(f)
    return _INT


INTERIOR_OF = {'gagshop': 'gagshop', 'hq': 'hq', 'petshop': 'petshop', 'toonhall': 'toonhall'}
LABEL_OF = {'gagshop': 'in-shop', 'hq': 'in-HQ', 'petshop': 'in-shop', 'clothes': 'in-shop', 'toonhall': 'in-building',
            'door': 'in-building', 'bank': 'in-building', 'library': 'in-building', 'school': 'in-building'}
CAP = {'gagshop': 3, 'hq': 2, 'petshop': 2, 'clothes': 2}      # bots inside at once (default 2)
EXIT_WAIT = 6.0         # personal space: s a bot waits inside while a toon stands on the door's way-out spot


def interiorZone(extZone, block):
    return extZone - extZone % 100 + 500 + block


def layoutFor(kind, intZone):
    data = interiors()
    key = INTERIOR_OF.get(kind)
    if key is None:
        # a toon building: the room is picked by the client with random.Random(interior zone)
        codes = data['TI_codes']
        key = codes[random.Random(intZone).randint(0, len(codes) - 1)]
    return data[key]


def visitKinds(area):
    return [p for p in area.doors if p['kind'] != 'kartshop']


class Visit:
    """One building visit, driven from an activity's step(). step() -> True while it runs."""

    def __init__(self, act, place, extDoId, stay=None):
        self.act = act
        self.bot = act.bot
        self.d = act.director
        self.place = place
        self.kind = place['kind']
        self.extDoId = extDoId
        self.area = self.bot.area
        self.extZone = self.d.world.doorZone(self.area, place)
        self.intZone = interiorZone(self.extZone, place['extra']['block'])
        self.stayFor = stay if stay is not None else random.uniform(20.0, 60.0)
        self.phase = 'walk'
        self.until = 0.0
        self.reply = None
        self.intDoId = None
        self.view = None
        self.layout = None
        self.door = None           # interior layout door dict
        self.at = None             # where the bot stands inside
        self.trail = []            # points walked from the entry (to walk back)
        self.task = None           # 'buy' / 'talk' / 'stand'
        self.npc = None
        self.npcState = None
        self.npcDue = 0.0
        self.npcAction = None
        self.sent = None
        self.inside = False
        self.want = None           # PROGRESSION: 'hq' or a quest NPC id: talk to that one (toontask.TaskRun)
        self.wantMode = None       # 'gettask' / 'visit'
        self.treatMet = None       # HALLOWEEN: trickOrTreatTargetMet's beans (0 = that shop was found before)
        self.label = 'to-' + ('HQ' if self.kind == 'hq' else 'shop' if self.kind in ('gagshop', 'petshop', 'clothes')
                              else 'building')

    # -- life cycle --------------------------------------------------------------------------------
    def begin(self):
        node = self.area.placeNode(self.place)
        if node is None or not self.bot.walkTo(node, *tripSpeed()):
            return False
        self.until = now_() + 90.0
        return True

    def doorLive(self):
        v = self.d.viewOf(self.extZone)
        return v is not None and self.extDoId in v.objects

    def onDirect(self, fieldName, args):
        if fieldName == 'setOtherZoneIdAndDoId':
            self.reply = ('ok', args[0], args[1])
        elif fieldName == 'trickOrTreatTargetMet':        # HALLOWEEN: the shop's answer (beans, 0 = found before)
            self.treatMet = args[0]
        elif fieldName == 'rejectEnter':
            self.reply = ('reject',)
        elif fieldName == 'freeAvatar':
            if self.npcState in ('asked',):
                self.npcState = 'refused'
        elif fieldName == 'setInventory' and self.sent is not None:
            ok = bytes(args[0]) == bytes(self.sent)
            LIFE.count('buy_accepted' if ok else 'buy_rejected')
            if not ok:
                notify.warning('[TTBOTS] %s gag purchase came back different (rejected)' % self.bot.avId)
            self.sent = None

    def onField(self, obj, fieldName, args):
        if self.npc is None or obj.doId != self.npc.doId or fieldName != 'setMovie':
            return
        mode, avId = args[0], args[2]
        cls = obj.className
        if cls == 'DistributedNPCClerk':
            if avId == self.bot.avId and mode == 1:                 # PURCHASE_MOVIE_START: the gag shop panel
                self.npcState, self.npcDue = 'shopping', now_() + random.uniform(4.0, 11.0)
            elif avId == self.bot.avId and mode == 3:               # NO_MONEY
                self.npcState = 'done'
            elif mode in (0, 2, 8) and self.npcState in ('shopping', 'paid', 'asked'):
                self.npcState = 'done'
        else:
            quests = args[3]
            if avId == self.bot.avId:
                if mode == 6 and quests:                            # QUEST_CHOICE: pick one like a player
                    from toontown.bots import progress
                    self.npcState, self.npcDue = 'choose', now_() + random.uniform(3.0, 7.0)
                    take = self.want is not None or random.random() < 0.75
                    self.npcAction = ('chooseQuest', [progress.chooseQuest(self.bot, list(quests)) if take else 0])
                elif mode == 8 and quests:                          # TRACK_CHOICE
                    from toontown.bots import progress
                    self.npcState, self.npcDue = 'choose', now_() + random.uniform(4.0, 8.0)
                    self.npcAction = ('chooseTrack', [progress.chooseTrack(self.bot, list(quests))])
                elif mode in (1, 11):                               # REJECT / TIER_NOT_DONE: read it, done
                    self.npcState, self.npcDue = 'reading', now_() + random.uniform(2.0, 3.5)
                    if self.wantMode == 'gettask' and getattr(self.bot, 'prog', None) is not None:
                        self.bot.prog.noTasks('officer mode %d' % mode)
                elif mode in (2, 3, 4):                             # COMPLETE / INCOMPLETE / ASSIGN
                    self.npcState, self.npcDue = 'reading', now_() + random.uniform(5.0, 10.0)
                    LIFE.count('npc_task_movie_%d' % mode)
            if mode in (0, 10) and self.npcState in ('asked', 'reading', 'choose'):
                self.npcState = 'done'                              # CLEAR / TIMEOUT

    # -- the steps -------------------------------------------------------------------------------------
    def step(self, now):
        bot = self.bot
        ph = self.phase
        if ph == 'walk':
            if bot.path:
                if now > self.until:
                    bot.stopWalking()
                    return False
                return True
            if not self.doorLive():
                return False
            bot.faceTo(self.place['pos'])
            bot.setAnim('neutral')
            if not bot.settled(now):
                return True                  # smoothness: the client's door walk starts where it drew me stop
            bot.send('requestEnter', [], doId=self.extDoId, className='DistributedDoor')
            self.reply = None
            self.phase, self.until = 'enter', now + 3.0
        elif ph == 'enter':
            if self.reply and self.reply[0] == 'ok':
                self.intZone, self.intDoId = self.reply[1], self.reply[2]
                # the walk into the door is the client's own door track (smoothing stopped): a sample of mine now
                # would yank the toon off it (5 ft pops). I only stand where it ends; nothing is broadcast.
                x, y, z = self.place['pos']
                bot.pos = Point3(x, y, z)
                LIFE.kind('door_enter', self.kind)
                self.act.interruptible = False
                self.phase, self.until = 'doorin', now + 1.6
            elif self.reply or now > self.until:
                LIFE.count('door_rejected')
                return False
        elif ph == 'doorin':
            if now >= self.until:
                self.__goIn()
                self.phase, self.until = 'arrive', now + 2.2
        elif ph == 'arrive':
            if now >= self.until:
                bot.setAnim('neutral', force=True)
                self.phase, self.until = 'stay', now + self.stayFor
                self.label = LABEL_OF.get(self.kind, 'in-building')
                self.__plan(now)
        elif ph == 'stay':
            return self.__stay(now)
        elif ph == 'leavewalk':
            if not bot.path:
                # personal space: every client draws a toon coming out of a door on one spot in front of it; while
                # a toon stands there, wait inside a moment (EXIT_WAIT s at most)
                if not getattr(self, 'exitBy', 0.0):
                    self.exitBy = now + EXIT_WAIT
                if now < self.exitBy and not self.__exitClear():
                    return True
                self.__faceDoor()
                bot.setAnim('neutral')
                self.reply = None
                bot.send('requestEnter', [], doId=self.intDoId, className='DistributedDoor')
                self.phase, self.until = 'leaveenter', now + 3.0
        elif ph == 'leaveenter':
            if self.reply or now > self.until:
                x, y, z = self.door['pos']
                bot.path = [(x, y, z, None)]
                bot.speed = WALK_SPEED
                bot.setAnim('walk')
                self.phase, self.until = 'doorout', now + 1.6
        elif ph == 'doorout':
            # (personal space: the way-out spot looked again just before stepping out, up to EXIT_WAIT s more)
            if now >= self.until and (self.__exitClear() or now >= self.until + EXIT_WAIT):
                self.__comeOut()
                self.phase, self.until = 'out', now + 2.0
        elif ph == 'out':
            if now >= self.until:
                bot.setAnim('neutral', force=True)
                self.act.interruptible = True
                return False
        return True

    def __goIn(self):
        bot = self.bot
        self.view = bot.air.openView(bot.air.districtId, self.intZone)
        self.layout = layoutFor(self.kind, self.intZone)
        doors = self.layout['doors']
        idx = 0
        if len(doors) > 1:
            v = self.d.viewOf(self.extZone)
            o = v.objects.get(self.extDoId) if v is not None else None
            di = (o.get('setDoorIndex') or (0,))[0] if o is not None else 0
            idx = di if di < len(doors) else 0
        self.door = doors[idx]
        ex, ey, ez = self.door['entry']
        bot.path = []
        bot.relocate(QUIET)
        bot.pos = Point3(ex, ey, ez)
        bot.h = self.door['h'] + 180.0
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.intZone)
        bot.send('requestExit', [], doId=self.intDoId, className='DistributedDoor')
        self.inside = True
        self.at = tuple(self.door['entry'])
        self.trail = []

    def __comeOut(self, silent=False):
        bot = self.bot
        node = self.area.placeNode(self.place)
        bot.path = []
        bot.relocate(QUIET)
        bot.node = node
        bot.pos = Point3(*self.area.wm.pos(node))
        x, y, z = self.place['pos']
        bot.faceTo((2 * bot.pos[0] - x, 2 * bot.pos[1] - y))     # back to the door
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.extZone)
        bot.send('requestExit', [], doId=self.extDoId, className='DistributedDoor')
        space.doorUsed(self.extDoId)
        LIFE.kind('door_exit', self.kind)
        self.inside = False
        self.__closeView()

    def __exitClear(self):
        """Nobody stands where this door puts a toon coming out (its walk-map node and its step)."""
        if not space.doorFree(self.extDoId):
            return False                 # someone came out of it a moment ago
        return all(space.clear(self.d, self.area, p[0], p[1], p[2], self.bot) for p in space.doorSpots(self.area)
                   if math.hypot(p[0] - self.place['pos'][0], p[1] - self.place['pos'][1]) < 6.0)

    def __closeView(self):
        if self.view is not None:
            self.bot.air.closeView(self.view)
            self.view = None

    def __faceDoor(self):
        self.bot.faceTo(self.door['pos'])

    # -- inside -------------------------------------------------------------------------------------------
    def __walkInside(self, points):
        """Walk straight legs (every leg proven wall-free by the bake)."""
        bot = self.bot
        bot.path = [(p[0], p[1], p[2], None) for p in points]
        bot.speed = WALK_SPEED
        bot.setAnim('walk')
        bot.nextTick = min(bot.nextTick, now_())

    def __back(self):
        """Points back to the entry along the trail (reversed)."""
        pts = list(reversed(self.trail[:-1])) + [tuple(self.door['entry'])]
        self.trail = []
        return pts

    def __goto(self, legs):
        pts = self.__back() if self.trail else []
        pts += legs
        self.trail = list(legs)
        self.at = tuple(legs[-1])
        self.__walkInside(pts)

    def __npcs(self, classes):
        if self.view is None:
            return []
        return [o for o in self.view.objects.values() if o.className in classes]

    def __plan(self, now):
        """What to do in here: buy gags, talk to an NPC, or just stand about."""
        real = self.d.realPlayersIn(self.intZone)
        self.task = 'stand'
        if self.wantMode == 'treat':                       # HALLOWEEN: stand in, then say it (__stay)
            self.__standSomewhere()
            self.task, self.npcDue = 'treat', now_() + random.uniform(2.0, 5.0)
            return
        if self.want is not None:
            # PROGRESSION: the task's NPC (by name) or any HQ officer; a player waits his turn, the NPC says busy
            name = None
            if self.want != 'hq':
                from toontown.toon import NPCToons
                name = NPCToons.getNPCName(self.want)
            npcs = [o for o in self.__npcs(('DistributedNPCToon', 'DistributedNPCSpecialQuestGiver'))
                    if name is None or (o.get('setName') or ('',))[0] == name]
            free = [o for o in npcs if (o.get('setMovie') or (0,))[0] in (0,)]
            if free:
                self.__approach(random.choice(free), 'talk')
                return
            if npcs:
                self.__standSomewhere()
                self.npcDue = now_() + random.uniform(4.0, 8.0)
                self.task = 'waitnpc'
                return
        elif not real:
            if self.kind == 'gagshop':
                clerks = [o for o in self.__npcs(('DistributedNPCClerk',)) if (o.get('setMovie') or (0,))[0] in (0,)]
                if clerks and (self.bot.ownFields.get('setMoney') or (0,))[0] > 0:
                    self.__approach(random.choice(clerks), 'buy')
                    return
            elif self.kind == 'hq' or (self.kind in ('door', 'bank', 'library', 'school') and random.random() < 0.5):
                npcs = [o for o in self.__npcs(('DistributedNPCToon',)) if (o.get('setMovie') or (0,))[0] in (0,)]
                if npcs and random.random() < (0.55 if self.kind == 'hq' else 1.0):
                    self.__approach(random.choice(npcs), 'talk')
                    return
        self.__standSomewhere()

    def __standSomewhere(self):
        spots = self.door.get('spots') or []
        if spots:
            self.__goto([tuple(random.choice(spots))])
        self.task = 'stand'
        self.npcDue = now_() + random.uniform(4.0, 10.0)

    def __approach(self, npc, task):
        idx = (npc.get('setPositionIndex') or (0,))[0]
        info = next((c for c in self.layout['npcs'] if c['index'] == idx), None)
        di = self.layout['doors'].index(self.door)
        if info is not None and di in info.get('ok', []):
            legs = [tuple(info['talk'])]
        elif info is not None and str(di) in info.get('via', {}):
            legs = [tuple(info['via'][str(di)]), tuple(info['talk'])]
        else:
            # no clear line to the counter (HQ desks, pet shop): the spot nearest to the NPC
            spots = self.door.get('spots') or []
            if not spots or info is None:
                return self.__standSomewhere()
            nx, ny = info['pos'][0], info['pos'][1]
            legs = [tuple(min(spots, key=lambda s: (s[0] - nx) ** 2 + (s[1] - ny) ** 2))]
        self.npc = npc
        self.npcPos = tuple(info['pos']) if info is not None else legs[-1]
        self.task = task
        self.npcState = 'walking'
        self.__goto(legs)

    def __stay(self, now):
        bot = self.bot
        if bot.path:
            return True
        st = self.npcState
        if self.task in ('buy', 'talk') and st == 'walking':
            if self.d.realPlayersIn(self.intZone) or (self.npc.get('setMovie') or (0,))[0] != 0:
                self.task, self.npcState = 'stand', None        # someone got there first: wait about instead
                self.npcDue = now + random.uniform(3.0, 6.0)
                return True
            bot.faceTo(self.npcPos)
            bot.setAnim('neutral')
            bot.send('avatarEnter', [], doId=self.npc.doId, className=self.npc.className)
            LIFE.count('npc_' + self.task)
            self.npcState, self.npcDue = 'asked', now + 6.0
            return True
        if st == 'asked' and now > self.npcDue:
            self.npcState = 'done'                             # no answer (busy): give up
        elif st == 'shopping' and now >= self.npcDue:
            self.__buy()
            self.npcState, self.npcDue = 'paid', now + 6.0
        elif st == 'paid' and now > self.npcDue:
            self.npcState = 'done'
        elif st == 'choose' and now >= self.npcDue:
            field, args = self.npcAction
            bot.send(field, args, doId=self.npc.doId, className=self.npc.className)
            LIFE.count('npc_' + field)
            self.npcState, self.npcDue = 'asked', now + 8.0
        elif st == 'reading' and now >= self.npcDue:
            if (self.npc.get('setMovie') or (0, 0, 0))[2] == bot.avId:
                bot.send('setMovieDone', [], doId=self.npc.doId, className=self.npc.className)
            self.npcState = 'done'
        if self.task == 'treat':                           # HALLOWEEN: Trick-or-Treat
            from toontown.bots import halloween
            if self.npcState is None and now >= self.npcDue:
                self.treatMet = None
                halloween.trickOrTreat(bot, self.view, now)
                self.npcState, self.npcDue = 'treat', now + 10.0
            elif self.npcState == 'treat' and (self.treatMet is not None or now > self.npcDue):
                halloween.treated(bot, self.act.goal, self.treatMet)
                self.npcState = 'done'
            if self.npcState != 'done':
                return True
        if self.npcState == 'refused':
            self.npcState = 'done'
        if self.task == 'waitnpc' and now >= self.npcDue:
            if now >= self.until:
                return self.__leave()
            self.__plan(now)
            return True
        if self.npcState == 'done':
            self.npcState = None
            self.npc = None
            self.task = 'stand'
            self.npcDue = now + random.uniform(2.0, 5.0)
            if self.want is not None or self.wantMode == 'treat':
                self.until = min(self.until, now + random.uniform(3.0, 8.0))
            if random.random() < 0.3:
                say(bot, random.choice((500, 303, 309)), now)
            return True
        if self.task == 'stand' and now >= self.npcDue:
            self.npcDue = now + random.uniform(4.0, 10.0)
            if now >= self.until:
                return self.__leave()
            r = random.random()
            if r < 0.25:
                say(bot, random.choice(INSIDE.get(INTERIOR_OF.get(self.kind, 'toon'), INSIDE['toon'])), now)
            elif r < 0.35:
                emote(bot, now=now)
            elif r < 0.55:
                self.__standSomewhere()
            else:
                others = [b for b in self.d.bots.values() if b is not bot and b.zoneId == self.intZone]
                if others:
                    bot.faceTo(random.choice(others).pos)
        return True

    def __leave(self):
        pts = self.__back()
        self.__walkInside(pts)
        self.at = tuple(self.door['entry'])
        self.phase = 'leavewalk'
        self.label = 'leaving'
        return True

    def __buy(self):
        """The gag shop panel: fill the pouch like a player does (best gags first), pay 1 bean each."""
        bot = self.bot
        plan = planPurchase(bot)
        if plan is None:
            return
        blob, newMoney, n = plan
        bot.send('setInventory', [blob, newMoney, 1], doId=self.npc.doId, className='DistributedNPCClerk')
        self.sent = blob
        LIFE.count('buy_sent')
        LIFE.count('gags_bought', n)
        if n == 0:
            LIFE.count('buy_browse_only')

    # -- the end, whatever the reason -------------------------------------------------------------------
    def abort(self, why):
        bot = self.bot
        if self.npc is not None and self.npcState in ('shopping',):
            plan = planPurchase(bot, browse=True)
            if plan is not None:
                bot.send('setInventory', [plan[0], plan[1], 1], doId=self.npc.doId, className='DistributedNPCClerk')
        elif self.npc is not None and self.npcState == 'choose':
            bot.send('chooseQuest' if self.npcAction[0] == 'chooseQuest' else 'chooseTrack',
                     [0] if self.npcAction[0] == 'chooseQuest' else [self.npcAction[1][0]],
                     doId=self.npc.doId, className=self.npc.className)
        elif self.npc is not None and self.npcState == 'reading':
            bot.send('setMovieDone', [], doId=self.npc.doId, className=self.npc.className)
        if self.inside and why not in ('logout', 'district down', 'deleted by the server', 'activation timeout') \
                and bot.state == 'present':
            if self.intDoId is not None:
                bot.send('requestEnter', [], doId=self.intDoId, className='DistributedDoor')
            self.__comeOut()
        self.__closeView()
        self.act.interruptible = True


class _Shopper:
    def __init__(self, exp, maxCarry, tracks, access):
        self.experience = exp
        self.maxCarry = maxCarry
        self.trackArray = tracks
        self.access = access
        self.doId = 0
        self.DISLid = 0

    def getGameAccess(self):
        return self.access

    def hasTrackAccess(self, track):
        return bool(self.trackArray[track])

    def getMaxCarry(self):
        return self.maxCarry


def planPurchase(bot, browse=False):
    """(inventory blob, new money, gags bought) that validatePurchase accepts, from the owner view of
    the toon (the same numbers the client's gag shop panel shows)."""
    from toontown.toon.Experience import Experience
    from toontown.toon.InventoryBase import InventoryBase
    from toontown.toonbase import ToontownBattleGlobals as TBG
    if (getattr(bot, 'p8bLoadout', False) or getattr(bot, 'fillUp', False)) and not browse:
        # P8b: a Cog HQ regular fills up top gags first; (09-25) so does a bot on a restock trip
        bot.fillUp = False
        from toontown.bots.coghq import loadout
        return loadout.planPurchase(bot)
    f = bot.ownFields
    try:
        invBlob = f['setInventory'][0]
        money = f['setMoney'][0]
        exp = Experience(f['setExperience'][0])
        maxCarry = f['setMaxCarry'][0]
        tracks = list(f['setTrackAccess'][0])
    except (KeyError, IndexError, TypeError):
        return None
    access = (f.get('setAccess') or (OTPGlobals.AccessFull,))[0]
    toon = _Shopper(exp, maxCarry, tracks, access)
    inv = InventoryBase(toon, invBlob)
    toon.inventory = inv
    n = 0
    need = getattr(bot, 'mustBuy', None)
    if need and not browse:
        # PROGRESSION: a Deliver Gag task's gags first (the task needs them in the pouch)
        t, lvl, num = need
        while inv.numItem(t, lvl) < num and n < money and inv.addItem(t, lvl) > 0:
            n += 1
    if not browse:
        budget = min(money, n + random.randint(8, 60))
        mine = [t for t in range(len(tracks)) if tracks[t]]
        fav = random.sample(mine, min(len(mine), random.randint(1, 3))) if mine else []
        stuck = 0
        while n < budget and stuck < 30 and fav:
            t = random.choice(fav if random.random() < 0.8 else mine)
            top = min(exp.getExpLevel(t), TBG.LAST_REGULAR_GAG_LEVEL)
            done = False
            for lvl in range(top, -1, -1):
                if random.random() < 0.25 and lvl > 0:
                    continue          # not always the very best one
                if inv.addItem(t, lvl) > 0:
                    n += 1
                    done = True
                    break
            stuck = 0 if done else stuck + 1
    newMoney = money - n
    check = InventoryBase(toon, invBlob)
    if not check.validatePurchase(inv.inventory, money, newMoney):
        LIFE.count('buy_skipped_invalid')
        return None
    return inv.makeNetString(), newMoney, n


# ---- Cogs on a street -----------------------------------------------------------------------------------
class SuitWatch:
    def __init__(self):
        self.stores = {}
        self.cache = {}          # area id -> (time, [(x, y, r)])
        self.legs = {}           # suit doId -> (endpoints, legList)
        self.last = {}           # suit doId -> Point3

    def __store(self, area):
        s = self.stores.get(area.id)
        if s is None:
            from panda3d.toontown import DNAStorage, loadDNAFileAI
            from toontown.toonbase import ToontownGlobals
            hood = area.hood
            fname = 'phase_%s/dna/%s_%d.dna' % (ToontownGlobals.streetPhaseMap[hood], ToontownGlobals.dnaMap[hood],
                                                area.id)
            st = DNAStorage()
            try:
                loadDNAFileAI(st, fname)
                st.discoverContinuity()
                idx = {}
                for i in range(st.getNumSuitPoints()):
                    p = st.getSuitPointAtIndex(i)
                    idx[p.getIndex()] = p
                s = (st, idx)
            except Exception:
                import traceback
                notify.warning('[TTBOTS] suit DNA %s: %s' % (fname, traceback.format_exc()))
                s = (None, {})
            self.stores[area.id] = s
        return s

    def __suitPos(self, area, o):
        ep = o.get('setPathEndpoints')
        pp = o.get('setPathPosition')
        if not ep or not pp:
            return self.last.get(o.doId)
        st, idx = self.__store(area)
        if st is None:
            return None
        key = tuple(ep)
        cached = self.legs.get(o.doId)
        if cached is None or cached[0] != key:
            from panda3d.toontown import SuitLegList
            from toontown.suit import SuitTimings
            from toontown.toonbase import ToontownGlobals
            a, b = idx.get(ep[0]), idx.get(ep[1])
            path = st.getSuitPath(a, b, ep[2], ep[3]) if a is not None and b is not None and ep[3] else None
            ll = SuitLegList(path, st, ToontownGlobals.SuitWalkSpeed, SuitTimings.fromSky, SuitTimings.toSky,
                             SuitTimings.fromSuitBuilding, SuitTimings.toSuitBuilding,
                             SuitTimings.toToonBuilding) if path is not None else None
            cached = (key, ll)
            self.legs[o.doId] = cached
        ll = cached[1]
        if ll is None:
            return self.last.get(o.doId)
        state = (o.get('setPathState') or (1,))[0]
        if state != 1 and o.doId in self.last:
            return self.last[o.doId]
        try:
            start = globalClockDelta.networkToLocalTime(pp[1]) - ll.getStartTime(pp[0])
            elapsed = globalClock.getFrameTime() - start
            i = ll.getLegIndexAtTime(elapsed, 0)
            leg = ll.getLeg(i)
            p = leg.getPosAtTime(elapsed - leg.getStartTime())
        except Exception:
            return self.last.get(o.doId)
        pos = Point3(p[0], p[1], p[2])
        self.last[o.doId] = pos
        return pos

    def hazards(self, director, area):
        """[(x, y, radius)] of the Cogs (and street battles) on this street right now."""
        now = globalClock.getRealTime()
        c = self.cache.get(area.id)
        if c is not None and now - c[0] < 0.5:
            return c[1]
        out = []
        for z in area.zones:
            v = director.viewOf(z)
            if v is None:
                continue
            for o in v.objects.values():
                cls = o.className
                if cls == 'DistributedSuit':
                    p = self.__suitPos(area, o)
                    if p is not None:
                        out.append((p[0], p[1], 11.0))
                elif cls == 'DistributedBattle':
                    p = o.get('setPosition')
                    if p:
                        out.append((p[0], p[1], 20.0))
        self.cache[area.id] = (now, out)
        return out

    def nearest(self, director, area, pos):
        hz = [h for h in self.hazards(director, area) if h[2] < 15]
        if not hz:
            return None
        return min(math.hypot(h[0] - pos[0], h[1] - pos[1]) for h in hz)

    def blocked(self, director, area, bot, ahead=16.0):
        """A Cog (or a battle) at the bot or on its next few feet of path."""
        hz = self.hazards(director, area)
        if not hz:
            return None
        pts = [bot.pos]
        dist = 0.0
        prev = bot.pos
        for x, y, z, k in bot.path[:8]:
            dist += math.hypot(x - prev[0], y - prev[1])
            pts.append((x, y))
            prev = (x, y)
            if dist > ahead:
                break
        for hx, hy, r in hz:
            for p in pts:
                if (p[0] - hx) ** 2 + (p[1] - hy) ** 2 < r * r:
                    return (hx, hy, r)
        return None


SUITS = SuitWatch()
