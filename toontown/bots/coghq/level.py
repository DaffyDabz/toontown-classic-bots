"""P8b LEVEL RUNS: a Sellbot factory, a Cashbot mint, a Lawbot DA office, a Bossbot CGC, inside.

What the client does, and so what a bot does (no AI handshake exists once inside a level):
  - set its location to the facility zone and open interest in every zone the level lists
    (DistributedLevel.setZoneIds): the Cogs and the battles live in those zones.
  - a Cog battle starts by walking into a Cog: <Level>Suit.requestBattle(x, y, z, h, p, r); if the
    cell already has a battle, the others join it (toonRequestJoin). Every battle is the shared brain
    (battlebrain.py, kind 'level'); the foreman / supervisor / clerk / president battle ends in its
    <Facility>Reward, acked with rewardDone like the client.
  - the level's Cogs, their battle cells and the boss cell come from the same spec modules the
    client loads (FactorySpecs, MintRoomSpecs, StageRoomSpecs, CountryClubRoomSpecs), by setCogId.
  - DA office / CGC floors end at a floor elevator (DistributedElevatorFloor / DistributedClubElevator:
    requestBoard); the next floor comes as set<Stage|CountryClub>Zone(zone) to the avatar.
  - the win is the level / final room's setDefeated: every client teleports to the HQ courtyard.
  - CGC games, reported as a client would: the mole field (setClientTriggered, then whackedMole for
    moles that really pop, from the shared MoleFieldBase schedule, at a kid's reaction time and hit
    rate, never a bomb on purpose), the golf green (requestJoin, requestBoard(0), each board reported
    solved after a believable solve time, requestBoard(1)), the maze (setClientTriggered, then
    setFinishedMaze after a believable walk).
A run with a REAL player in it: the bots never start a battle and never lead; they walk his trail
(the positions he broadcast, a few feet behind) and join every battle he is in, and board the floor
elevator when he does (or when every room is done).
"""
import math
import random

from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots import battlebrain as bb
from toontown.bots.coghq import common as cm

QUIET = OTPGlobals.QuietZone
LEVEL_CLASSES = {
    'DistributedFactory': ('toontown.coghq.FactorySpecs', 'setFactoryId', 'factory'),
    'DistributedMintRoom': ('toontown.coghq.MintRoomSpecs', 'setRoomId', 'room'),
    'DistributedStageRoom': ('toontown.coghq.StageRoomSpecs', 'setRoomId', 'room'),
    'DistributedCountryClubRoom': ('toontown.coghq.CountryClubRoomSpecs', 'setRoomId', 'room'),
}
FLOOR_OBJS = ('DistributedStage', 'DistributedCountryClub', 'DistributedMint')
FLOOR_ELEVATORS = ('DistributedElevatorFloor', 'DistributedClubElevator', 'DistributedLawOfficeElevatorInt')
GAME_CLASSES = ('DistributedMoleField', 'DistributedGolfGreenGame', 'DistributedMaze')
NEXT_FLOOR_FIELDS = ('setStageZone', 'setCountryClubZone')


def isSuit(o):
    return o.get('setCogId') is not None and o.get('setLevelDoId') is not None


class Level:
    """One DistributedLevel (a factory, or one room of a mint / DA office floor / CGC floor)."""

    def __init__(self, obj):
        self.obj = obj
        self.doId = obj.doId
        mod, idField, self.kind = LEVEL_CLASSES[obj.className]
        self.specId = (obj.get(idField) or (0,))[0]
        self.roomNum = (obj.get('setRoomNum') or (0,))[0]
        self.cogData, self.reserveData, self.cells = [], [], {}
        self.entities = {}
        try:
            import importlib
            m = importlib.import_module(mod)
            if self.kind == 'factory':
                cogs = m.getCogSpecModule(self.specId)
            else:
                cogs = m.getCogSpecModule(self.specId)
            self.cogData = list(getattr(cogs, 'CogData', []))
            self.reserveData = list(getattr(cogs, 'ReserveCogData', []))
            self.cells = dict(getattr(cogs, 'BattleCells', {}))
            if obj.className == 'DistributedCountryClubRoom':
                spec = m.getCountryClubRoomSpecModule(self.specId)
                self.entities = dict(getattr(spec, 'GlobalEntities', {}))
                for z in getattr(spec, 'ZoneEntities', {}).values():
                    self.entities.update(z)
        except Exception:
            import traceback
            bb.HUB.error('P8b level spec %s %s' % (obj.className, self.specId), traceback.format_exc())
        self.bossCells = set(c['battleCell'] for c in self.cogData if c.get('boss'))

    def zoneIds(self):
        return list((self.obj.get('setZoneIds') or ([],))[0])

    def cellOf(self, suit):
        cogId = suit.get('setCogId')[0]
        reserve = (suit.get('setReserve') or (0,))[0]
        table = self.reserveData if reserve else self.cogData
        if 0 <= cogId < len(table):
            return table[cogId].get('battleCell')
        return None

    def suitPos(self, suit):
        cogId = suit.get('setCogId')[0]
        reserve = (suit.get('setReserve') or (0,))[0]
        table = self.reserveData if reserve else self.cogData
        if 0 <= cogId < len(table):
            p = table[cogId].get('pos') or Point3(0, 0, 0)
            return Point3(p[0], p[1], p[2]), table[cogId].get('h', 0)
        return Point3(0, 0, 0), 0


