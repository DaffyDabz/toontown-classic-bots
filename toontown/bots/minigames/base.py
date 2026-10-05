"""The shared part of every minigame bot brain (TTBOTS P6): the minigame FRAMEWORK a real client
runs (DistributedMinigame.py), plus the Brain base class the per-game modules extend.

GameSession = one minigame DO, as ONE bot's client sees it:
    generate      -> after the load time (1-3.5 s) setAvatarJoined         (JOIN barrier 50 s)
    rules screen  -> a kid reads / clicks Play after 3-11 s: setAvatarReady (READY barrier 66 s)
    setGameReady  -> brain.onGameReady()
    setGameStart  -> brain.onGameStart(localStartTime)
    broadcasts / fields addressed to the bot -> brain.onField / brain.onDirect
    setGameExit   -> brain.onGameExit(), then setAvatarExited 0.4-1.5 s later (the client's cleanup)
    setGameAbort  -> brain.onGameExit(); counted as an abort (the bot never sends requestExit)
A session never aborts a game: it has no path that sends requestExit to the minigame.

Brain = what the bot does DURING the game (per-game module). It gets:
    self.game (the session), self.bot, self.index (my index in the participant list), self.avIds,
    self.hood (the safezone the trolley is in), self.send(field, args) (to the minigame DO, as the bot),
    self.at(delay, func, *args) (a one-shot timer, cancelled when the game ends),
    self.every(period, func) (a repeating timer), self.gameTime() (s since setGameStart),
    self.moveTo(x, y, z, h) (the toon's own position broadcast, as the client's smooth node sends it).
Override: onGameReady, onGameStart, onField, onDirect, tick(now), onGameExit.
"""
import random
import traceback

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.ClockDelta import globalClockDelta
from panda3d.core import Point3

from toontown.hood import ZoneUtil

TICK = 0.1


def now():
    return globalClock.getRealTime()


