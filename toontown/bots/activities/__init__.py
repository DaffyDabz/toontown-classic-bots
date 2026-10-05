"""Bot ACTIVITIES: what a bot toon does while it stands in an area (TTBOTS P2 plug-in point).

Later phases (P4 playground life, P5 fishing, P6 trolley/minigames, P7 streets, P8 battles,
P9 commands) each add ONE module here. Adding one needs no edit to any shared file except
ONE line: its module name in MODULES below.

HOW TO ADD AN ACTIVITY
    # toontown/bots/activities/fishing.py
    from toontown.bots.activities import Activity, register

    @register
    class Fishing(Activity):
        name = 'fishing'
        weight = 2.0                     # how often a free bot picks it, relative to the others
        kinds = ('playground',)          # area kinds it runs in (None = any)

        @classmethod
        def canRun(cls, bot):            # cheap check: can this bot do it here, now?
            return bool(bot.area.wm.places('fishing'))

        def start(self):                 # claim what it needs, start walking
            place = ...
            if not self.claim(('fishing', bot.zoneId, place['name'])):
                return False             # False = could not start, the director picks again
            self.bot.walkTo(self.bot.area.placeNode(place))

        def step(self, now):             # every bot tick (~0.3 s moving, ~1 s idle)
            if self.bot.path: return True
            ...                          # talk to the pond like a client: self.bot.send(field, args, doId, cls)
            return True                  # False = done; the director picks the next one

        def stop(self, why):             # ALWAYS called once at the end (done, yielded, travel, logout)
            ...                          # leave the spot like a client would; claims are released for you

THE CONTRACT
  Activity(bot)               one instance per run; self.bot, self.director, self.air are set.
  canRun(bot) classmethod     True when it may start for this bot in bot.area (bot.zoneId).
  start() -> bool|None        False = cannot start (no free resource); anything else = running.
  step(now) -> bool           called by the ONE central tick; False ends it. Never block, never sleep:
                              keep state on self and return; use self.after(seconds) to wait.
  stop(why)                   the end of every run, whatever the reason. Claims are released after it.
  onHeard(speaker, msgId)     a REAL player said SpeedChat msgId nearby; return True if handled.
  onField(obj, field, args)   a broadcast in the bot's zone from the AI or a real player (not from bots),
                              e.g. fillSlot0 on the trolley; obj is a BotZoneView.ViewObject.
  onDirect(field, args)       something addressed to the bot's avatar (rejectBoard, setMovie, ...).
  interruptible = True        False = the director never pulls the bot away (a minigame, a battle).
  yieldTo(avId)               a real player wants this activity's resource; default: stop('yield').
  releaseFor(why) -> str      optional, for a NOT interruptible activity: a friend whispered an order
                              (P9 "Follow me"). End cleanly and return 'now' (leaving; the order starts
                              when the activity ends) or 'after' (finish this game first). Fishing: reel in
                              and step off; trolley: hop off a waiting car, else Exit after the game.
  trigger(bot, speaker, msgId) classmethod: start me because of something heard (e.g. "Follow me!");
                              return an instance or None (see follow.py).

RESOURCES (a fishing spot, a trolley seat, a kart): self.claim(key) -> bool, where key is any
hashable, e.g. ('trolley', zoneId, seat). One bot per key; claims drop when the activity stops.
Real players always win: before taking a real object, check self.realNear(pos, radius) and
director.realPlayersIn(zoneId); when a real toon walks up, call self.yieldTo(avId) or stop.

THE BOT (toontown/bots/BotToon.py) gives you:
  bot.area (BotWorld.Area: .wm walk map, .kind, .hood, .places via bot.area.wm.places(kind)),
  bot.zoneId, bot.pos, bot.h, bot.node, bot.path (non-empty while walking),
  bot.walkTo(node[, speed, anim]) -> bool, bot.walkToPos(x, y, z), bot.stopWalking(),
  bot.faceTo(point), bot.setAnim('neutral'|'walk'|'run'|...), bot.say(speedChatId), bot.emote(id),
  bot.send(field, args, doId=None, className='DistributedToon')  (sent AS the bot's client),
  bot.view (the shared BotZoneView of its zone: .objects, .first(className), .ofClass(className)).
Do NOT change bot.zoneId or send setLocation yourself: travel between areas is the director's.
"""
import importlib

