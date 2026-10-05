"""FISHING (TTBOTS P5): bots fish every pond (6 playgrounds + 17 streets, 4 spots each) the way a
player at the keyboard does, talking to the pond exactly like a client:

  walk up to a free spot -> FishingSpot.requestEnter()            (the spot's collision sphere)
  cast                    -> FishingSpot.doCast(power, heading)    (costs jellybeans, AI checks money)
  bob lands, the client checks the targets every 0.5 s (DistributedFishingPond.checkTargets):
                             a target within 2.5 ft of the bob    -> FishingPond.hitTarget(targetId)
  the AI rolls the catch (FishManagerAI.recordCatch) and plays PullInMovie (the reel everyone sees)
  a few minutes later     -> FishingSpot.requestExit()
  bucket full / broke     -> walk to the Fisherman, NPCFisherman.avatarEnter(), completeSale(1)

HONEST CATCHES: the bot never tells the pond it hit a target unless the client's own rule says so.
The bob's landing point is the client's ballistic (DistributedFishingSpot.moveBobTask: v0 = 25*power,
angle = 30*power deg, g = 32.2, from (0, 3, 8.5) in the spot's angle node, until z < the pond's water
level), and the targets move the client's way (DistributedFishingTarget.setState: an easeInOut lerp to
radius*cos/sin(angle) around the pond centre). The bot aims at the bubbles like a player (with a
player's error), then checks exactly when the client would (1.0 s after the splash, then every 0.5 s).
A cast that finds nothing is recast after a player's patience (well inside the AI's 45 s timeout).

RULES (TTBOTS-RECON 6b law): a bot never takes the last free spot of a pond (claims count as
taken), and when a real player is at a pond with no free spot (waiting beside it, or he just sat in
the last one) one bot gets up within a few seconds. Real players always win.
"""
import json
import math
import os
import random
import time

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.ClockDelta import globalClockDelta
from panda3d.core import NodePath, Point3

from toontown.bots.activities import Activity, register
from toontown.bots.BotToon import WALK_SPEED
from toontown.fishing import FishGlobals, FishingTargetGlobals

notify = DirectNotifyGlobal.directNotify.newCategory('BotFishing')

# client constants (DistributedFishingSpot / DistributedFishingPond / DistributedFishingTarget)
V_ZERO_MAX = 25.0
ANGLE_MAX = 30.0
BOB_START = (0.0, 3.0, 8.5)
GRAVITY = 32.2
TARGET_RADIUS = 2.5
POLL = 0.5
ANGLE_LIMIT = FishGlobals.FishingAngleMax
CAST_TIMEOUT = FishGlobals.CastTimeout
FISH_CODES = (FishGlobals.FishItem, FishGlobals.FishItemNewEntry, FishGlobals.FishItemNewRecord)
# NPCToons sell movie flags
SELL_START, SELL_COMPLETE, SELL_NOFISH, SELL_TROPHY, SELL_TIMEOUT = 1, 2, 3, 4, 8

NEAR_SPOT = 12.0        # a real toon this close to a spot is "at the pond"
SESSION = (420.0, 900.0)     # P10b: longer sessions (was 180-420 s): fewer trips, fewer database writes
COOLDOWN = (180.0, 600.0)    # P10b: a break away from the pond between sessions (was 60-240 s)

# npc_fisherman_origin_0 of every pond area (the client places the Fisherman there), from the DNA
# (extracted once with a separate script): area -> (x, y, z, h)
FISHERMEN = {
    2000: (-68.0, -2.0, -2.0, 27.0), 1000: (-12.5, 178.0, 3.3, 180.0), 5000: (67.1, 10.9, 0.0, 21.0),
    4000: (-58.2, -59.2, -14.5, -61.0), 3000: (-120.5, -35.0, 7.5, -70.0), 9000: (150.2, 33.7, -15.0, -171.0),
    2100: (11.4, -633.0, 0.0, -87.0), 2200: (-226.4, 143.2, 0.0, 94.0), 2300: (492.7, -78.6, 0.0, 5.0),
    1100: (350.0, -348.0, 0.0, -78.0), 1200: (-413.0, -252.0, 0.0, -35.0), 1300: (374.0, 120.0, 0.0, 130.0),
    5100: (148.7, 19.8, 0.0, -11.0), 5200: (201.0, 74.0, 0.0, 32.0), 5300: (134.5, -93.3, 0.0, 3.0),
    4100: (-613.6, -89.8, 0.0, -98.0), 4200: (-248.7, 249.0, 0.0, -98.0), 4300: (748.6, -14.4, 0.0, 100.0),
    3100: (488.0, 36.0, 0.1, 101.0), 3200: (360.0, 458.0, 0.0, 56.0), 3300: (22.8, 93.5, 0.0, -94.0),
    9100: (117.0, -149.8, 0.0, -159.0), 9200: (259.3, -353.0, 0.0, 24.0),
}

