"""P8b COG HQ: bots gather in the Cog HQ courtyards and lobbies and run factories, mints, DA offices and
Cog Golf Courses in groups, like players (the boss battles are activities/boss.py, same plumbing).

Who goes where (BotWorld.ELIGIBLE['coghq']): the Brrrgh and Dreamland regulars. A lobby (the boss
elevator's room) only takes a bot whose suit for that department is whole (the lobby door's own
check); the courtyards, the factory exterior and the DA office lobby take any of them.

A run, per bot (HQRun):
  walk      to the facility elevator (its walk-map place), wait for the group (<= 60 s)
  board     requestBoard (a real client's elevator sphere); the seat is fillSlotN(me)
  seated    until the elevator sends set<Facility>InteriorZone(zone) to the avatar
  ride      the Quiet Zone for the client's loading time, then setLocation(zone)
  inside    toontown/bots/coghq/level.py (the group's FacilityRun)
  out       after the win the client teleports to the HQ courtyard; so does the bot.
A REAL player boarding a facility elevator: 1-3 bots standing in that place come along (never more
than the free seats), follow him inside and join his battles.

The proof hook: <run>/bots-coghq.cmd, one line per run, e.g. "factory 4 front", "mint 3 coin",
"stage 4 a", "cgc 4 front", "vp 6": the commander picks eligible free bots, sends them (teleport)
to the facility's place and starts the run. The file is renamed .done when read.
Run log: <run>/bots-coghq.json/.txt (coghq/common.py).
"""
import math
import os
import random
import time
import traceback

from direct.showbase.DirectObject import DirectObject
from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots import BotWorld
from toontown.bots import battlebrain as bb
from toontown.bots.activities import Activity, register
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED
from toontown.bots.coghq import common as cm
from toontown.bots.coghq import suits
from toontown.bots.coghq.level import FacilityRun, NEXT_FLOOR_FIELDS

QUIET = OTPGlobals.QuietZone
LOBBY_DEPT = {10100: cm.C, 11100: cm.S, 12100: cm.M, 13100: cm.L}
MAX_RUNS = 3                     # bot-led facility runs at once on the district
# facility variant -> (elevator id field value, walk-map place, laff minimum (FactoryLaffMinimums))
VARIANTS = {
    'factory': {'front': (0, 'elevator_factory_front', 0), 'side': (1, 'elevator_factory_side', 31)},
    'mint': {'coin': (12500, 'elevator_coin_mint', 0), 'dollar': (12600, 'elevator_dollar_mint', 66),
             'bullion': (12700, 'elevator_bullion_mint', 71)},
    'stage': {'a': (0, 'elevator_office_a', 0), 'b': (1, 'elevator_office_b', 81),
              'c': (2, 'elevator_office_c', 86), 'd': (3, 'elevator_office_d', 96)},
    'cgc': {'front': (10500, 'kart_front_three', 0), 'middle': (10600, 'kart_middle_six', 101),
            'back': (10700, 'kart_back_nine', 106)},
}
ID_FIELD = {'factory': 'setEntranceId', 'mint': 'setMintId', 'stage': 'setEntranceId', 'cgc': 'setCountryClubId'}


# ---- who may stand in a Cog HQ place ---------------------------------------------------------------------
def eligible(bot, area):
    if not suits.higherHood(bot):
        return taskHere(bot, area)
    dept = LOBBY_DEPT.get(area.id)
    if dept is not None:          # a boss lobby: the regulars who can board its elevator (the AI's own checks)
        return suits.hasWholeSuit(bot, dept) and suits.readyForPromotion(bot, dept) and \
            (bb.botHp(bot)[1] or 0) >= BOSS_LAFF.get(dept, 0)
    return True


def taskHere(bot, area):
    """PROGRESSION: a progressing toon goes to a Cog HQ (not a boss lobby) only for a facility ToonTask there,
    as a player does (the factory for a Daisy Gardens task); the regulars' rule above is untouched."""
    if getattr(bot, 'crew', False) or area.id in LOBBY_DEPT:
        return False
    g = getattr(bot, 'goal', None) or {}
    if g.get('kind') != 'facility':
        return False
    f = cm.FACILITIES.get('stage' if g.get('facility') == 'da' else g.get('facility'))
    return f is not None and area.hood == f['hq']


BotWorld.ELIGIBLE['coghq'] = eligible
# P8c: the laff a regular brings to each boss lobby (the Cog rounds of the CJ and the CEO hit 12-14 a Cog, several Cogs
# a round, for 15-30 rounds: a 60-laff Brrrgh toon went sad in the first battle every time)
BOSS_LAFF = {cm.S: 60, cm.M: 80, cm.L: 90, cm.C: 100}


def _quality(bot):
    from toontown.bots.coghq import loadout
    return loadout.quality(bot)


def fit(bot, frac=0.8, gags=15):
    return bot.state == 'present' and bot.travel is None and bb.healthy(bot, frac) and \
        bb.gagCount(bot, bb.ATTACK_TRACKS) >= gags


# P8c: who a facility run takes (the regulars who can finish it: a DA office's Cogs are level 9-12 and it has ~12
# battles, so a 60-laff toon with a dozen gags goes sad by the third cell): (laff share, attack gags, max laff)
KIT = {'factory': (0.7, 20, 0), 'mint': (0.75, 30, 50), 'stage': (0.85, 45, 90), 'cgc': (0.85, 45, 100)}


def healer(bot, n=6):
    """A regular who can carry the group's Toon-Ups (the owner's rule 5: one healer, sized to what is missing)."""
    return bb.gagCount(bot, (bb.HEAL,)) >= n