from direct.directnotify import DirectNotifyGlobal

REGISTRY = []           # Activity classes, in load order
MODULES = [             # ONE line per activity module (toontown/bots/activities/<name>.py)
    'stroll',
    'follow',
    # reserved by the lead for the phases being built in parallel; a module whose file does not exist yet
    # is skipped quietly, so builders add their FILE and never edit this shared list.
    'playground',       # P4 playground life: idle, SpeedChat, emotes, shops/HQ/doors
    'fishing',          # P5
    'street',           # P7 street walking, building doors
    'trolley',          # P6 trolley + minigames (brains in toontown/bots/minigames/)
    'battle',           # P8 street battles
    'cogbuilding',      # P8 Cog buildings
    'coghq',            # P8 factories, mints, DA offices, CGC
    'boss',             # P8 VP / CFO / CJ / CEO
    'faclobby',         # sweep fix: bots wait at every facility elevator and board with a real toon
    'commands',         # P9 player commands (Follow me / Wait here / ToonTask help)
    'helpcall',         # 09-25 anyone says Help! in a street battle: the bots that can come run over
    'toontask',         # 09-30 PROGRESSION: a bot works its own ToonTasks (progress.py / taskplan.py)
    'home',             # 09-30 PROGRESSION W2: home to its estate for a Phone / Mailbox ToonTask (weight 0)
]
notify = DirectNotifyGlobal.directNotify.newCategory('BotActivities')


def register(cls):
    if cls not in REGISTRY:
        REGISTRY.append(cls)
    return cls


def loadAll():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for name in MODULES:
        if not (os.path.exists(os.path.join(here, name + '.py')) or os.path.isdir(os.path.join(here, name))):
            continue
        try:
            importlib.import_module('toontown.bots.activities.' + name)
        except Exception:
            import traceback
            notify.warning('[TTBOTS] activity module %s failed to load: %s' % (name, traceback.format_exc()))
    return REGISTRY


class Activity:
    name = 'activity'
    weight = 1.0
    kinds = None
    interruptible = True
    progress = False        # PROGRESSION (owner 1a): True = it levels a toon up (XP, beans, tasks); the director
                            # starts it only while a real player is online (director.playing)
    anchored = False        # PROGRESSION: True = the director never pulls the bot out of it (spare / over-max
                            # logout / rotate / recall): a toon waiting at a building door for help stays

    def __init__(self, bot):
        self.bot = bot
        self.director = bot.director
        self.air = bot.air
        self.claims = []
        self.wakeAt = 0.0
        self.stopped = False

    # ---- to override ---------------------------------------------------------
    @classmethod
    def canRun(cls, bot):
        return True

    @classmethod
    def trigger(cls, bot, speaker, msgId):
        return None

    def start(self):
        return True

    def step(self, now):
        return False

    def stop(self, why):
        pass

    def onHeard(self, speaker, msgId):
        return False

    def onField(self, obj, fieldName, args):
        pass

    def onDirect(self, fieldName, args):
        pass

    def yieldTo(self, avId):
        self.bot.endActivity('yield to %s' % avId)

    # ---- helpers ---------------------------------------------------------------
    @classmethod
    def runsIn(cls, bot):
        return (cls.kinds is None or bot.area.kind in cls.kinds) and cls.canRun(bot)

    def claim(self, key):
        if self.director.claim(key, self.bot):
            self.claims.append(key)
            return True
        return False

    def releaseAll(self):
        for key in self.claims:
            self.director.release(key, self.bot)
        self.claims = []

    def after(self, seconds):
        """Sleep: True once `seconds` have passed since the first call (call it every step)."""
        now = globalClock.getRealTime()
        if not self.wakeAt:
            self.wakeAt = now + seconds
        if now >= self.wakeAt:
            self.wakeAt = 0.0
            return True
        return False

    def realNear(self, pos, radius):
        return self.director.realPlayersNear(self.bot.zoneId, pos, radius)