class Brain:
    notify = DirectNotifyGlobal.directNotify.newCategory('BotMinigameBrain')
    name = 'minigame'
    moves = False                 # True: the brain moves the toon (position broadcasts at the bot's cadence)
    preStart = True               # False: onField only from setGameStart on (the brain sets up there)

    def __init__(self, game):
        self.game = game
        self.bot = game.bot
        self.avIds = game.avIds
        self.index = game.index
        self.hood = game.hood
        self.difficulty = game.difficulty
        self.doId = game.doId
        self.numPlayers = len(game.avIds)
        self.timers = []          # [when, func, args]
        self.startTime = None     # local real time of setGameStart
        self.done = False
        self.note = {}            # what the brain did (logged at the end)
        self.pose = None
        self.poseDirty = False
        self.poseMoving = False
        self.lastPose = 0.0

    # ---- helpers -------------------------------------------------------------------------------
    def send(self, fieldName, args):
        self.game.send(fieldName, args)

    def at(self, delay, func, *args):
        self.timers.append([now() + max(0.0, delay), func, args])

    def every(self, period, func, jitter=0.0):
        def again():
            if self.done:
                return
            func()
            self.at(period + random.uniform(-jitter, jitter), again)
        self.at(period, again)

    def gameTime(self):
        return now() - self.startTime if self.startTime is not None else 0.0

    def netTime(self, bits=16):
        return globalClockDelta.getRealNetworkTime(bits=bits)

    def moveTo(self, x, y, z, h=None, p=0.0, r=0.0):
        """Where my toon is now. The session sends it as the client's smooth node does
        (setSmPosHpr every 0.2 s while it changes, one setSmStop when it stops)."""
        bot = self.bot
        bot.pos = Point3(x, y, z)
        if h is not None:
            bot.h = h
        self.pose = (x, y, z, bot.h, p, r)
        self.poseDirty = True

    def placeAt(self, x, y, z, h=0.0, p=0.0, r=0.0):
        """Start of the game: d_clearSmoothing + sendCurrentPosition, as the client does."""
        self.moveTo(x, y, z, h, p, r)
        self.bot.send('clearSmoothing', [0])
        self.broadcastPose()

    def broadcastPose(self):
        x, y, z, h, p, r = self.pose
        self.bot.send('setSmPosHpr', [x, y, z, h, p, r, globalClockDelta.getRealNetworkTime(bits=16)])
        self.poseDirty = False
        self.poseMoving = True

    def walkToward(self, x, y, speed, dt, z=0.0):
        """Step toward (x, y) at speed ft/s for dt s; True when there."""
        import math
        px, py = self.bot.pos[0], self.bot.pos[1]
        dx, dy = x - px, y - py
        d = math.sqrt(dx * dx + dy * dy)
        if d < 0.05:
            return True
        s = min(d, speed * dt)
        h = math.degrees(math.atan2(-dx, dy))
        self.moveTo(px + dx / d * s, py + dy / d * s, z, h)
        return s >= d - 0.01

    def fieldOf(self, name, default=None):
        return self.game.obj.get(name, default)

    def view(self):
        return self.game.act.mgView

    def objectsOf(self, className):
        v = self.view()
        return v.ofClass(className) if v is not None else []

    def posOf(self, avId):
        """Where another toon in this game is now: a bot's own position (bots never see each other's
        broadcasts, they share this process), a real player's from his smooth-position broadcasts."""
        b = self.bot.director.bots.get(avId)
        if b is not None:
            return b.pos
        v = self.view()
        o = v.objects.get(avId) if v is not None else None
        return o.pos if o is not None else None

    def isBot(self, avId):
        return avId in self.bot.director.bots

    # ---- to override -------------------------------------------------------------------------
    def onGameReady(self):
        pass

    def onGameStart(self, t0):
        pass

    def onField(self, fieldName, args):
        pass

    def onDirect(self, fieldName, args):
        pass

    def onObject(self, obj, fieldName, args):
        """A broadcast from another object in the game zone (a treasure, a real player's toon)."""
        pass

    def tick(self, t):
        pass

    def onGameExit(self):
        pass

    # ---- run by the session --------------------------------------------------------------------
    def runTimers(self, t):
        due = [x for x in self.timers if x[0] <= t]
        if not due:
            return
        self.timers = [x for x in self.timers if x[0] > t]
        for when, func, args in due:
            if self.done:
                return
            func(*args)