def withHealers(pick, pool, need):
    """P8c: a party takes at least `need` healers (a DA office party with no Toon-Up lost a toon every run):
    swap the weakest non-healers in `pick` for the strongest healers left in `pool`."""
    have = sum(1 for b in pick if healer(b))
    spare = [b for b in pool if b not in pick and healer(b)]
    for h in spare[:max(0, need - have)]:
        others = [b for b in pick if not healer(b)]
        if not others:
            break
        pick[pick.index(others[-1])] = h
    return pick


def kitFor(bot, kind, lead=False):
    frac, gags, laff = KIT.get(kind, (0.7, 20, 0))
    # the owner (09-25): a facility run starts with a gag check - stocked up like a normal player
    return fit(bot, frac + (0.1 if lead else 0.0), gags) and (bb.botHp(bot)[1] or 0) >= laff and bb.stocked(bot)


def free(bot):
    return bot.state == 'present' and bot.travel is None and not bot.pinned and \
        (bot.activity is None or (bot.activity.interruptible and bot.activity.name not in ('coghq', 'boss')))


# ---- groups -------------------------------------------------------------------------------------------------
class Group:
    def __init__(self, kind, variant, area, placeName, elevId, log):
        self.kind = kind                 # facility or boss kind
        self.variant = variant
        self.area = area                 # the Area with the elevator
        self.placeName = placeName
        self.elevId = elevId
        self.log = log
        self.members = []                # bot avIds
        self.leader = None
        self.go = False
        self.withPlayer = False
        self.t0 = globalClock.getRealTime()
        self.run = None                  # FacilityRun / BossRun, made when the first bot gets in
        self.expect = 0                  # how many bots the commander is still bringing

    def place(self):
        return next((p for p in self.area.wm.places() if p['name'] == self.placeName), None)


RUNS = []                                # live groups


def elevatorsIn(director, area, cls):
    v = director.viewOf(area.id)
    return [o for o in v.objects.values() if o.className == cls] if v is not None else []


def findElevator(director, kind, variant, area):
    """(elevator view object, walk-map place name) for a facility variant / a boss lobby."""
    if kind in cm.BOSSES:
        els = elevatorsIn(director, area, cm.BOSSES[kind]['elevClass'])
        return (els[0] if els else None), 'elevator_' + kind
    idVal, placeName, minLaff = VARIANTS[kind][variant]
    for o in elevatorsIn(director, area, cm.FACILITIES[kind]['elevClass']):
        if (o.get(ID_FIELD[kind]) or (None,))[0] == idVal:
            return o, placeName
    return None, placeName