class FacilityRun:
    """A group's trip through one facility (shared by its bots; a real player may be in it)."""

    def __init__(self, kind, director, log):
        self.kind = kind
        self.info = cm.FACILITIES[kind]
        self.director = director
        self.air = director.air
        self.log = log
        self.members = []              # bot avIds (in the run)
        self.players = set()           # real players who boarded with us
        self.zone = None
        self.views = {}
        self.levels = {}
        self.doneCells = set()
        self.target = None             # (levelDoId, cell)
        self.targetAt = 0.0            # when the group has walked there
        self.requested = 0.0           # when the leader sent requestBattle
        self.state = 'boarding'
        self.floor = 0
        self.floors = []
        self.games = {}                # entity doId -> game driver
        self.lastTick = 0.0
        self.won = False
        self.failed = None
        self.trail = []                # the real player's recent positions (x, y, z, h, t)
        self.skip = set()              # cells a real run skips (none by default)
        self.playerGone = {}           # real player avId -> when he was last missing from the zone

    # -- zones ------------------------------------------------------------------------------------------
    def enterZone(self, zone):
        if zone == self.zone:
            return
        self.closeViews()
        self.zone = zone
        self.floor += 1
        self.floors.append(zone)
        self.levels = {}
        self.target = None
        self.games = {}
        self.openView(zone)
        self.state = 'inside'
        self.floorT0 = globalClock.getRealTime()
        if self.log.tInside is None:
            import time
            self.log.tInside = time.time()
        self.log.note('floor %d: zone %s' % (self.floor, zone))

    def openView(self, zone):
        if zone not in self.views and self.air.districtId:
            self.views[zone] = self.air.openView(self.air.districtId, zone)

    def closeViews(self):
        for v in self.views.values():
            self.air.closeView(v)
        self.views = {}

    def objects(self):
        for v in list(self.views.values()):
            for o in list(v.objects.values()):
                yield o

    def find(self, doId):
        for v in self.views.values():
            o = v.objects.get(doId)
            if o is not None:
                return o
        return None

    # -- the shared tick (any member calls it; runs once a second) ----------------------------------------
    def tick(self, now):
        if now - self.lastTick < 0.9 or self.state not in ('inside', 'floorEnd'):
            return
        self.lastTick = now
        # levels and their zones
        for o in list(self.objects()):
            if o.className in LEVEL_CLASSES and o.doId not in self.levels:
                self.levels[o.doId] = Level(o)
            if o.className in LEVEL_CLASSES:
                for z in (o.get('setZoneIds') or ([],))[0]:
                    self.openView(z)
            elif o.className in GAME_CLASSES and o.doId not in self.games:
                self.games[o.doId] = Game(self, o)
        for doId, lv in list(self.levels.items()):
            if self.find(doId) is None:
                del self.levels[doId]
        self.__trackPlayers(now)
        if self.won:
            return
        self.__pickTarget(now)
        for g in list(self.games.values()):
            g.tick(now)

    def __trackPlayers(self, now):
        v = self.views.get(self.zone)
        if v is None:
            return
        for avId in list(self.players):
            o = v.objects.get(avId)
            if o is None:
                # he left (teleported out, went sad, logged off): the group carries on by itself
                gone = self.playerGone.setdefault(avId, now)
                if now - gone > 30.0:
                    self.players.discard(avId)
                    self.log.note('real player %s left the facility: the bots carry on' % avId)
                continue
            self.playerGone.pop(avId, None)
            p = (o.pos[0], o.pos[1], o.pos[2], o.h, now)
            if not self.trail or math.hypot(p[0] - self.trail[-1][0], p[1] - self.trail[-1][1]) > 3.0:
                self.trail = (self.trail + [p])[-60:]

    # -- cells ------------------------------------------------------------------------------------------------
    def suits(self):
        """{(levelDoId, cell): [suitObj]} for every Cog still standing."""
        out = {}
        for o in self.objects():
            if not isSuit(o):
                continue
            lv = self.levels.get(o.get('setLevelDoId')[0])
            if lv is None:
                continue
            active = (lv.obj.get('setSuits') or ([], []))[0]
            if o.doId not in active:
                continue
            cell = lv.cellOf(o)
            if cell is None:
                continue
            out.setdefault((lv.doId, cell), []).append(o)
        return out

    def battles(self):
        return [o for o in self.objects() if bb.isBattle(o)]

    def battleFor(self, key, suitsByCell=None):
        suitsByCell = suitsByCell if suitsByCell is not None else self.suits()
        ids = set(s.doId for s in suitsByCell.get(key, []))
        for b in self.battles():
            m = b.get('setMembers')
            if m and ids & set(m[0]):
                return b
        return None

    def order(self, keys):
        def k(key):
            lv = self.levels.get(key[0])
            boss = lv is not None and key[1] in lv.bossCells
            return (lv.roomNum if lv else 99, boss, key[1])
        return sorted(keys, key=k)

    def roomBlocked(self, lv):
        """CGC: a room's door stays shut until the room before it is beaten (setBlockedRooms)."""
        club = next((o for o in self.objects() if o.className == 'DistributedCountryClub'), None)
        if club is None or lv.kind != 'room':
            return False
        rooms = list((club.get('setRoomDoIds') or ([],))[0])
        blocked = list((club.get('setBlockedRooms') or ([],))[0])
        if lv.doId not in rooms:
            return False
        i = rooms.index(lv.doId)
        return any(j in blocked for j in range(i))

    def roomBeaten(self, lv):
        """CGC: the room's own index left setBlockedRooms (its challenge / battle is beaten)."""
        club = next((o for o in self.objects() if o.className == 'DistributedCountryClub'), None)
        if club is None:
            return False
        rooms = list((club.get('setRoomDoIds') or ([],))[0])
        blocked = list((club.get('setBlockedRooms') or ([],))[0])
        return lv.doId in rooms and rooms.index(lv.doId) not in blocked

    def __pickTarget(self, now):
        by = self.suits()
        live = [k for k in by if k not in self.doneCells and k not in self.skip]
        if self.target is not None and self.target not in by:
            if self.battleFor(self.target, by) is None:
                self.doneCells.add(self.target)
                self.log.count('cells')
                self.log.note('cell %s done' % (self.target,))
                self.target = None
        if self.target is None and live:
            # rooms in order: a room's game comes before its Cogs (the maze leads to them), a room with a
            # game and no Cogs is played on the way, and a room behind a shut door waits
            for lv in sorted(self.levels.values(), key=lambda l: l.roomNum):
                if self.roomBlocked(lv):
                    break
                g = self.gameIn(lv)
                if g is not None and not g.done:
                    g.want = True
                    return
                if any(k[0] == lv.doId for k in live):
                    break
            for key in self.order(live):
                lv = self.levels.get(key[0])
                if lv is None or self.roomBlocked(lv):
                    continue
                self.target = key
                walk = 8.0 + random.uniform(0, 14.0)
                self.targetAt = now + walk
                self.requested = 0.0
                self.log.note('next cell %s (boss %s), walk %.0f s' % (key, key[1] in lv.bossCells, walk))
                return
        if self.target is None and not live:
            # nothing left to fight: games still to play, a floor elevator, or the end
            for lv in sorted(self.levels.values(), key=lambda l: l.roomNum):
                g = self.gameIn(lv)
                if g is not None and not g.done and not self.roomBlocked(lv):
                    g.want = True
                    return
            # a floor is over once its Cogs have been fought (or it never had any, well after the views filled)
            mine = [k for k in self.doneCells if k[0] in self.levels]
            if self.levels and self.state == 'inside' and (mine or now - self.floorT0 > 40.0) and \
                    now - self.floorT0 > 10.0:
                self.state = 'floorEnd'
                self.log.note('floor %d cleared (%d cells)' % (self.floor, len(mine)))

    def gameIn(self, lv):
        for g in self.games.values():
            if g.levelDoId == lv.doId:
                return g
        return None

    def floorElevator(self):
        for o in self.objects():
            if o.className in FLOOR_ELEVATORS:
                return o
        return None

    def defeated(self):
        for lv in self.levels.values():
            if lv.obj.get('setDefeated') is not None:
                return True
        return False

    def end(self):
        self.closeViews()
        for g in self.games.values():
            g.stop()
        self.games = {}