STATS = {'sessions': 0, 'enters': 0, 'rejected': 0, 'casts': 0, 'hits': 0, 'catches': 0, 'fish': 0, 'boots': 0,
         'beans': 0, 'questItems': 0, 'tankFull': 0, 'sales': 0, 'trophies': 0, 'saleBusy': 0, 'saleNoFish': 0,
         'yields': 0, 'kicked': 0, 'broke': 0, 'recasts': 0, 'fishSold': 0, 'tankGrewByAI': 0}
PONDSTATS = {}          # pond key -> sampled numbers
ACTIVE = {}             # pond key -> set of Fishing activities (walking to it, seated, leaving)
TRACK = {}              # target doId -> (setState args, start Point3, arrival time, duration)
_state = {'director': None, 'task': False, 'started': time.time(), 'lastWrite': 0.0, 'lastLog': 0.0,
          'yieldAt': {}}
_root = NodePath('fishing-math')


# ---- the pond, as the views see it ----------------------------------------------------------
def ponds(area):
    """[(key, zoneId, places)] for every pond of an area (walk-map 'fishing' places by pond)."""
    cache = getattr(area, '_fishPonds', None)
    if cache is None:
        groups = {}
        for p in area.wm.places('fishing'):
            ex = p.get('extra', {})
            zone = ex.get('visZone') or area.id
            groups.setdefault((ex.get('pond'), zone), []).append(p)
        cache = [((area.id, name, zone), zone, places) for (name, zone), places in sorted(groups.items())]
        area._fishPonds = cache
    return cache


def spotsOf(director, zoneId, places):
    """The DistributedFishingSpot objects of one pond (matched to the walk-map places by position)."""
    v = director.viewOf(zoneId)
    if v is None:
        return []
    out = []
    for s in v.ofClass('DistributedFishingSpot'):
        if s.get('setPosHpr') is None:
            continue
        for p in places:
            if (Point3(*p['pos']) - s.pos).lengthSquared() < 16.0:
                out.append(s)
                break
    return out


def occupant(spot):
    occ = spot.get('setOccupied')
    return occ[0] if occ else 0


def claimed(director, spot):
    holder = director.claims.get(('fishing', spot.doId))
    return holder is not None and holder.state != 'offline'


def pondState(director, zoneId, places):
    """(spots, available, realSeated, realNear, botsSeated): available = no one in it, no bot heading for it."""
    spots = spotsOf(director, zoneId, places)
    avail = [s for s in spots if occupant(s) == 0 and not claimed(director, s)]
    realSeated = [occupant(s) for s in spots if occupant(s) and occupant(s) not in director.bots]
    botsSeated = [occupant(s) for s in spots if occupant(s) in director.bots]
    near = []
    v = director.viewOf(zoneId)
    if v is not None and spots:
        for avId in director.realPlayersIn(zoneId):
            if avId in realSeated:
                continue
            o = v.objects.get(avId)
            if o is not None and min((o.pos - s.pos).length() for s in spots) <= NEAR_SPOT:
                near.append(avId)
    return spots, avail, realSeated, near, botsSeated


def waterLevel(pondObj):
    area = pondObj.get('setArea', (0,))[0] if pondObj is not None else 0
    return FishingTargetGlobals.getWaterLevel(area), area