# ---- the run, per bot -------------------------------------------------------------------------------------------
class HQRun(Activity):
    """Board a Cog HQ elevator with the group, ride, then hand over to inside()."""
    name = 'coghq'
    weight = 0.0
    progress = True          # PROGRESSION (owner 1a): none starts while no real player is online
    kinds = ('coghq',)
    label = 'cog-hq'

    def __init__(self, bot, group, role='member'):
        Activity.__init__(self, bot)
        self.group = group
        self.role = role
        self.phase = 'walk'
        self.until = 0.0
        self.seat = None
        self.boardAt = 0.0
        self.zone = None
        self.fast = False
        self.outAt = 0.0
        self.label = 'to-' + group.kind

    # -- start / stop ---------------------------------------------------------------------------------------
    def start(self):
        bb.HUB.ensure(self.director)
        g, bot = self.group, self.bot
        if bot.avId not in g.members:
            g.members.append(bot.avId)
        if g.leader is None and self.role == 'leader':
            g.leader = bot.avId
        place = g.place()
        if place is None or bot.area is not g.area:
            return False
        node = g.area.placeNode(place)
        if node is None or not bot.walkTo(node, RUN_SPEED):
            k = g.area.nodeNear(place['pos'][0], place['pos'][1], 12.0)
            if k is None or not bot.walkTo(k, RUN_SPEED):
                if bot.node != node:
                    return False
        self.until = globalClock.getRealTime() + 150.0
        bb.HUB.watch(g.elevId, self.__elevField)
        self.interruptible = False
        return True

    def stop(self, why):
        g = self.group
        bb.HUB.unwatch(g.elevId, self.__elevField)
        if self.phase in ('boarding', 'seated') and why not in ('logout', 'district down') and self.bot.state == 'present':
            self.bot.send('requestExit', [], doId=g.elevId, className=self.elevClass())
        self.stopInside(why)
        if self.bot.avId in g.members and self.phase in ('walk', 'atelev', 'boarding'):
            g.members.remove(self.bot.avId)
        if not any(b.activity is not None and getattr(b.activity, 'group', None) is g
                   for b in self.director.bots.values() if b is not self.bot):
            if g in RUNS:
                RUNS.remove(g)
            if g.log.tEnd is None and g.log.tInside is None:
                g.log.finish('abandoned')
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()

    def elevClass(self):
        g = self.group
        return (cm.BOSSES.get(g.kind) or cm.FACILITIES.get(g.kind))['elevClass']

    def elev(self):
        v = self.director.viewOf(self.group.area.id)
        return v.objects.get(self.group.elevId) if v is not None else None

    # -- elevator broadcasts --------------------------------------------------------------------------------
    def __elevField(self, obj, fieldName, args):
        me = self.bot.avId
        if fieldName.startswith('fillSlot') and args and args[0] == me:
            self.seat = int(fieldName[8:])
        elif fieldName.startswith('emptySlot') and args and args[0] == me:
            self.seat = None

    def onDirect(self, fieldName, args):
        g = self.group
        if fieldName == 'rejectBoard' and self.phase == 'boarding':
            g.log.note('%s rejected by the elevator (reason %s)' % (self.bot.avId, args[1:] if len(args) > 1 else args))
            self.until = 0.0
        elif fieldName.endswith('InteriorZone') or fieldName.endswith('InteriorZoneForce') or \
                fieldName.startswith('setBossOfficeZone'):
            self.zone = args[0]
            g.departed = True
        else:
            self.onDirectInside(fieldName, args)

    # -- the tick ---------------------------------------------------------------------------------------------
    def step(self, now):
        bot, g = self.bot, self.group
        ph = self.phase
        if ph == 'walk':
            if bot.path:
                if now > self.until:
                    return False
                return True
            place = g.place()
            if place is not None:
                bot.faceTo(place['pos'])
            self.phase = 'atelev'
            self.until = now + 75.0
            return True
        if ph == 'atelev':
            e = self.elev()
            if e is None or g.run is not None or getattr(g, 'departed', False):
                return False                   # the group already rode up without me: too late
            lead = g.leader if g.leader in g.members else (g.members[0] if g.members else None)
            if not g.go and bot.avId == lead:
                here = [a for a in g.members if self.__atElevator(a)]
                walking = any(getattr(getattr(self.director.bots.get(a), 'activity', None), 'phase', None) == 'walk'
                              for a in g.members)
                if (len(here) >= len(g.members) and g.expect <= 0) or (now > self.until - 5.0 and not walking) \
                        or now > self.until + 60.0:
                    g.go = True
                    kit = []
                    for a in here:
                        b = self.director.bots.get(a)
                        hp, mx = bb.botHp(b)
                        kit.append('%s %s/%s laff %d gags' % (a, hp, mx, bb.gagCount(b, bb.ATTACK_TRACKS)))
                    g.log.note('boarding with %s' % ', '.join(kit))
            if (g.go or g.withPlayer) and cm.elevState(e) in cm.BOARDABLE:
                if not self.boardAt:
                    self.boardAt = now + random.uniform(0.4, 2.5)
                if now >= self.boardAt:
                    bot.send('requestBoard', [], doId=g.elevId, className=self.elevClass())
                    self.phase = 'boarding'
                    self.until = now + 6.0
            elif now > self.until:
                return False
            return True
        if ph == 'boarding':
            if self.seat is not None:
                self.phase = 'seated'
                self.fast = True                  # tick quickly: the ride's end starts timers
                self.label = g.kind
                bb.STATS.count('hq_boarded')
                self.until = now + 75.0
            elif now > self.until:
                return False
            return True
        if ph == 'seated':
            if self.zone is not None:
                self.phase = 'ride'
                bot.relocate(QUIET)
                # the client loads the place (a boss office starts a 5 s WaitForToons barrier at once)
                self.until = now + (random.uniform(0.8, 1.5) if g.kind in cm.BOSSES else random.uniform(2.0, 4.0))
                return True
            if self.seat is None or now > self.until:
                return False
            return True
        if ph == 'ride':
            if now >= self.until:
                bot.path = []
                self.phase = 'inside'
                self.enterInside(now)
            return True
        if ph == 'inside':
            return self.stepInside(now)
        if ph == 'out':
            if now >= self.outAt:
                self.leaveTo(cm.FACILITIES.get(g.kind, cm.BOSSES.get(g.kind))['hq'], 'won')
                return False
            return True
        return False

    def __atElevator(self, avId):
        b = self.director.bots.get(avId)
        place = self.group.place()
        return b is not None and place is not None and b.activity is not None and \
            getattr(b.activity, 'phase', None) in ('atelev', 'boarding', 'seated') and \
            math.hypot(b.pos[0] - place['pos'][0], b.pos[1] - place['pos'][1]) < 30.0

    def leaveTo(self, areaId, why):
        """Out of the facility / boss: the client teleports (to the HQ courtyard after a win, home when sad)."""
        bot, d = self.bot, self.director
        bot.p8bWantGags = bb.gagCount(bot, bb.ATTACK_TRACKS) < 30
        dest = d.world.areas.get(areaId)
        if why == 'sad' or dest is None or not d.eligible(bot, dest):
            dest = d.world.areas.get(bot.home)
        bot.path = []
        self.stopInside('leave')
        self.phase = 'gone'
        if dest is not None:
            d.travel(bot, dest, why='P8b %s: out of the %s' % (why, self.group.kind), via='teleport')

    # -- overridden by the facility / boss activities ---------------------------------------------------------
    def enterInside(self, now):
        pass

    def stepInside(self, now):
        return False

    def stopInside(self, why):
        pass

    def onDirectInside(self, fieldName, args):
        pass