# ---- CGC games -------------------------------------------------------------------------------------------
class Game:
    """One CGC game entity, played by the run's bots the way clients report it."""

    def __init__(self, run, obj):
        self.run = run
        self.obj = obj
        self.doId = obj.doId
        self.levelDoId = (obj.get('setLevelDoId') or (0,))[0]
        self.entId = (obj.get('setEntId') or (0,))[0]
        self.cls = obj.className
        self.want = False              # the run has reached its room
        self.done = False
        self.started = False
        self.schedule = []
        self.t0 = None
        self.next = 0
        self.whacked = 0
        self.target = 0
        self.tasks = []
        self.boards = {}               # avId -> solve-until time
        self.joined = set()
        bb.HUB.watch(self.doId, self.onField)

    def stop(self):
        bb.HUB.unwatch(self.doId, self.onField)
        self.done = True

    def bots(self):
        d = self.run.director
        return [d.bots[a] for a in self.run.members if a in d.bots and d.bots[a].state == 'present'
                and d.bots[a].zoneId == self.run.zone]

    def onField(self, obj, fieldName, args):
        if fieldName == '__exit__':
            self.done = True
        elif fieldName == 'setGameStart':
            self.__gameStart(args)
        elif fieldName == 'setScore':
            self.whacked = args[0]
        elif fieldName in ('signalDone', 'setGameOver', 'setPityWin'):
            self.done = True
            self.run.log.note('%s %s over (%s %s)' % (self.cls, self.doId, fieldName, args))
            self.run.log.count('games')
        elif fieldName == 'toonFinished' and args[2]:
            self.done = True
            self.run.log.count('games')

    def tick(self, now):
        if self.done or not self.want:
            return
        lv = self.run.levels.get(self.levelDoId)
        if self.started and self.cls == 'DistributedMoleField' and lv is not None and self.run.roomBeaten(lv):
            self.done = True
            self.run.log.note('mole field %s beaten (%d whacked of %d)' % (self.doId, self.whacked, self.target))
            self.run.log.count('games')
            return
        bots = self.bots()
        if not bots:
            return
        if not self.started:
            self.started = True
            b = random.choice(bots)
            self.run.log.note('%s %s: %s steps on the trigger' % (self.cls, self.doId, b.avId))
            if self.cls == 'DistributedGolfGreenGame':
                for b in bots:
                    self.__later(random.uniform(1.0, 4.0), self.__golfJoin, b)
            else:
                b.send('setClientTriggered', [], doId=self.doId, className=self.cls)
                if self.cls == 'DistributedMaze':
                    self.t0 = now
            return
        if self.cls == 'DistributedMoleField':
            self.__moles(now, bots)
        elif self.cls == 'DistributedGolfGreenGame':
            self.__golf(now, bots)
        elif self.cls == 'DistributedMaze':
            self.__maze(now, bots)

    def __later(self, delay, fn, *args):
        def run(task):
            if not self.done:
                try:
                    fn(*args)
                except Exception:
                    import traceback
                    bb.HUB.error('P8b game %s' % fn.__name__, traceback.format_exc())
            return task.done
        taskMgr.doMethodLater(delay, run, 'botgame-%d-%d' % (self.doId, random.randint(0, 1 << 30)))

    # -- mole field: whack the moles that pop, as the client's own schedule has them --------------------------
    def __gameStart(self, args):
        if self.cls == 'DistributedMoleField':
            from direct.distributed.ClockDelta import globalClockDelta
            timestamp, self.target, total = args
            self.t0 = globalClockDelta.networkToLocalTime(timestamp)
            ent = self.run.levels.get(self.levelDoId)
            spec = ent.entities.get(self.entId, {}) if ent is not None else {}
            n = spec.get('numSquaresX', 6) * spec.get('numSquaresY', 6)
            self.schedule = moleSchedule(self.entId, self.levelDoId, n, total)
            self.next = 0
            self.run.log.note('mole field %s: %d moles to whack in %d s (%d popups)' % (
                self.doId, self.target, total, len(self.schedule)))
        elif self.cls == 'DistributedMaze':
            self.t0 = globalClock.getRealTime()

    def __moles(self, now, bots):
        if self.t0 is None:
            return
        t = globalClock.getFrameTime() - self.t0
        while self.next < len(self.schedule) and self.schedule[self.next][0] <= t - 0.4:
            start, idx, up, stay, down, kind, popupNum = self.schedule[self.next]
            self.next += 1
            if kind != 0:                  # a bomb: a kid leaves it alone (now and then one gets whacked)
                if random.random() < 0.05:
                    b = random.choice(bots)
                    self.__later(up + random.uniform(0.3, 1.2), self.__whackBomb, b, idx, popupNum)
                continue
            # a kid near that hill sees it and whacks it before it goes down, most of the time
            if random.random() < 0.78:
                b = random.choice(bots)
                react = up + random.uniform(0.4, min(stay, 2.2))
                self.__later(max(0.0, start + react - t), self.__whack, b, idx, popupNum)

    def __whack(self, bot, idx, popupNum):
        if bot.state == 'present':
            bot.send('whackedMole', [idx, popupNum], doId=self.doId, className=self.cls)
            self.run.log.count('moles')

    def __whackBomb(self, bot, idx, popupNum):
        from direct.distributed.ClockDelta import globalClockDelta
        if bot.state == 'present':
            bot.send('whackedBomb', [idx, popupNum, globalClockDelta.getRealNetworkTime()], doId=self.doId,
                     className=self.cls)

    # -- golf green: each kid solves the boards it is given, at a kid's pace ---------------------------------
    def __golfJoin(self, bot):
        if bot.state != 'present':
            return
        bot.send('requestJoin', [], doId=self.doId, className=self.cls)
        self.joined.add(bot.avId)
        self.__later(random.uniform(1.5, 3.0), self.__golfAsk, bot, 0)

    def __golfAsk(self, bot, verify):
        if bot.state != 'present' or self.done:
            return
        bot.send('requestBoard', [verify], doId=self.doId, className=self.cls)
        self.boards[bot.avId] = globalClock.getRealTime() + random.uniform(24.0, 48.0)
        if verify:
            self.run.log.count('boards')

    def onDirect(self, bot, fieldName, args):
        if fieldName == 'startBoard':
            self.boards[bot.avId] = globalClock.getRealTime() + random.uniform(24.0, 48.0)
        elif fieldName == 'boardCleared':
            self.boards[bot.avId] = globalClock.getRealTime() + random.uniform(1.0, 3.0)

    def __golf(self, now, bots):
        for b in bots:
            until = self.boards.get(b.avId)
            if until is not None and now >= until:
                del self.boards[b.avId]
                self.__golfAsk(b, 1 if random.random() < 0.85 else 0)

    # -- maze: walk it, finish at a kid's time -------------------------------------------------------------------
    def __maze(self, now, bots):
        if self.t0 is None or getattr(self, 'mazeTimes', None) is None and not bots:
            return
        if getattr(self, 'mazeTimes', None) is None:
            ent = self.run.levels.get(self.levelDoId)
            spec = ent.entities.get(self.entId, {}) if ent is not None else {}
            dur = 35.0 + spec.get('numSections', 1) * 15.0
            self.mazeTimes = dict((b.avId, self.t0 + dur * random.uniform(0.35, 0.8)) for b in bots)
        for b in bots:
            t = self.mazeTimes.get(b.avId)
            if t is not None and now >= t:
                del self.mazeTimes[b.avId]
                b.send('setFinishedMaze', [], doId=self.doId, className=self.cls)
                self.run.log.count('maze_finished')
        if not self.mazeTimes:
            self.done = True
            self.run.log.count('games')


def moleSchedule(entId, levelDoId, numMoles, duration):
    """MoleFieldBase.scheduleMoles (the client's and the AI's shared schedule) + each hill's popup count."""
    from toontown.coghq.MoleFieldBase import MoleFieldBase

    class _F(MoleFieldBase):
        pass
    f = _F()
    f.entId = entId

    class _L:
        doId = levelDoId
    f.level = _L()
    f.numMoles = numMoles
    f.GameDuration = duration
    f.notify = bb.notify
    f.scheduleMoles()
    counts = {}
    out = []
    for start, idx, up, stay, down, kind in f.schedule:
        counts[idx] = counts.get(idx, 0) + 1
        out.append((start, idx, up, stay, down, kind, counts[idx]))
    return out