def targetPos(obj, t, area):
    """Where a client draws this target at real time t (DistributedFishingTarget.setState)."""
    st = obj.get('setState')
    cx, cy, cz = FishingTargetGlobals.getTargetCenter(area)
    if not st:
        return Point3(obj.pos)
    angle, radius = st[1], st[2]
    dest = Point3(radius * math.cos(angle) + cx, radius * math.sin(angle) + cy, cz)
    rec = TRACK.get(obj.doId)
    if rec is None or rec[0] != tuple(st):
        return dest                 # not seen arriving: assume it has got there (it rests 1-5 s)
    start, t0, dur = rec[1], rec[2], rec[3]
    f = min(max((t - t0) / dur, 0.0), 1.0)
    f = f * f * (3.0 - 2.0 * f)     # easeInOut
    return start + (dest - start) * f


def landing(power, water):
    """The client's moveBobTask: flight time and distance along the angle node's Y."""
    v = power * V_ZERO_MAX
    a = math.radians(power * ANGLE_MAX)
    vz, vy = v * math.sin(a), v * math.cos(a)
    drop = BOB_START[2] - water
    t = (vz + math.sqrt(vz * vz + 2.0 * GRAVITY * max(drop, 0.0))) / GRAVITY
    return t, BOB_START[1] + vy * t


def spotNode(spot):
    x, y, z, h, p, r = spot.get('setPosHpr')
    np = _root.attachNewNode('spot')
    np.setPosHpr(x, y, z, h, p, r)
    return np


# ---- sampling, yielding and the status file (one task for the whole process) ------------------
def _ensureTask(director):
    if _state['director'] is None:
        _state['director'] = director
    if not _state['task']:
        _state['task'] = True
        taskMgr.doMethodLater(1.0, _sample, 'bots-fishing-sample')


def _sample(task):
    d = _state['director']
    try:
        if d is not None and d.world is not None and d.air.districtId:
            now = globalClock.getRealTime()
            for area in d.world.areas.values():
                for key, zone, places in ponds(area):
                    _samplePond(d, now, key, zone, places)
            wall = time.time()
            if wall - _state['lastWrite'] >= 10.0:
                _state['lastWrite'] = wall
                _writeStatus(d)
            if wall - _state['lastLog'] >= 60.0:
                _state['lastLog'] = wall
                notify.info('[TTBOTS] fishing: %s' % json.dumps(STATS, sort_keys=True))
    except Exception:
        import traceback
        if d is not None:
            d.error('fishing sample', traceback.format_exc())
    return task.again


def _samplePond(d, now, key, zone, places):
    spots, avail, realSeated, near, botsSeated = pondState(d, zone, places)
    if not spots:
        return
    ps = PONDSTATS.setdefault(key, {'samples': 0, 'botSamples': 0, 'maxBots': 0, 'sumBots': 0,
                                    'fullSamples': 0, 'fullBotsOnly': 0, 'realSeen': 0, 'yields': 0})
    ps['samples'] += 1
    nb = len(botsSeated)
    ps['sumBots'] += nb
    ps['maxBots'] = max(ps['maxBots'], nb)
    if nb:
        ps['botSamples'] += 1
    empty = sum(1 for s in spots if occupant(s) == 0)
    if empty == 0:
        ps['fullSamples'] += 1
        if not realSeated:
            ps['fullBotsOnly'] += 1
    if realSeated or near:
        ps['realSeen'] += 1
    # a real player at a pond with no spot left: one bot gets up (after a human reaction time)
    if not avail and (realSeated or near) and ACTIVE.get(key):
        due = _state['yieldAt'].get(key)
        if due is None:
            _state['yieldAt'][key] = now + random.uniform(1.5, 4.0)
        elif now >= due:
            del _state['yieldAt'][key]
            acts = [a for a in ACTIVE[key] if a.phase in ('toSpot', 'entering', 'seated')]
            if acts:
                acts.sort(key=lambda a: (a.phase == 'seated', a.seatedAt or 0.0))
                who = (near + realSeated)[0]
                ps['yields'] += 1
                acts[0].yieldTo(who)
    else:
        _state['yieldAt'].pop(key, None)