@register
class FacilityActivity(HQRun):
    """A factory / mint / DA office / CGC run (the inside is coghq/level.py)."""
    name = 'coghq'
    weight = 0.25
    kinds = ('coghq',)

    @classmethod
    def canRun(cls, bot):
        """A regular standing at a facility entrance sometimes gets a group together."""
        d = bot.director
        if bot.area is None or not d.clockSynced or not d.air.districtId:
            return False
        kind = next((k for k, v in cm.FACILITIES.items() if v['ext'] == bot.area.id), None)
        if kind is None or globalClock.getRealTime() < getattr(bot, 'p8bNext', 0.0):
            return False
        if sum(1 for g in RUNS if g.kind in cm.FACILITIES and not g.withPlayer) >= MAX_RUNS:
            return False
        if bot.area.players:
            return False                   # where he is, bots wait for him to pick the elevator
        from toontown.bots.activities.faclobby import spareForRun
        if not spareForRun(bot.area):
            return False                   # sweep fix: a bot-only run never takes the elevators' waiters
        return kitFor(bot, kind, lead=True)

    def __init__(self, bot, group=None, role='member'):
        HQRun.__init__(self, bot, group, role) if group is not None else Activity.__init__(self, bot)
        self.brain = None
        self.lastBattle = None
        self.joinAt = 0.0
        self.boardFloorAt = 0.0
        self.floorBoarded = False
        self.leftAck = False
        self.nextOuch = 0.0
        if group is None:
            self.group = None

    def start(self):
        if self.group is None:
            # natural: lead a group from the bots standing here
            bot, d = self.bot, self.director
            bot.p8bNext = globalClock.getRealTime() + random.uniform(240.0, 600.0)
            kind = next(k for k, v in cm.FACILITIES.items() if v['ext'] == bot.area.id)
            mates = [b for b in d.bots.values() if b is not bot and b.area is bot.area and free(b) and kitFor(b, kind)]
            if len(mates) < 2:
                return False
            n = random.choice((2, 3, 3))
            pool = mates
            mates = random.sample(mates, min(n, len(mates)))
            if not healer(bot):
                mates = withHealers(mates, pool, 1)
            hp = min(bb.botHp(b)[0] or 0 for b in mates + [bot])
            opts = [v for v, t in VARIANTS[kind].items() if t[2] <= hp]
            variant = random.choice(opts) if opts else None
            if variant is None:
                return False
            g = makeGroup(d, kind, variant, bot.area)
            if g is None:
                return False
            self.__init__(bot, g, 'leader')
            ok = HQRun.start(self)
            if not ok:
                RUNS.remove(g)
                return False
            for b in mates:
                if b.startActivity(FacilityActivity(b, g)):
                    b.nextTick = min(b.nextTick, globalClock.getRealTime())
            g.log.members = list(g.members)
            return True
        return HQRun.start(self)

    # -- inside ------------------------------------------------------------------------------------------------
    def enterInside(self, now):
        g, bot = self.group, self.bot
        if g.run is None:
            g.run = FacilityRun(g.kind, self.director, g.log)
            g.run.members = list(g.members)
            g.run.players = set(p for p in getattr(g, 'playerIds', []))
            g.log.members = list(g.members)
        if g.run.zone is not None and g.run.zone != self.zone:
            g.log.note('%s rode up alone into %s: out again' % (bot.avId, self.zone))
            self.phase = 'out'
            self.outAt = now
            return
        g.run.enterZone(self.zone)
        bot.relocate(self.zone)
        self.fast = True
        self.__placeInside()

    def __placeInside(self):
        """Stand where the client puts an arriving toon (next to him when he is here)."""
        run, bot = self.group.run, self.bot
        if run.trail:
            x, y, z, h, t = run.trail[-1]
            bot.pos = Point3(x + random.uniform(-4, 4), y + random.uniform(-4, 4), z)
            bot.h = h
        bot.broadcastNow()

    def onDirectInside(self, fieldName, args):
        g = self.group
        if fieldName in NEXT_FLOOR_FIELDS and g.run is not None:
            self.__nextFloor(args[0])
        elif fieldName in ('startBoard', 'boardCleared', 'informGag') and g.run is not None:
            for game in g.run.games.values():
                if game.cls == 'DistributedGolfGreenGame':
                    game.onDirect(self.bot, fieldName, args)
        if self.brain is not None:
            self.brain.onDirect(fieldName, args)

    def __nextFloor(self, zone):
        run, bot = self.group.run, self.bot
        if self.brain is not None:
            self.brain.leave('floor')
            self.brain = None
        bot.relocate(QUIET)
        run.enterZone(zone)
        self.floorBoarded = False
        self.boardFloorAt = 0.0

        def arrive(task):
            if bot.state == 'present' and self.phase == 'inside':
                bot.relocate(zone)
                self.__placeInside()
            return task.done
        taskMgr.doMethodLater(random.uniform(2.0, 3.5), arrive, 'botfloor-%d' % bot.avId)

    def stepInside(self, now):
        g, bot = self.group, self.bot
        run = g.run
        if run is None:
            return False
        run.tick(now)
        if bot.zoneId != run.zone:
            return True                        # between floors (the Quiet Zone)
        if self.brain is not None:
            if not self.brain.step(now):
                res = self.brain.result
                self.brain = None
                run.log.count('battle_' + res)
                if res == 'died':
                    run.log.note('%s went sad' % bot.avId)
                    self.__ackLeaving()
                    self.leaveTo(bot.home, 'sad')
                    return False
            return True
        if run.defeated():
            if not run.won:
                run.won = True
                run.log.finish('won')
            if not self.outAt:
                self.outAt = now + random.uniform(2.0, 5.0)
                self.__ackLeaving()
                self.phase = 'out'
            return True
        if not self.__alive():
            return True
        # join the battle my group (or the real player) is in
        b = self.__battleToJoin(run)
        if b is not None:
            if not self.joinAt:
                self.joinAt = now + random.uniform(1.0, 3.5)
            elif now >= self.joinAt:
                self.joinAt = 0.0
                self.brain = bb.BattleBrain(bot, 'level')
                self.brain.join(b)
            return True
        self.joinAt = 0.0
        if run.players:
            self.__follow(now)
        elif run.target is not None and self.__leader() == bot.avId and now >= run.targetAt and \
                (not run.requested or now - run.requested > 10.0):
            self.__startBattle(run, now)
        elif run.state == 'floorEnd':
            self.__boardFloor(run, now)
        self.__ouch(now)
        return True

    def __alive(self):
        hp, mx = bb.botHp(self.bot)
        return hp is None or hp > 0

    def __leader(self):
        run = self.group.run
        for a in run.members:
            b = self.director.bots.get(a)
            if b is not None and b.state == 'present' and b.zoneId == run.zone and \
                    isinstance(b.activity, FacilityActivity) and b.activity.phase == 'inside':
                hp, mx = bb.botHp(b)
                if hp is None or hp > 0:
                    return a
        return None

    def __battleToJoin(self, run):
        me = self.bot.avId
        for b in run.battles():
            m = b.get('setMembers')
            if not m or me in m[6] or len(m[6]) >= 4:
                continue
            st = (b.get('setState') or ('',))[0]
            if st not in ('FaceOff', 'WaitForJoin', 'WaitForInput', 'MakeMovie', 'PlayMovie'):
                continue
            toons = set(m[6])
            if toons & run.players or toons & set(run.members):
                return b
        return None

    def __startBattle(self, run, now):
        by = run.suits()
        cands = by.get(run.target) or []
        if not cands:
            return
        lv = run.levels.get(run.target[0])
        suit = random.choice(cands)
        pos, h = lv.suitPos(suit) if lv is not None else (Point3(0, 0, 0), 0)
        run.requested = now
        self.bot.pos = Point3(pos[0], pos[1] - 6.0, pos[2])
        self.bot.broadcastNow()
        self.brain = bb.BattleBrain(self.bot, 'level')
        self.brain.startOn(suit, pos, h, className=suit.className)
        run.log.count('battles_started')

    def __follow(self, now):
        """Walk his trail, a few steps behind (every spot on it is floor he walked on)."""
        run, bot = self.group.run, self.bot
        if not run.trail:
            return
        i = max(0, len(run.trail) - 1 - (run.members.index(bot.avId) + 1 if bot.avId in run.members else 2) * 2)
        x, y, z, h, t = run.trail[i]
        dx, dy = x - bot.pos[0], y - bot.pos[1]
        d = math.hypot(dx, dy)
        if d > 2.0:
            step = min(d, RUN_SPEED * 0.3)
            bot.pos = Point3(bot.pos[0] + dx / d * step, bot.pos[1] + dy / d * step, z)
            bot.h = math.degrees(math.atan2(-dx, dy))
            bot.dirty = True
            bot.setAnim('run')
        elif bot.anim in ('run', 'walk'):
            bot.setAnim('neutral')

    def __boardFloor(self, run, now):
        e = run.floorElevator()
        if e is None or self.floorBoarded:
            return
        if not self.boardFloorAt:
            self.boardFloorAt = now + random.uniform(3.0, 9.0)
            return
        if now >= self.boardFloorAt and cm.elevState(e).lower() in ('waitempty', 'waitcountdown'):
            self.bot.send('requestBoard', [], doId=e.doId, className=e.className)
            self.floorBoarded = True
            run.log.count('floor_boarded')

    def __ouch(self, now):
        """Stompers, goons and lasers now and then catch a kid (DistributedLevel.setOuch, like the client)."""
        run = self.group.run
        if run.kind not in ('factory', 'mint', 'stage') or run.players:
            return
        if not self.nextOuch:
            self.nextOuch = now + random.uniform(60.0, 240.0)
            return
        if now >= self.nextOuch:
            self.nextOuch = now + random.uniform(180.0, 480.0)
            hp, mx = bb.botHp(self.bot)
            if hp is not None and mx and hp > 0.5 * mx:
                lv = next(iter(run.levels.values()), None)
                if lv is not None:
                    dmg = random.choice((2, 3, 4, 6))
                    self.bot.send('setOuch', [dmg], doId=lv.doId, className=lv.obj.className)
                    run.log.count('ouch')

    def __ackLeaving(self):
        """announceLeaving: the level's 'allToonsGone' barrier (the facility is deleted once all left)."""
        if self.leftAck or self.group.run is None:
            return
        self.leftAck = True
        me = self.bot.avId
        for o in self.group.run.objects():
            for context, name, avIds in (o.get('setBarrierData') or ([],))[0]:
                if name == 'allToonsGone' and me in avIds:
                    self.bot.send('setBarrierReady', [context], doId=o.doId, className=o.className)

    def stopInside(self, why):
        if self.brain is not None and self.brain.phase != 'done':
            self.brain.leave(why)
        self.brain = None
        g = self.group
        if g is None or g.run is None:
            return
        if self.phase in ('inside', 'out'):
            self.__ackLeaving()
        if not any(b is not self.bot and isinstance(b.activity, FacilityActivity) and b.activity.group is g
                   and b.activity.phase in ('inside', 'out', 'ride') for b in self.director.bots.values()):
            if g.log.tEnd is None:
                g.log.finish('won' if g.run.won else ('left-' + why))
            g.run.end()