class GameSession:
    """One bot in one minigame DO (see the module docstring)."""
    notify = DirectNotifyGlobal.directNotify.newCategory('BotMinigame')

    def __init__(self, act, obj, brainCls, record):
        self.act = act
        self.bot = act.bot
        self.air = act.air
        self.obj = obj
        self.doId = obj.doId
        self.className = obj.className
        self.avIds = list(obj.get('setParticipants', ([],))[0])
        self.index = self.avIds.index(self.bot.avId) if self.bot.avId in self.avIds else 0
        trolleyZone = obj.get('setTrolleyZone', (act.trolleyZone,))[0]
        from toontown.minigame import MinigameGlobals as MG
        dOver, zOver = obj.get('setDifficultyOverrides', (MG.NoDifficultyOverride, MG.NoTrolleyZoneOverride))
        self.hood = MG.getSafezoneId(zOver if zOver != MG.NoTrolleyZoneOverride else (trolleyZone or 2000))
        self.difficulty = MG.getDifficulty(self.hood)
        if dOver != MG.NoDifficultyOverride:
            self.difficulty = dOver / float(MG.DifficultyOverrideMult)
        self.record = record
        self.state = 'loading'
        self.joinAt = now() + random.uniform(1.0, 3.5)
        self.readyAt = self.joinAt + random.uniform(3.0, 11.0)
        self.exitAt = None
        self.ended = None           # 'exit' / 'abort'
        self.brain = brainCls(self) if brainCls else None
        self.task = taskMgr.doMethodLater(TICK, self.__tick, 'botmg-%d-%d' % (self.bot.avId, self.doId))

    def send(self, fieldName, args):
        self.bot.send(fieldName, args, doId=self.doId, className=self.className)

    def error(self, what):
        self.bot.director.error('minigame %s %s' % (self.className, what), traceback.format_exc())

    def __tick(self, task):
        if self.state == 'closed':
            return task.done
        t = now()
        try:
            if self.state == 'loading' and t >= self.joinAt:
                self.send('setAvatarJoined', [])
                self.state = 'rules'
            elif self.state == 'rules' and t >= self.readyAt:
                self.send('setAvatarReady', [])
                self.state = 'waitStart'
            if self.brain is not None and not self.brain.done:
                self.brain.runTimers(t)
                if self.state == 'play':
                    self.brain.tick(t)
                b = self.brain
                load = getattr(self.bot.director, 'load', None)     # P10b: nobody sees an all-bot game
                every = 0.2 if load is None or load.watched(self.bot) else 3.0
                if b.pose is not None and t - b.lastPose >= every:
                    if b.poseDirty:
                        b.lastPose = t
                        b.broadcastPose()
                    elif b.poseMoving:
                        b.lastPose = t
                        self.bot.send('setSmStop', [globalClockDelta.getRealNetworkTime(bits=16)])
                        b.poseMoving = False
            if self.exitAt is not None and t >= self.exitAt:
                self.exitAt = None
                self.send('setAvatarExited', [])
        except Exception:
            self.error('tick')
        return task.again

    def onField(self, fieldName, args):
        try:
            if fieldName == 'setGameReady':
                if self.brain:
                    self.brain.onGameReady()
            elif fieldName == 'setGameStart':
                if self.state in ('loading', 'rules'):
                    # the AI only starts once everyone is ready; our ready is already in
                    self.state = 'waitStart'
                self.state = 'play'
                start = globalClockDelta.networkToLocalTime(args[0], now=now())
                if self.brain:
                    self.brain.startTime = start
                    self.brain.onGameStart(start)
                self.record.setdefault('started', now())
            elif fieldName == 'setGameExit':
                self.finish('exit')
                self.exitAt = now() + random.uniform(0.4, 1.5)
            elif fieldName == 'setGameAbort':
                self.finish('abort')
            elif self.brain is not None and not self.brain.done and (self.brain.startTime is not None
                                                                     or self.brain.preStart):
                self.brain.onField(fieldName, args)
        except Exception:
            self.error('onField %s' % fieldName)

    def onDirect(self, fieldName, args):
        if self.brain is not None and not self.brain.done:
            try:
                self.brain.onDirect(fieldName, args)
            except Exception:
                self.error('onDirect %s' % fieldName)

    def onObject(self, obj, fieldName, args):
        if self.brain is not None and not self.brain.done:
            try:
                self.brain.onObject(obj, fieldName, args)
            except Exception:
                self.error('onObject %s.%s' % (obj.className, fieldName))

    def finish(self, how):
        if self.ended:
            return
        self.ended = how
        if self.brain is not None and not self.brain.done:
            try:
                self.brain.onGameExit()
            except Exception:
                self.error('onGameExit')
            self.brain.done = True
            self.brain.timers = []
            if self.brain.note:
                self.record.setdefault('notes', {})[self.bot.avId] = self.brain.note
        self.record['ended'] = now()
        if how == 'abort':
            self.record['aborted'] = self.state
        self.act.gameEnded(self, how)

    def close(self):
        """The bot leaves (or the DO is gone): stop the timers. Never sends anything."""
        if self.brain is not None:
            self.brain.done = True
            self.brain.timers = []
        if self.state != 'closed' and self.exitAt is not None:
            # the exit ack is owed: a client's cleanup sends it even while leaving
            try:
                self.send('setAvatarExited', [])
            except Exception:
                pass
        self.exitAt = None
        self.state = 'closed'
        taskMgr.remove(self.task)