def _writeStatus(d):
    names = {}
    for a in d.world.areas.values():
        names[a.id] = a.name
    rows = []
    for key in sorted(PONDSTATS):
        ps = PONDSTATS[key]
        rows.append({'area': key[0], 'name': names.get(key[0]), 'zone': key[2], 'samples': ps['samples'],
                     'withBots': ps['botSamples'], 'maxBots': ps['maxBots'],
                     'avgBots': round(ps['sumBots'] / float(max(ps['samples'], 1)), 2),
                     'fullSamples': ps['fullSamples'], 'fullBotsOnly': ps['fullBotsOnly'],
                     'realSeen': ps['realSeen'], 'yields': ps['yields']})
    data = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'since': int(time.time() - _state['started']),
            'stats': STATS, 'ponds': rows,
            'fishingNow': sum(len(v) for v in ACTIVE.values())}
    lines = ['TTBOTS FISHING  %s  (%ds)  bots at ponds now %d' % (data['time'], data['since'], data['fishingNow']),
             ' '.join('%s=%s' % kv for kv in sorted(STATS.items())),
             '%-6s %-22s %6s %8s %7s %7s %6s %8s %6s %6s' % ('area', 'name', 'zone', 'samples', 'w/bots', 'maxBots',
                                                            'avg', 'full', 'fullB', 'yield')]
    for r in rows:
        lines.append('%-6s %-22s %6s %8d %7d %7d %6.2f %8d %6d %6d' % (
            r['area'], (r['name'] or '')[:22], r['zone'], r['samples'], r['withBots'], r['maxBots'], r['avgBots'],
            r['fullSamples'], r['fullBotsOnly'], r['yields']))
    try:
        os.makedirs(d.runDir, exist_ok=True)
        for name, text in (('fishing-status.txt', '\n'.join(lines) + '\n'), ('fishing-status.json', json.dumps(data))):
            tmp = os.path.join(d.runDir, name + '.new')
            with open(tmp, 'w') as f:
                f.write(text)
            os.replace(tmp, os.path.join(d.runDir, name))
    except Exception:
        import traceback
        d.error('fishing status', traceback.format_exc())


# ---- the activity ------------------------------------------------------------------------------
def _own(bot, field, default):
    v = bot.ownFields.get(field)
    return v[0] if v else default