TREASURES = ('DistributedTTTreasure', 'DistributedDDTreasure', 'DistributedDGTreasure', 'DistributedMMTreasure',
             'DistributedBRTreasure', 'DistributedDLTreasure', 'DistributedOZTreasure', 'DistributedETreasure')


@register
class TreasureHunt(Activity):
    """A hurt toon in a playground runs to a treasure (DistributedTreasure.requestGrab, like the client's
    touch): the way a player gets laff back after a factory or a boss (the playground's own heal is 1 per 30 s)."""
    name = 'treasure'
    weight = 2.5
    kinds = ('playground',)
    label = 'treasure'

    @classmethod
    def canRun(cls, bot):
        if bot.node is None or bb.healthy(bot, 0.85) or bot.view is None:
            return False
        return any(o.className in TREASURES and not (o.get('setGrab') or (0,))[0] for o in bot.view.objects.values())

    def start(self):
        bot = self.bot
        ts = [o for o in bot.view.objects.values() if o.className in TREASURES and not (o.get('setGrab') or (0,))[0]]
        ts.sort(key=lambda o: (o.get('setPosition')[0] - bot.pos[0]) ** 2 + (o.get('setPosition')[1] - bot.pos[1]) ** 2)
        for o in ts[:3]:
            if self.claim(('treasure', o.doId)) and not self.realNear(Point3(*o.get('setPosition')), 25.0):
                x, y, z = o.get('setPosition')
                if bot.walkToPos(x, y, z, RUN_SPEED, 'run'):
                    self.target = o
                    self.until = globalClock.getRealTime() + 40.0
                    return True
        return False

    def step(self, now):
        bot = self.bot
        if bot.path:
            return now < self.until
        o = bot.view.objects.get(self.target.doId) if bot.view is not None else None
        if o is not None and not (o.get('setGrab') or (0,))[0]:
            bot.send('requestGrab', [], doId=o.doId, className=o.className)
            bb.STATS.count('hq_treasure_grab')
        return False