@register
class Fishing(Activity):
    name = 'fishing'
    progress = True          # PROGRESSION (owner 1a): none starts while no real player is online
    weight = 3.0
    kinds = ('playground',)
    interruptible = False     # a player at a pond is not pulled away mid-cast; the session ends itself

    @classmethod
    def canRun(cls, bot):
        area = bot.area
        if area is None or bot.node is None or not area.wm.places('fishing'):
            return False
        d = bot.director
        _ensureTask(d)
        if globalClock.getRealTime() < getattr(bot, '_fishAgain', 0.0) or not d.clockSynced:
            return False
        if _own(bot, 'setMoney', 0) < FishGlobals.getCastCost(_own(bot, 'setFishingRod', 0)) \
                and not _own(bot, 'setFishTank', []):
            return False
        return cls.pickSpot(bot) is not None

    @classmethod
    def pickSpot(cls, bot):
        d, area = bot.director, bot.area
        for key, zone, places in ponds(area):
            spots, avail, realSeated, near, botsSeated = pondState(d, zone, places)
            if len(avail) < 2 or near:          # never the last free spot; never in front of a waiting player
                continue
            cands = []
            for s in avail:
                k = area.wm.nearestNode(s.pos[0], s.pos[1], s.pos[2])
                if k is not None and area._comp[k] == area._comp[bot.node]:
                    cands.append(s)
            if cands:
                return key, zone, places, random.choice(cands)
        return None

    def __init__(self, bot):
        Activity.__init__(self, bot)
        self.phase = None
        self.key = None
        self.spot = None
        self.spotId = 0
        self.pondId = 0
        self.zone = None
        self.seatedAt = 0.0
        self.until = 0.0
        self.sessionEnd = 0.0
        self.checks = None
        self.cast = None
        self.fast = False
        self.sellAfter = False
        self.tries = 0
        self.leaveWhy = ''
        self.yieldNow = False

    def log(self, text):
        notify.info('[TTBOTS] %s fishing: %s' % (self.bot.avId, text))

    # ---- life ----
    def start(self):
        bot = self.bot
        pick = self.pickSpot(bot)
        if pick is None:
            return False
        self.key, self.zone, places, spot = pick
        if not self.claim(('fishing', spot.doId)):
            return False
        self.spot, self.spotId = spot, spot.doId
        self.pondId = spot.get('setPondDoId', (0,))[0]
        ACTIVE.setdefault(self.key, set()).add(self)
        STATS['sessions'] += 1
        self.tank = len(_own(bot, 'setFishTank', []))
        self.maxTank = _own(bot, 'setMaxFishTank', 20)
        self.money = _own(bot, 'setMoney', 0)
        self.rod = _own(bot, 'setFishingRod', 0)
        if self.tank >= self.maxTank or (self.money < FishGlobals.getCastCost(self.rod) and self.tank):
            self.sellAfter = True
            self.releaseAll()
            return self.__leave('sell first', exitSpot=False)
        if not bot.walkToPos(spot.pos[0], spot.pos[1], spot.pos[2], WALK_SPEED):
            return False
        self.phase = 'toSpot'
        self.until = globalClock.getRealTime() + 90.0
        return True

    def stop(self, why):
        bot = self.bot
        if self.phase in ('entering', 'seated', 'leaving') and self.spot is not None and occupant(self.spot) == bot.avId:
            bot.send('requestExit', [], doId=self.spotId, className='DistributedFishingSpot')
        if bot.path and why not in ('travel', 'logout'):
            bot.stopWalking()
        s = ACTIVE.get(self.key)
        if s is not None:
            s.discard(self)
        bot._fishAgain = globalClock.getRealTime() + random.uniform(*COOLDOWN)
        self.fast = False

    def yieldTo(self, avId):
        STATS['yields'] += 1
        self.log('yields its spot at %s to real player %s (%s)' % (self.key, avId, self.phase))
        if self.phase in ('toSpot', 'entering'):
            self.bot.stopWalking()
            if self.phase == 'entering' and occupant(self.spot) == self.bot.avId:
                self.__leave('yield', exitSpot=True)
            elif self.phase == 'entering':
                self.yieldNow = True          # the AI may still seat it: get straight up again
            else:
                self.releaseAll()
                self.phase = 'done'
        else:
            self.__leave('yield', exitSpot=True)

    @property
    def holdsPose(self):
        """Sweep fix: seated in the spot (the AI says so): the bot sends no position, as a fishing client
        (BotToon.broadcastNow); every client has the toon parented to the spot."""
        return self.spot is not None and self.phase in ('entering', 'seated', 'leaving') \
            and occupant(self.spot) == self.bot.avId

    def releaseFor(self, why):
        """Sweep fix (P9 whisper): a friend whispered an order: reel in and step off now (no trip to the
        Fisherman); the order starts when this activity ends."""
        self.sellAfter = False
        self.log('leaves %s for a friend (%s, %s)' % (self.key, why, self.phase))
        if self.phase == 'toSpot':
            self.bot.stopWalking()
            self.releaseAll()
            self.phase = 'done'
        elif self.phase == 'entering':
            if occupant(self.spot) == self.bot.avId:
                self.__leave(why, exitSpot=True)
            else:
                self.yieldNow = True
        elif self.phase == 'seated':
            self.__leave(why, exitSpot=True)
        elif self.phase not in ('leaving', 'stepOff', 'done'):
            self.bot.endActivity(why)
        return 'now'

    # ---- messages ----
    def onField(self, obj, fieldName, args):
        if obj.doId == self.spotId:
            if fieldName == 'setOccupied':
                self.__occupied(args[0])
            elif fieldName == 'setMovie' and self.phase == 'seated' and args[0] == FishGlobals.PullInMovie:
                self.__pulledIn(args[1], args[2])
        elif fieldName == 'setState' and obj.className == 'DistributedFishingTarget':
            if TRACK.get(obj.doId, (None,))[0] != tuple(args):
                TRACK[obj.doId] = (tuple(args), Point3(obj.pos), globalClock.getRealTime(), max(args[3], 0.05))
        elif fieldName == 'setMovie' and obj.className == 'DistributedNPCFisherman' and len(args) > 2 \
                and args[2] == self.bot.avId and self.phase == 'selling':
            self.__saleMovie(args[0])

    def onDirect(self, fieldName, args):
        if fieldName == 'rejectEnter' and self.phase == 'entering':
            STATS['rejected'] += 1
            self.log('rejected at spot %s' % self.spotId)
            self.releaseAll()
            self.phase = 'done'
        elif fieldName == 'freeAvatar' and self.phase == 'selling':
            STATS['saleBusy'] += 1
            self.phase = 'sellWait'
            self.until = globalClock.getRealTime() + random.uniform(3.0, 7.0)
        elif fieldName == 'setMoney':
            self.money = args[0]
            self.bot.ownFields['setMoney'] = tuple(args)
        elif fieldName == 'setFishTank':
            if len(args[0]) > self.tank:
                STATS['tankGrewByAI'] += len(args[0]) - self.tank    # the AI put the catch in my bucket (DB)
            self.tank = len(args[0])
            self.bot.ownFields['setFishTank'] = tuple(args)
        elif fieldName == 'setMaxFishTank':
            self.maxTank = args[0]
            self.bot.ownFields['setMaxFishTank'] = tuple(args)

    def __occupied(self, avId):
        now = globalClock.getRealTime()
        if avId == self.bot.avId and self.phase == 'entering':
            self.phase = 'seated'
            STATS['enters'] += 1
            self.seatedAt = now
            self.sessionEnd = now + random.uniform(*SESSION)
            self.until = now + random.uniform(3.0, 6.0)     # the run-in + pole anim, then the first cast
            if self.yieldNow:
                self.__leave('yield', exitSpot=True)
        elif avId == 0 and self.phase == 'seated':
            STATS['kicked'] += 1                              # the AI's cast timeout / out of beans
            self.log('put out of the spot by the AI (money %s)' % self.money)
            self.phase = 'leaving'
            self.until = now + 0.5
        elif avId == 0 and self.phase == 'leaving':
            self.until = min(self.until, now + random.uniform(0.3, 1.0))
        elif avId not in (0, self.bot.avId) and self.phase in ('toSpot', 'entering'):
            self.log('spot %s taken by %s before it got there' % (self.spotId, avId))
            self.bot.stopWalking()
            self.releaseAll()
            self.phase = 'done'

    # ---- the tick ----
    def step(self, now):
        bot = self.bot
        ph = self.phase
        if ph == 'done':
            return False
        if ph == 'toSpot':
            if bot.path:
                if now > self.until:
                    return False
                return True
            if occupant(self.spot) not in (0, bot.avId):
                return False
            if bot.area.kind == 'street' and bot.zoneId != self.zone and self.zone in bot.area.zones:
                bot.relocate(self.zone)
            self.__faceWater()
            bot.send('requestEnter', [], doId=self.spotId, className='DistributedFishingSpot')
            self.phase = 'entering'
            self.until = now + 6.0
            return True
        if ph == 'entering':
            return now < self.until
        if ph == 'seated':
            return self.__fish(now)
        if ph == 'leaving':
            if now < self.until:
                return True
            self.fast = False
            self.releaseAll()
            # sweep fix: off the spot, re-assert the toon's parent (b_setParent(SPRender), as a client does on
            # arriving anywhere): a client that missed the spot's setOccupied(0) kept this toon parented to the
            # spot's angle node and showed it walking 6 ft under TTC (seen: parent FishingSpotAngleNP)
            from toontown.toonbase import ToontownGlobals
            bot.send('setParent', [ToontownGlobals.SPRender])
            if self.sellAfter and self.tank > 0:
                return self.__goSell()
            # step off the dock back onto the walk map
            wm = bot.area.wm
            k = wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
            if k is None:
                return False
            bot.path = [wm.pos(k) + (k,)]
            bot.speed = WALK_SPEED
            bot.setAnim('walk')
            self.phase = 'stepOff'
            return True
        if ph == 'stepOff':
            return bool(bot.path)
        if ph == 'toSell':
            if bot.path:
                return now < self.until
            f = FISHERMEN.get(bot.area.id)
            npc = self.__fisherman()
            if npc is None or f is None:
                self.log('no Fisherman found in %s' % bot.area.id)
                return False
            if bot.area.kind == 'street' and bot.zoneId != npc.zoneId and npc.zoneId in bot.area.zones:
                bot.relocate(npc.zoneId)
            bot.faceTo(Point3(f[0], f[1], f[2]))
            bot.send('avatarEnter', [], doId=npc.doId, className='DistributedNPCFisherman')
            self.npcId = npc.doId
            self.phase = 'selling'
            self.until = now + 8.0
            return True
        if ph == 'selling':
            return now < self.until
        if ph == 'sellWait':
            if now < self.until:
                return True
            self.tries += 1
            if self.tries > 3:
                return False
            bot.send('avatarEnter', [], doId=self.npcId, className='DistributedNPCFisherman')
            self.phase = 'selling'
            self.until = now + 8.0
            return True
        if ph == 'sellClick':
            if now < self.until:
                return True
            bot.send('completeSale', [1], doId=self.npcId, className='DistributedNPCFisherman')
            self.phase = 'selling'
            self.until = now + 8.0
            return True
        return False

    def __faceWater(self):
        s = self.spot
        x, y, z, h = s.get('setPosHpr')[:4]
        rad = math.radians(h)
        self.bot.faceTo(Point3(x - math.sin(rad) * 10.0, y + math.cos(rad) * 10.0, z))
        self.bot.pos = Point3(s.pos)
        self.bot.broadcastNow()

    def __fisherman(self):
        for z in [self.zone] + [z for z in self.bot.area.zones if z != self.zone]:
            v = self.bot.director.viewOf(z)
            if v is not None:
                o = v.first('DistributedNPCFisherman')
                if o is not None:
                    return o
        return None

    def __goSell(self):
        bot = self.bot
        f = FISHERMEN.get(bot.area.id)
        if f is None or self.__fisherman() is None:
            return False
        rad = math.radians(f[3])
        # the Fisherman's collision tube is at (0, 1) in front of him, radius 1: stand just outside it
        x, y = f[0] - math.sin(rad) * 3.0, f[1] + math.cos(rad) * 3.0
        wm = bot.area.wm
        k = wm.nearestNode(x, y, f[2])
        if k is None:
            return False
        z = wm.pos(k)[2]
        if not bot.walkToPos(x, y, z, WALK_SPEED):
            return False
        self.phase = 'toSell'
        self.until = globalClock.getRealTime() + 120.0
        return True

    def __leave(self, why, exitSpot):
        self.leaveWhy = why
        self.checks = None
        self.fast = False
        if exitSpot:
            self.bot.send('requestExit', [], doId=self.spotId, className='DistributedFishingSpot')
            self.phase = 'leaving'
            self.until = globalClock.getRealTime() + 3.0      # the exit movie, then setOccupied(0)
            return True
        return self.__goSell()

    # ---- casting ----
    def __fish(self, now):
        bot = self.bot
        if self.checks is not None:
            return self.__check(now)
        if now < self.until:
            return True
        # a turn is over: session done, bucket full, broke
        if self.tank >= self.maxTank:
            STATS['tankFull'] += 1
            self.sellAfter = True
            return self.__leave('bucket full', True)
        cost = FishGlobals.getCastCost(self.rod)
        if self.money < cost:
            STATS['broke'] += 1
            self.sellAfter = self.tank > 0
            return self.__leave('out of jellybeans', True)
        if now >= self.sessionEnd:
            # P10b: a player sells when the bucket is full, not after every session (each sale is a money +
            # fish tank database write)
            self.sellAfter = self.tank >= self.maxTank
            return self.__leave('had enough', True)
        self.__cast(now)
        return True

    def __targets(self):
        v = self.bot.director.viewOf(self.zone)
        if v is None:
            return [], None
        pond = v.objects.get(self.pondId)
        return [o for o in v.ofClass('DistributedFishingTarget')
                if o.get('setPondDoId', (0,))[0] == self.pondId], pond

    def __cast(self, now):
        targets, pond = self.__targets()
        water, area = waterLevel(pond)
        node = spotNode(self.spot)
        try:
            aim = None
            if targets and random.random() < 0.85:
                # aim at the bubbles, with a player's error
                t = random.choice(targets)
                p = targetPos(t, now, area)
                aim = Point3(p[0] + random.gauss(0, 2.0), p[1] + random.gauss(0, 2.0), p[2])
            if aim is not None:
                local = node.getRelativePoint(_root, aim)
                heading = math.degrees(math.atan2(-local[0], local[1]))
                dist = math.hypot(local[0], local[1])
            else:
                heading = random.uniform(-35.0, 35.0)
                dist = random.uniform(8.0, 26.0)
            heading = max(-ANGLE_LIMIT, min(ANGLE_LIMIT, heading))
            lo, hi = 0.05, 1.0
            for _ in range(24):
                mid = (lo + hi) / 2.0
                if landing(mid, water)[1] < dist:
                    lo = mid
                else:
                    hi = mid
            power = round(max(0.05, min(1.0, lo)) * 255.0) / 255.0
            heading = round(heading * 100.0) / 100.0
            tFlight, y = landing(power, water)
            ang = node.attachNewNode('angle')
            ang.setH(heading)
            bob = _root.getRelativePoint(ang, Point3(0.0, y, water))
        finally:
            node.removeNode()
        self.bot.send('doCast', [power, heading], doId=self.spotId, className='DistributedFishingSpot')
        self.money -= FishGlobals.getCastCost(self.rod)
        STATS['casts'] += 1
        startT = 0.7 + (1.0 - power) * 0.3
        splash = now + (1.2 - startT) + tFlight
        self.cast = {'bob': bob, 'area': area, 'at': now}
        self.checks = [splash + 1.0, splash + random.uniform(15.0, 40.0)]    # next check, give up at (P10b: a player waits for the bite)
        self.fast = True

    def __check(self, now):
        c = self.checks
        if c[0] == float('inf'):               # hit sent: waiting for the pull-in
            if now > c[1]:
                self.checks = None
                self.fast = False
                self.until = now + random.uniform(1.0, 3.0)
            return True
        while c[0] <= now:
            tc = c[0]
            if tc > c[1]:
                STATS['recasts'] += 1          # nothing bit: a player casts again
                self.checks = None
                self.fast = False
                self.until = now + random.uniform(3.0, 8.0)     # P10b: a few seconds before casting again
                return True
            targets, pond = self.__targets()
            for t in targets:
                if (targetPos(t, tc, self.cast['area']) - self.cast['bob']).length() < TARGET_RADIUS:
                    self.bot.send('hitTarget', [t.doId], doId=self.pondId, className='DistributedFishingPond')
                    STATS['hits'] += 1
                    self.checks = [float('inf'), now + 8.0]
                    return True
            c[0] = tc + POLL
        return True

    def __pulledIn(self, code, item):
        now = globalClock.getRealTime()
        STATS['catches'] += 1
        if code in FISH_CODES:
            STATS['fish'] += 1                 # the bucket count comes from the AI's setFishTank
        elif code == FishGlobals.BootItem:
            STATS['boots'] += 1
        elif code == FishGlobals.JellybeanItem:
            STATS['beans'] += 1
            self.money += item
        elif code == FishGlobals.QuestItem:
            STATS['questItems'] += 1
        elif code == FishGlobals.OverTankLimit:
            self.tank = self.maxTank
        self.checks = None
        self.fast = False
        # reel + reel-neutral + fish-again (~4.5 s everyone sees), then the player reads the panel
        self.until = now + random.uniform(8.0, 16.0)     # P10b: human pace between catches (was 6-11 s)

    # ---- selling ----
    def __saleMovie(self, flag):
        now = globalClock.getRealTime()
        if flag == SELL_START:
            self.selling = self.tank
            self.phase = 'sellClick'
            self.until = now + random.uniform(2.0, 4.5)      # reads the Fisherman's dialog, clicks Yes
        elif flag in (SELL_COMPLETE, SELL_TROPHY):
            STATS['sales'] += 1
            if flag == SELL_TROPHY:
                STATS['trophies'] += 1
            n = getattr(self, 'selling', self.tank)
            STATS['fishSold'] += n
            self.log('sold %d fish to the Fisherman in %s' % (n, self.bot.area.id))
            self.tank = 0
            self.bot.ownFields['setFishTank'] = ([], [], [])
            self.phase = 'done'
        elif flag == SELL_NOFISH:
            STATS['saleNoFish'] += 1
            self.tank = 0
            self.phase = 'done'
        elif flag == SELL_TIMEOUT:
            self.phase = 'done'


@register
class StreetFishing(Fishing):
    """The same, on the streets' ponds (4 spots each), picked a little less often than in a playground."""
    name = 'fishing-street'
    weight = 1.5
    kinds = ('street',)