def _hqRestock():
    """The P8a gag-shop trip, for a regular who came home from a Cog HQ short of attack gags (a player
    stocks up before a facility even when his pouch still has Toon-Ups and lures in it)."""
    from toontown.bots.activities.battle import Restock

    @register
    class HQRestock(Restock):
        name = 'hqrestock'
        weight = 6.0

        @classmethod
        def canRun(cls, bot):
            if not getattr(bot, 'p8bWantGags', False) or bot.area is None or bot.node is None:
                return False
            if (bb.stocked(bot) and bb.gagCount(bot, bb.ATTACK_TRACKS) >= 30 and _quality(bot) >= 20) \
                    or (bot.ownFields.get('setMoney') or (0,))[0] < 15:
                bot.p8bWantGags = False
                return False
            return any(p['kind'] == 'gagshop' for p in bot.area.doors)

        def start(self):
            self.bot.p8bLoadout = True
            return Restock.start(self)

        def stop(self, why):
            self.bot.p8bWantGags = False
            self.bot.p8bLoadout = False
            return Restock.stop(self, why)
    return HQRestock


try:
    _hqRestock()
except Exception:
    import traceback as _tb
    cm.notify.warning('[TTBOTS-P8b] HQRestock not registered: %s' % _tb.format_exc())


def makeGroup(director, kind, variant, area):
    e, placeName = findElevator(director, kind, variant, area)
    if e is None:
        return None
    label = (cm.FACILITIES.get(kind) or cm.BOSSES.get(kind))['label']
    log = cm.RunLog(kind, '%s %s' % (label, variant or ''), [])
    g = Group(kind, variant, area, placeName, e.doId, log)
    RUNS.append(g)
    return g


# ---- a real player boards a Cog HQ elevator: bots come along -------------------------------------------------
def _elevWatch(obj, fieldName, args):
    if not fieldName.startswith('fillSlot') or not args or not args[0]:
        return
    d = bb.HUB.director
    avId = args[0]
    if d is None or avId in d.bots:
        return
    kind = cm.ELEV_CLASSES.get(obj.className)
    area = d.world.areas.get(obj.zoneId)
    if kind is None or area is None or kind in cm.BOSSES:
        return                       # a boss elevator: activities/boss.py LobbyKeeper fills it
    g = next((x for x in RUNS if x.elevId == obj.doId and x.run is None), None)
    if g is not None:
        g.withPlayer = True
        g.go = True
        g.playerIds = getattr(g, 'playerIds', []) + [avId]
        g.log.players.append(avId)
        return
    variant = None
    if kind in VARIANTS:
        idv = (obj.get(ID_FIELD[kind]) or (None,))[0]
        variant = next((v for v, t in VARIANTS[kind].items() if t[0] == idv), None)
    e, placeName = findElevator(d, kind, variant, area)
    label = (cm.FACILITIES.get(kind) or cm.BOSSES.get(kind))['label']
    log = cm.RunLog(kind, '%s %s' % (label, variant or ''), [], withPlayer=True)
    log.players.append(avId)
    g = Group(kind, variant, area, placeName, obj.doId, log)
    g.withPlayer = True
    g.go = True
    g.playerIds = [avId]
    free_ = len([s for s in cm.seats(obj, 8 if kind in cm.BOSSES else 4) if not s])
    seatsN = 8 if kind in cm.BOSSES else 4
    # sweep fix: at least two bots ride a facility with a kid (the elevator's waiters, activities/faclobby.py)
    want = min(free_, random.choice((2, 3, 3)) if kind in cm.FACILITIES else random.choice((3, 4, 5, 6)))
    cands = []
    for bot in d.bots.values():
        if bot.area is not area or not free(bot) or bot.node is None:
            continue
        if kind in cm.BOSSES:
            dept = cm.BOSSES[kind]['dept']
            if not (suits.readyForPromotion(bot, dept) and fit(bot, 0.7, 10)):
                continue
        elif not fit(bot, 0.6, 10):
            continue
        cands.append((math.hypot(bot.pos[0] - obj.pos[0], bot.pos[1] - obj.pos[1]), bot))
    cands.sort(key=lambda c: c[0])
    RUNS.append(g)
    sent = []
    act = FacilityActivity if kind in cm.FACILITIES else _bossActivity()
    for dist, bot in cands[:want]:
        if bot.startActivity(act(bot, g)):
            bot.nextTick = min(bot.nextTick, globalClock.getRealTime())
            sent.append(bot.avId)
    log.members = list(g.members)
    short = want - len(sent)
    if short > 0 and COMMANDER is not None:
        # friends teleport in to fill the seats (the elevator's countdown is long enough for a teleport)
        more = []
        for bot in d.bots.values():
            if bot.area is area or not free(bot) or not suits.higherHood(bot) or not d.eligible(bot, area):
                continue
            if kind in cm.BOSSES and not suits.readyForPromotion(bot, cm.BOSSES[kind]['dept']):
                continue
            if fit(bot, 0.75, 12):
                more.append(bot)
        random.shuffle(more)
        for bot in more[:short]:
            if d.travel(bot, area, why='P8b: joins a real player at the %s' % kind, via='teleport'):
                COMMANDER.pending.append((g, bot, act, 'member', globalClock.getRealTime() + 40.0))
                g.expect += 1
                sent.append(bot.avId)
    if not sent:
        RUNS.remove(g)
        log.finish('no-bots')
    log.note('real player %s boarded the %s elevator (%d seats): bots %s come along' % (avId, kind, seatsN, sent))
    bb.STATS.count('hq_player_boarded')


def _bossActivity():
    from toontown.bots.activities import boss
    return boss.BossActivity


for _cls in cm.ELEV_CLASSES:
    bb.HUB.classHooks.setdefault(_cls, []).append(_elevWatch)


# ---- the commander (proof hook) + the run log + the suits, once the district is up ----------------------
class Commander(DirectObject):
    def __init__(self):
        DirectObject.__init__(self)
        self.pending = []                # (group, bot, activityClass, role)
        self.waiting = []                # (next check time, command words): groups still forming
        self.accept('botai-district-up', self.__districtUp)
        taskMgr.doMethodLater(2.0, self.__tick, 'bots-coghq-cmd')
        taskMgr.doMethodLater(10.0, self.__write, 'bots-coghq-write')

    @property
    def director(self):
        return getattr(simbase.air, 'botDirector', None)

    def __districtUp(self, districtId):
        d = self.director
        if d is None:
            return
        if not getattr(d, 'p8bArrivedHook', False):
            # a bot the commander sent starts its run the moment it lands (before anything else claims it)
            d.p8bArrivedHook = True
            orig = d.arrived

            def arrived(bot, orig=orig):
                orig(bot)
                try:
                    self.place(d, only=bot)
                except Exception:
                    d.error('P8b arrived', traceback.format_exc())
            d.arrived = arrived
        try:
            suits.seedAll(d)
            from toontown.bots.coghq import loadout
            loadout.seedAll(d)
        except Exception:
            d.error('P8b cog suits', traceback.format_exc())

    def __write(self, task):
        d = self.director
        if d is not None and d.world is not None:
            try:
                cm.writeLog(d)
            except Exception:
                d.error('P8b log', traceback.format_exc())
        return task.again

    def __tick(self, task):
        d = self.director
        if d is None or d.world is None or not d.air.districtId:
            return task.again
        try:
            bb.HUB.ensure(d)
            self.__readCommands(d)
            self.place(d)
            now = globalClock.getRealTime()
            due = [w for w in self.waiting if w[0] <= now]
            self.waiting = [w for w in self.waiting if w[0] > now]
            for t, words in due:
                self.command(d, words)
            if int(globalClock.getRealTime()) % 60 < 2:
                suits.seedAll(d)          # bots that were online at district-up
            self.__restock(d)
        except Exception:
            d.error('P8b commander', traceback.format_exc())
        return task.again

    def __restock(self, d):
        """A regular standing in a Cog HQ with low laff or an empty pouch goes home to heal / buy gags,
        like a player (Cog HQs have no playground heal and no gag shop). A few per tick."""
        now = globalClock.getRealTime()
        if now - getattr(self, 'lastRestock', 0.0) < 5.0:
            return
        self.lastRestock = now
        n = 0
        for b in list(d.bots.values()):
            if n >= 2:
                break
            if b.area is None or b.area.kind != 'coghq' or not free(b) or now - b.arrivedAt < 20.0:
                continue
            if any(p[1] is b for p in self.pending):
                continue
            if not bb.healthy(b, 0.6) or bb.gagCount(b) < 0.4 * ((b.ownFields.get('setMaxCarry') or (80,))[0]) \
                    or bb.gagCount(b, bb.ATTACK_TRACKS) < 20 or _quality(b) < 15 or not bb.stocked(b):
                b.p8bWantGags = bb.gagCount(b, bb.ATTACK_TRACKS) < 30 or _quality(b) < 20 or not bb.stocked(b)
                home = d.world.areas.get(b.home)
                if home is not None and d.travel(b, home, why='P8b: home to heal / restock', via='teleport'):
                    bb.STATS.count('hq_home_restock')
                    n += 1

    def __readCommands(self, d):
        path = os.path.join(d.runDir, 'bots-coghq.cmd')
        if not os.path.exists(path):
            return
        done = path + '.done'
        try:
            os.replace(path, done)
        except OSError:
            return
        with open(done) as f:
            lines = [l.split('#')[0].strip() for l in f]
        for line in lines:
            if line:
                self.command(d, line.split())

    def command(self, d, words):
        if words[0].lower() == 'census':           # how many regulars could go to each boss right now
            out = {}
            for k, v in cm.BOSSES.items():
                ready = [b for b in d.bots.values() if suits.higherHood(b) and suits.readyForPromotion(b, v['dept'])
                         and suits.hasWholeSuit(b, v['dept'])]
                ok = [b for b in ready if b.state == 'present' and bb.healthy(b, 0.85)
                      and bb.gagCount(b, bb.ATTACK_TRACKS) >= 25]
                out[k] = (len(ready), len(ok), len([b for b in ok if free(b)]))
                prof = []
                for b in ready[:12]:
                    inv, exp = bb.loadInventory(b)
                    top = [max([l for l in range(7) if inv.numItem(t, l) > 0] or [-1]) + 1 for t in bb.ATTACK_TRACKS] \
                        if inv is not None else None
                    prof.append('%s:%s/%s:%s' % (b.home, bb.botHp(b)[0], bb.botHp(b)[1], top))
                cm.notify.info('[TTBOTS-P8b] census %s ready bots (home:laff:top gag per sound/throw/squirt/drop) %s'
                               % (k, ' '.join(prof)))
            hurt = [b for b in d.bots.values() if suits.higherHood(b) and b.state == 'present' and not bb.healthy(b, 0.85)]
            cm.notify.info('[TTBOTS-P8b] census (ready, ready+healthy, +free) %s; higher-hood bots hurt %d' % (out, len(hurt)))
            return
        if words[0].lower() == 'bossruns':          # P8c test switch: bot-only boss runs (off at every start)
            from toontown.bots.activities import boss
            boss.KEEPER.testRuns = len(words) > 1 and words[1].lower() == 'on'
            cm.notify.info('[TTBOTS-P8c] bot-only boss test runs %s' % ('ON' if boss.KEEPER.testRuns else 'off'))
            return
        gather = words[0].lower() == 'gather'      # 'gather vp 6': just bring them to the place (a player's run)
        if gather:
            words = words[1:]
        # 'cfo 8 wait=900 min=7': a group forms only once enough regulars are free and fit (a player waits
        # in the lobby for a full elevator too); checked every 15 s until the deadline
        opts = dict(w.split('=', 1) for w in words if '=' in w)
        words = [w for w in words if '=' not in w]
        waitUntil = float(opts.get('until', 0)) or (globalClock.getRealTime() + float(opts['wait']) if 'wait' in opts else 0)
        kind = words[0].lower()
        n = int(words[1]) if len(words) > 1 else 4
        variant = words[2].lower() if len(words) > 2 else None
        if kind in cm.FACILITIES:
            variant = variant or next(iter(VARIANTS[kind]))
            areaId = cm.FACILITIES[kind]['ext']
            minLaff = VARIANTS[kind][variant][2]
            dept = None
        elif kind in cm.BOSSES:
            from toontown.bots.activities import boss
            if not gather and not boss.KEEPER.testRuns:
                cm.notify.warning('[TTBOTS-P8c] command %s refused: bots never ride a boss elevator without a real '
                                  'toon (test runs: "bossruns on" first)' % words)
                return
            areaId = cm.BOSSES[kind]['lobby']
            minLaff = 0
            dept = cm.BOSSES[kind]['dept']
        else:
            bb.STATS.note('P8b: unknown command %s' % words)
            return
        area = d.world.areas[areaId]
        cands = []
        why = {}
        for b in d.bots.values():
            if not suits.higherHood(b):
                continue
            r = None
            hp, mx = bb.botHp(b)
            if b.state != 'present':
                r = 'offline'
            elif not free(b):
                r = 'busy'
            elif not d.eligible(b, area):
                r = 'no suit'
            elif dept is not None and not suits.readyForPromotion(b, dept):
                r = 'not ready'
            elif hp is None or hp < minLaff or not bb.healthy(b, float(opts.get('hp', 0.85))):
                r = 'laff'
            elif bb.gagCount(b) < 0.5 * ((b.ownFields.get('setMaxCarry') or (80,))[0]) \
                    or bb.gagCount(b, bb.ATTACK_TRACKS) < int(opts.get('gags', 30)) \
                    or _quality(b) < int(opts.get('top', 20)) or not bb.stocked(b):
                r = 'gags'
            why[r] = why.get(r, 0) + 1
            if r is None:
                cands.append(b)
        cm.notify.info('[TTBOTS-P8b] command %s: higher-hood bots by reason %s' % (words, why))
        # a group that goes in picks its strongest free regulars (the ones already here first)
        random.shuffle(cands)
        cands.sort(key=lambda b: (b.area is not area, -(bb.botHp(b)[1] or 0)))
        if kind in cm.FACILITIES and kind != 'factory':
            cands.sort(key=lambda b: -(bb.botHp(b)[1] or 0))      # (a boss test run takes its lobby's bots first)
        pick = withHealers(cands[:n], cands, 2 if kind in cm.BOSSES else (1 if kind != 'factory' else 0))
        least = int(opts.get('min', n))
        if waitUntil and len(pick) < n and (globalClock.getRealTime() < waitUntil or len(pick) < least):
            if globalClock.getRealTime() < waitUntil:
                self.waiting.append((globalClock.getRealTime() + 15.0,
                                     words + ['%s=%s' % kv for kv in opts.items() if kv[0] not in ('wait', 'until')]
                                     + ['until=%f' % waitUntil]))
                return
            cm.notify.warning('[TTBOTS-P8b] command %s: only %d of %d came together in time' % (words, len(pick), least))
            return
        if gather:
            for b in pick:
                if b.area is not area:
                    d.travel(b, area, why='P8b: gather at the %s' % kind, via='teleport')
            bb.STATS.note('P8b gather %s: %d bots' % (kind, len(pick)))
            cm.notify.info('[TTBOTS-P8b] gather %s: %s' % (kind, [b.avId for b in pick]))
            return
        g = makeGroup(d, kind, variant, area)
        if g is None or not pick:
            bb.STATS.note('P8b: command %s: %d bots fit, elevator %s' % (words, len(cands), g is not None))
            cm.notify.warning('[TTBOTS-P8b] command %s: %d candidates, elevator found %s' % (words, len(cands), g is not None))
            if g is not None:
                RUNS.remove(g)
                g.log.finish('no-bots')
            return
        g.expect = len(pick)
        g.skipTo = opts.get('skip')          # TEST runs only: ~boss <skip> once the fight starts
        g.log.note('command %s: %d of %d candidates' % (' '.join(words), len(pick), len(cands)))
        act = FacilityActivity if kind in cm.FACILITIES else _bossActivity()
        for i, b in enumerate(pick):
            role = 'leader' if i == 0 else 'member'
            self.pending.append((g, b, act, role, globalClock.getRealTime() + 120.0))
            if b.area is not area:
                d.travel(b, area, why='P8b: to the %s' % kind, via='teleport')

    def place(self, d, only=None):
        now = globalClock.getRealTime()
        keep = []
        for g, b, act, role, until in self.pending:
            if only is not None and b is not only:
                keep.append((g, b, act, role, until))
                continue
            if b.state != 'present' or now > until:
                g.expect -= 1
                continue
            landed = b.travel is None or getattr(b.travel, 'phase', None) == 'done'
            if not landed or b.area is not g.area:
                keep.append((g, b, act, role, until))
                continue
            g.expect -= 1
            if b.startActivity(act(b, g, role)):
                b.nextTick = min(b.nextTick, now)
                g.log.members = list(g.members)
            else:
                g.log.note('bot %s could not start (%s)' % (b.avId, act.__name__))
        self.pending = keep


COMMANDER = Commander()
