"""P8 COG BUILDINGS: a group of 1-4 bots (and him, when he boards) takes a Cog building back, like players.

  leader    a healthy bot with gags on a street with a Cog building picks it, calls 1-3 bots from the
            street (they run over), waits at the door until they are there, and they all board the
            building's elevator (DistributedElevatorExt.requestBoard; 15 s countdown).
  member    a bot called by a leader, or by a REAL player boarding a Cog building elevator on his
            street (the elevator's fillSlotN shows his avId): runs over and boards within the countdown.
Inside, every client barrier is acked as the client does (DistributedSuitInterior):
  setAvatarJoined  once the interior shows up (the AI has NO timer on it: a bot inside must send it)
  elevatorDone     after the elevator ride (4 + 2 s; the AI waits 8 s)
  reserveJoinDone  after the reserves walk out (2 + 1 s; the AI waits 5 s)
  each floor's battle is the shared brain (toontown/bots/battlebrain.py, kind 'bldg')
  between floors   board the interior elevator (DistributedElevatorInt.requestBoard) after a few s
  top floor        back out on the street at the elevator door, then DistributedBuilding.setVictorReady
                   (NO timer on the AI either) -> the building turns back into a toon building.
A bot that goes sad inside teleports to its playground like a player; the rest fight on.
"""
import math
import random

from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots import battlebrain as bb
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED

QUIET = OTPGlobals.QuietZone
MAX_RUNS = 3                 # bot-led building runs on the district at once
TASK_WAIT = 180.0            # PROGRESSION: s a bot with a building task waits at the door asking for help
HELPERS = {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}     # ... toons it waits for, by floors
SOLO_FLOORS = 2              # ... it goes in alone after the wait only up to this tall
BOARDABLE = ('waitEmpty', 'waitCountdown')
_VIEWS = {}                  # street branch zone -> view (DistributedBuilding lives there)


def branchView(director, area):
    v = _VIEWS.get(area.id)
    if v is None and director.air.districtId:
        v = director.air.openView(director.air.districtId, area.id)
        _VIEWS[area.id] = v
    return v


def suitBuildings(director, area):
    """[(bldgObj, elevatorObj, doorPlace, numFloors)] Cog buildings on this street with a live elevator."""
    v = branchView(director, area)
    if v is None:
        return []
    elevs = {}
    for z in area.zones:
        zv = director.viewOf(z)
        if zv is None:
            continue
        for o in zv.objects.values():
            if o.className == 'DistributedElevatorExt':
                elevs[(o.get('setBldgDoId') or (0,))[0]] = o
    out = []
    for b in v.objects.values():
        if b.className != 'DistributedBuilding' or (b.get('setState') or ('',))[0] != 'suit':
            continue
        e = elevs.get(b.doId)
        blk = (b.get('setBlock') or (None,))[0]
        door = next((p for p in area.doors if p['extra'].get('block') == blk), None)
        if e is None or door is None:
            continue
        out.append((b, e, door, (b.get('setSuitData') or (0, 0, 1))[2]))
    return out


def seats(elev):
    out = []
    for i in range(4):
        f = elev.get('fillSlot%d' % i)
        out.append(f[0] if f else 0)
    return out


class Group:
    def __init__(self, bldg, elev, door, floors):
        self.bldg, self.elev, self.door, self.floors = bldg, elev, door, floors
        self.leader = None
        self.members = []
        self.go = False
        self.withPlayer = False
        self.reporter = None
        self.t0 = globalClock.getRealTime()


RUNS = {}                    # building doId -> Group (bot-led or joining him)


def wantsThis(b, g, areaId):
    """PROGRESSION: a toon whose own task this building helps - a building task, or a visit to an NPC inside it."""
    goal = getattr(b, 'goal', None) or {}
    if goal.get('kind') == 'building':
        return True
    return goal.get('kind') == 'visit' and goal.get('area') == areaId and         goal.get('block') == (g.bldg.get('setBlock') or (None,))[0]


@register
class CogBuilding(Activity):
    name = 'cogbuilding'
    progress = True          # PROGRESSION (owner 1a): none starts while no real player is online
    weight = 0.5
    kinds = ('street',)
    label = 'cog-building'

    @classmethod
    def canRun(cls, bot):
        d = bot.director
        if bot.area is None or bot.node is None or not d.clockSynced or not d.air.districtId:
            return False
        if globalClock.getRealTime() < getattr(bot, 'p8NextBldg', 0.0):
            return False
        now = globalClock.getRealTime()
        for k in [k for k, g in RUNS.items() if now - g.t0 > 1800.0]:
            del RUNS[k]
        if len(RUNS) >= MAX_RUNS:
            return False
        # the owner (09-25): nobody starts a building without a gag check - stocked up like a normal player
        if not (bb.healthy(bot, 0.75) and bb.stocked(bot)):
            return False
        return bool(suitBuildings(d, bot.area))

    @classmethod
    def canRunFor(cls, bot, want):
        """PROGRESSION: a bot with a building ToonTask: a building its task counts on this street, stocked and
        healthy like any run (the owner's gag check), a free run slot."""
        d = bot.director
        if bot.area is None or bot.node is None or not d.clockSynced or not d.air.districtId:
            return False
        if len(RUNS) >= MAX_RUNS or not (bb.healthy(bot, 0.75) and bb.stocked(bot)):
            return False
        return any(want(x[0], x[3]) and x[0].doId not in RUNS for x in suitBuildings(d, bot.area))

    def __init__(self, bot, role='leader', group=None, task=None, want=None):
        Activity.__init__(self, bot)
        self.role = role
        self.group = group
        self.task = task           # PROGRESSION: the building ToonTask goal of a leader (waits at the door, asks for help)
        self.want = want           # fn(bldgObj, floors) -> the task counts this building
        self.coming = {}           # PROGRESSION: avId -> until, toons teleporting over to help with the task
        self.nextAsk = 0.0
        self.phase = 'pick'
        self.until = 0.0
        self.brain = None
        self.interior = None       # DistributedSuitInterior view object
        self.intView = None
        self.intZone = None
        self.intElev = None
        self.seat = None
        self.floor = 0
        self.watching = []
        self.boardAt = 0.0
        self.boarded = False
        self.label = 'to-cog-building'

    # -- start ---------------------------------------------------------------------------------------------
    def start(self):
        bb.HUB.ensure(self.director)
        bot, d = self.bot, self.director
        bot.p8NextBldg = globalClock.getRealTime() + random.uniform(120.0, 240.0)
        if self.role == 'leader':
            blds = [x for x in suitBuildings(d, bot.area) if x[0].doId not in RUNS
                    and (x[1].get('setState') or ('',))[0] in BOARDABLE
                    and sum(1 for s in seats(x[1]) if s) == 0 and d.canWalkTo(bot, x[2])
                    and (self.want is None or self.want(x[0], x[3]))]
            if not blds:
                return False
            # the tallest one near (a group that goes in picks a worthwhile building); a toon on a building task
            # picks the smallest one its task counts (fewer floors = fewer helpers to wait for), as a player does
            if self.task is not None:
                blds.sort(key=lambda x: (x[3], math.hypot(x[2]['pos'][0] - bot.pos[0], x[2]['pos'][1] - bot.pos[1])))
                b, e, door, floors = blds[0]
            else:
                blds.sort(key=lambda x: (-x[3], math.hypot(x[2]['pos'][0] - bot.pos[0], x[2]['pos'][1] - bot.pos[1])))
                b, e, door, floors = blds[0] if random.random() < 0.7 else random.choice(blds)
            if not self.claim(('bldg', b.doId)):
                return False
            self.group = Group(b, e, door, floors)
            self.group.leader = bot.avId
            RUNS[b.doId] = self.group
            if self.task is None:
                self.__recruit(random.choice((1, 2, 3, 3)))
            else:
                self.__recruit(1)            # the rest come when it asks at the door (step 'atdoor')
            bb.STATS.note('bot %s leads a run on %d-story building %s (block %s) with %s' % (
                bot.avId, floors, b.doId, door['extra'].get('block'), self.group.members))
            if random.random() < 0.5:
                kit.say(bot, random.choice((1103, 1104)))
        g = self.group
        if bot.avId not in g.members and bot.avId != g.leader:
            g.members.append(bot.avId)
        if not bot.walkTo(bot.area.placeNode(g.door), RUN_SPEED, 'run'):
            if self.role == 'leader':
                RUNS.pop(g.bldg.doId, None)
            return False
        self.phase = 'walk'
        self.until = globalClock.getRealTime() + 45.0
        bb.HUB.watch(g.elev.doId, self.__elevField)
        self.watching.append(g.elev.doId)
        return True

    def __askForHelp(self, now, g):
        """PROGRESSION: the task's building. It asks aloud every so often (\"Let's go take over a Cog building!\",
        \"Can you help me?\", its ToonTask line) and one more toon comes each time - a toon with a building task on
        this street first. In it goes with enough help for the floors, or with whoever came when the wait is up
        (alone only for a small building); with nobody for a tall one it gives up (progress.failed)."""
        from toontown.bots import progress
        here = [a for a in g.members if self.__nearDoor(a)]
        enough = HELPERS.get(min(g.floors, 5), 3)
        if len(here) >= enough and len(here) >= len(g.members):
            g.go = True
            kit.say(self.bot, 1104)                                      # "Let's go in the elevator!"
            return
        if now >= self.nextAsk:
            self.nextAsk = now + random.uniform(14.0, 24.0)
            r = random.random()
            if r < 0.45:
                kit.say(self.bot, 1103, force=True)                      # "Let's go take over a Cog building!"
            elif r < 0.75:
                kit.say(self.bot, 514, force=True)                       # "Can you help me?"
            else:
                progress.sayTask(self.bot, self.task.get('qid'))
            if len(g.members) + len(self.coming) < enough:
                self.__recruit(1)
        self.__arrivals(now, g)
        if now > self.until - 6.0:
            # nobody came: a toon tries its task's building alone, as a player does after asking for a while (its
            # task wants that many floors: a Melodyland 3-floor task with nobody in the hood failed every wait)
            # with whoever came only when the building fits the help: each toon that came carries one more floor
            # (the 09-30 advance soak: two 35-laff toons went into a 4-floor Cashbot building, sad 6 times in a row)
            alone = max(SOLO_FLOORS, self.task.get('floors') or 1)
            if g.floors <= alone + len(here):
                g.go = True
            else:
                progress.failed(self.bot, 'nobody came to help with a %d-floor building' % g.floors)
                self.until = now

    def __recruit(self, n):
        d, bot, g = self.director, self.bot, self.group
        cands = []
        for b in d.bots.values():
            if b is bot or b.area is not bot.area or b.state != 'present' or b.travel is not None or b.node is None:
                continue
            if b.activity is not None and (not b.activity.interruptible or b.activity.name in ('battle', 'cogbuilding')):
                continue
            if not (bb.healthy(b, 0.6) and bb.stocked(b)):
                continue
            if not d.canWalkTo(b, g.door):
                continue
            dist = math.hypot(b.pos[0] - g.door['pos'][0], b.pos[1] - g.door['pos'][1])
            if self.task is not None and wantsThis(b, g, bot.area.id):
                dist -= 1000.0               # a toon with a building task of its own comes first
            if dist < 400.0:
                cands.append((dist, b))
        cands.sort(key=lambda c: c[0])
        for dist, b in cands[:n]:
            if b.startActivity(CogBuilding(b, role='member', group=g)):
                b.nextTick = min(b.nextTick, globalClock.getRealTime())
        if self.task is not None and not cands:
            self.__callFar(n)

    def __callFar(self, n):
        """PROGRESSION: nobody on this street - a toon elsewhere in the hood hears the call and teleports over (as a
        friend does), a toon with a building task of its own first."""
        d, bot, g = self.director, self.bot, self.group
        cands = []
        for b in d.bots.values():
            if b is bot or b.area is None or b.area is bot.area or b.area.hood != bot.area.hood or b.avId in self.coming:
                continue
            if b.state != 'present' or b.travel is not None or b.node is None or b.pinned or getattr(b, 'crew', False):
                continue
            if b.activity is not None and (not b.activity.interruptible or
                                           b.activity.name in ('battle', 'cogbuilding', 'coghq', 'facwait')):
                continue
            if not (bb.healthy(b, 0.6) and bb.stocked(b)) or not d.eligible(b, bot.area):
                continue
            mine = wantsThis(b, g, bot.area.id)
            cands.append((0 if mine else 1, random.random(), b))
        cands.sort(key=lambda c: (c[0], c[1]))
        from toontown.bots import progress
        if cands or not getattr(self, 'saidNone', False):
            self.saidNone = not cands
            progress.log(bot, 'BLDGCALL %d-floor: %d toons in the hood could come%s' % (
                g.floors, len(cands), ', %s teleports over' % [c[2].avId for c in cands[:n]] if cands else ''))
        for _, _, b in cands[:n]:
            if d.travel(b, bot.area, why='PROGRESSION: helps %s with a building task' % bot.avId, via='teleport'):
                self.coming[b.avId] = globalClock.getRealTime() + 60.0
                bb.STATS.count('bldg_task_called_far')

    def __arrivals(self, now, g):
        """The toons that teleported over for the task join the group once they land here."""
        for avId, until in list(self.coming.items()):
            b = self.director.bots.get(avId)
            if b is None or b.state != 'present' or now > until:
                del self.coming[avId]
                continue
            landed = b.travel is None or getattr(b.travel, 'phase', None) == 'done'
            if not landed or b.area is not self.bot.area or b.node is None:
                continue
            del self.coming[avId]
            if b.startActivity(CogBuilding(b, role='member', group=g)):
                b.nextTick = min(b.nextTick, now)
                bb.STATS.count('bldg_task_far_joined')

    # -- the elevator / interior / building broadcasts --------------------------------------------------------
    def __elevField(self, obj, fieldName, args):
        me = self.bot.avId
        if fieldName == '__exit__':
            return
        if fieldName.startswith('fillSlot') and args[0] == me:
            self.seat = int(fieldName[-1])
            self.boarded = True
        elif fieldName.startswith('fillSlot') and self.seat is not None and fieldName == 'fillSlot%d' % self.seat \
                and args[0] == 0 and self.phase == 'seated':
            self.__ride()
        elif fieldName.startswith('emptySlot') and args[0] == me:
            self.seat = None
            self.boarded = False

    def __intField(self, obj, fieldName, args):
        if fieldName == '__exit__':
            if self.phase in ('inside', 'joined'):
                self.phase = 'gone'
            return
        if fieldName == 'setState':
            self.__intState(args[0])

    def __intElevField(self, obj, fieldName, args):
        me = self.bot.avId
        if fieldName.startswith('fillSlot') and args[0] == me:
            self.boarded = True
        elif fieldName == 'forcedExit':
            pass

    def __bldgField(self, obj, fieldName, args):
        if fieldName == 'setState' and self.phase == 'victor' and args[0] != 'waitForVictors':
            self.until = 0.0

    def __watch(self, doId, fn):
        bb.HUB.watch(doId, fn)
        self.watching.append((doId, fn))

    # -- the tick -----------------------------------------------------------------------------------------------
    def step(self, now):
        bot, g = self.bot, self.group
        ph = self.phase
        if ph == 'walk':
            if bot.path:
                if now > self.until:
                    bot.stopWalking()
                    return False
                return True
            bot.faceTo(g.door['pos'])
            self.phase = 'atdoor'
            self.until = now + (30.0 if self.role == 'leader' else 40.0)
            if self.task is not None and self.role == 'leader':
                self.until = now + TASK_WAIT          # the owner 09-30: waits outside the building asking for help
                g.taskUntil = self.until
            elif self.role == 'member':
                # a helper waits as long as the toon that asked (the 09-30 advance soak: helpers left after 40 s,
                # so a 4-floor task building never had two of them at the door at once)
                self.until = max(self.until, getattr(g, 'taskUntil', 0.0) + 5.0)
            return True
        if ph == 'atdoor':
            st = (g.elev.get('setState') or ('',))[0]
            v = self.director.viewOf(g.elev.zoneId)
            if v is None or g.elev.doId not in v.objects or (g.bldg.get('setState') or ('',))[0] != 'suit':
                return False
            g.elev = v.objects[g.elev.doId]
            if self.role == 'leader' and not g.go and self.task is not None:
                self.__askForHelp(now, g)
            elif self.role == 'leader' and not g.go:
                here = [a for a in g.members if self.__nearDoor(a)]
                if len(here) >= len(g.members) or now > self.until - 12.0:
                    g.go = True
            if (g.go or g.withPlayer or any(seats(g.elev))) and st in BOARDABLE:
                if not self.boardAt:
                    self.boardAt = now + random.uniform(0.5, 2.5)
                if now >= self.boardAt:
                    bot.send('requestBoard', [], doId=g.elev.doId, className='DistributedElevatorExt')
                    self.phase = 'boarding'
                    self.until = now + 5.0
                    self.interruptible = False
            elif now > self.until:
                return False
            return True
        if ph == 'boarding':
            if self.boarded:
                self.phase = 'seated'
                self.label = 'cog-building'
                bot.stopWalking()
                bb.STATS.count('bldg_boarded')
                if not bb.stocked(bot):
                    bb.STATS.count('bldg_boarded_unstocked')      # the owner's gag check (09-25): must stay 0
                inv, exp = bb.loadInventory(bot)
                if inv is not None:
                    from toontown.bots import gagplan
                    gagplan.log(self.director, 'GAGCHECK bot %s boarded building %s: %d gags of %d it can carry (%s)' % (
                        bot.avId, self.group.bldg.doId if getattr(self.group, 'bldg', None) is not None else '?',
                        bb.gagCount(bot), bb.pouchRoom(inv, exp), 'stocked' if bb.stocked(bot) else 'NOT STOCKED'))
                self.until = now + 60.0
            elif now > self.until:
                self.interruptible = True
                return False
            return True
        if ph == 'seated':
            if not self.boarded:               # emptied (someone left / the elevator gave up)
                self.interruptible = True
                return False
            st = (g.elev.get('setState') or ('',))[0]
            if st == 'closed' or now > self.until:
                self.__ride()
            return True
        if ph == 'ride':
            if now >= self.until:
                self.__arriveInside(now)
            return True
        if ph == 'inside':
            o = self.intView.first('DistributedSuitInterior') if self.intView is not None else None
            if o is not None:
                self.interior = o
                self.__watch(o.doId, self.__intField)
                self.phase = 'joined'
                self.joinAt = now + random.uniform(1.0, 2.5)
                self.__need(('joined',))
                return True
            if now > self.until:
                return self.__bail('no interior')
            return True
        if ph == 'joined':
            if self.joinAt and now >= self.joinAt:
                self.joinAt = 0.0
                self.__send('setAvatarJoined')
                self.__acked(('joined',))
                g2 = sorted(set([g.leader] + g.members) - {None})
                if g.reporter is None:
                    g.reporter = self.bot.avId
                    bb.STATS.count('bldg_entered')
                    if not g.withPlayer:
                        bb.STATS.count('bldg_entered_botonly')
                    bb.STATS.note('building %s entered (%d floors) by %s' % (g.bldg.doId, g.floors, g2))
            self.__battleStep(now)
            return True
        if ph == 'victor':
            if now >= self.until:
                self.interruptible = True
                k = bot.area.nodeNear(bot.pos[0], bot.pos[1], 15.0)
                if k is not None:
                    bot.walkTo(k, WALK_SPEED)
                return False
            return True
        if ph == 'gone':
            return self.__bail('interior gone')
        return False

    def __nearDoor(self, avId):
        b = self.director.bots.get(avId)
        g = self.group
        return b is not None and b.zoneId is not None and \
            math.hypot(b.pos[0] - g.door['pos'][0], b.pos[1] - g.door['pos'][1]) < 20.0

    def __ride(self):
        if self.phase != 'seated':
            return
        g = self.group
        self.intZone = (g.bldg.get('setBlock') or (0, 0))[1]
        self.intView = self.bot.air.openView(self.bot.air.districtId, self.intZone)
        self.phase = 'ride'
        self.until = globalClock.getRealTime() + random.uniform(1.5, 3.0)     # the client loads the interior
        self.bot.relocate(QUIET)
        self.__watch(g.bldg.doId, self.__bldgField)

    def __arriveInside(self, now):
        bot = self.bot
        bot.path = []
        bot.relocate(self.intZone)
        self.phase = 'inside'
        self.until = now + 20.0

    # -- inside -------------------------------------------------------------------------------------------------
    def __record(self):
        return bb.HUB.record(self.interior.doId, 'interior') if self.interior is not None else None

    def __need(self, key):
        r = self.__record()
        if r is not None:
            r.need(key, self.bot.avId)

    def __acked(self, key):
        r = self.__record()
        if r is not None:
            r.acked(key, self.bot.avId)

    def __send(self, field, args=None):
        self.bot.send(field, args or [], doId=self.interior.doId, className='DistributedSuitInterior')

    def __intState(self, st):
        r = self.__record()
        prev = getattr(self, '_intPrev', None)
        self._intPrev = st
        if r is not None:
            if prev == 'WaitForAllToonsInside' or st == 'Elevator':
                r.closePrefix('joined')
            if prev == 'Elevator' and st != 'Elevator':
                r.closePrefix('elev')
            if prev == 'ReservesJoining' and st != 'ReservesJoining':
                r.closePrefix('reserve')
        if self.phase != 'joined':
            return
        me = self.bot.avId
        if st == 'Elevator':
            self.floor += 1
            self.__need(('elev', self.floor))
            self.__later(6.0 + random.uniform(0.1, 0.8), self.__elevatorDone, self.floor)
        elif st == 'ReservesJoining':
            self.__need(('reserve', self.floor))
            self.__later(3.0 + random.uniform(0.1, 0.7), self.__reserveDone, self.floor)
        elif st == 'Resting':
            if self.group.reporter == me:
                bb.STATS.count('bldg_floors')
            self.intElev = None
        elif st == 'Reward':
            if self.group.reporter == me:
                bb.STATS.count('bldg_floors')
            self.__victory()

    def __later(self, delay, fn, *args):
        def run(task):
            if not self.stopped and self.phase == 'joined':
                try:
                    fn(*args)
                except Exception:
                    import traceback
                    bb.HUB.error('cogbuilding %s' % fn.__name__, traceback.format_exc())
            return task.done
        taskMgr.doMethodLater(delay, run, 'botbldg-%d-%d' % (self.bot.avId, random.randint(0, 1 << 30)))

    def __elevatorDone(self, floor):
        if (self.interior.get('setState') or ('',))[0] == 'Elevator':
            self.__send('elevatorDone')
            self.__acked(('elev', floor))

    def __reserveDone(self, floor):
        if (self.interior.get('setState') or ('',))[0] == 'ReservesJoining':
            self.__send('reserveJoinDone')
            self.__acked(('reserve', floor))

    def __battleStep(self, now):
        bot = self.bot
        st = (self.interior.get('setState') or ('',))[0]
        if self.brain is not None:
            if not self.brain.step(now):
                res = self.brain.result
                self.brain = None
                if res == 'died':
                    self.__bail('sad')
            return
        if st == 'Battle' or st == 'ReservesJoining':
            for o in self.intView.objects.values():
                if o.className == 'DistributedBattleBldg' and bot.avId in ((o.get('setMembers') or [[]] * 7)[6]) \
                        and o.doId != getattr(self, 'lastBattle', None):
                    self.lastBattle = o.doId
                    self.brain = bb.BattleBrain(bot, 'bldg')
                    self.brain.attach(o)
                    break
        elif st == 'Resting':
            e = self.intElev
            if e is None:
                e = self.intView.first('DistributedElevatorInt')
                if e is not None:
                    self.intElev = e
                    self.boarded = False
                    self.__watch(e.doId, self.__intElevField)
                    real = any(s and s not in self.director.bots for s in seats(e))
                    self.boardAt = now + (random.uniform(2.0, 5.0) if real else random.uniform(4.0, 10.0))
                    self.boardSent = False
            elif not self.boardSent and now >= self.boardAt and (e.get('setState') or ('',))[0] in BOARDABLE:
                self.boardSent = True
                bot.send('requestBoard', [], doId=e.doId, className='DistributedElevatorInt')
            elif self.boardSent and not self.boarded and now > self.boardAt + 8.0:
                self.boardSent = False          # try again (the doors were still opening)
                self.boardAt = now + 1.0

    def __victory(self):
        """Top floor won: back out on the street at the elevator, then setVictorReady (no AI timer)."""
        bot, g = self.bot, self.group
        toons = [t for t in (self.interior.get('setToons') or ([],))[0] if t]
        real = [t for t in toons if t not in self.director.bots]
        if not getattr(g, 'counted', False):
            g.counted = True
            bb.STATS.count('bldg_cleared')
            if not real and not g.withPlayer:
                bb.STATS.count('bldg_cleared_botonly')
                bb.STATS.c['bldg_tallest'] = max(bb.STATS.c.get('bldg_tallest', 0), g.floors)
            bb.STATS.note('building %s (%d floors) CLEARED by %s' % (
                g.bldg.doId, g.floors, (self.interior.get('setToons') or ([],))[0]))
        self.phase = 'victor'
        self.__closeInterior()
        node = bot.area.placeNode(g.door)
        x, y, z = g.door['pos']
        p = bot.area.wm.pos(node)
        bot.relocate(QUIET)
        bot.node = node
        bot.pos = Point3(*p)
        bot.faceTo((2 * p[0] - x, 2 * p[1] - y))
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(bot.director.world.doorZone(bot.area, g.door))
        rec = bb.HUB.record(g.bldg.doId, 'building')
        rec.need(('victor',), bot.avId)
        delay = random.uniform(2.0, 4.0)

        def ready(task):
            if bot.state == 'present':
                bot.send('setVictorReady', [], doId=g.bldg.doId, className='DistributedBuilding')
                rec.acked(('victor',), bot.avId)
            return task.done
        taskMgr.doMethodLater(delay, ready, 'botvictor-%d' % bot.avId)
        self.until = globalClock.getRealTime() + delay + 12.0

    def __closeInterior(self):
        if self.intView is not None:
            self.bot.air.closeView(self.intView)
            self.intView = None

    def __bail(self, why):
        """Out of the building without a win (sad, the building gone): home to the playground."""
        bb.STATS.count('bldg_bail_' + why.replace(' ', '_'))
        self.__closeInterior()
        self.interruptible = True
        if self.bot.zoneId != self.bot.director.world.doorZone(self.bot.area, self.group.door):
            bb.HUB.goHeal(self.bot, 'sad' if why == 'sad' else 'bldg')
        return False

    # -- plumbing -----------------------------------------------------------------------------------------------
    def onDirect(self, fieldName, args):
        if fieldName == 'rejectBoard' and self.phase == 'boarding':
            self.until = 0.0
        elif fieldName == 'forcedExit':
            self.phase = 'gone'
        if self.brain is not None:
            self.brain.onDirect(fieldName, args)

    def onHeard(self, speaker, msgId):
        return False

    def stop(self, why):
        g = self.group
        for w in self.watching:
            if isinstance(w, tuple):
                bb.HUB.unwatch(*w)
            else:
                bb.HUB.unwatch(w, self.__elevField)
        self.watching = []
        if self.brain is not None and self.brain.phase != 'done':
            self.brain.leave(why)
        if self.phase in ('boarding', 'seated') and why not in ('logout', 'district down') and self.bot.state == 'present':
            self.bot.send('requestExit', [], doId=g.elev.doId, className='DistributedElevatorExt')
        self.__closeInterior()
        if g is not None and self.bot.avId == g.leader:
            RUNS.pop(g.bldg.doId, None)
        elif g is not None and g.leader is None and not any(
                b.activity is not None and b.activity.name == 'cogbuilding' and b is not self.bot
                and getattr(b.activity, 'group', None) is g for b in self.director.bots.values()):
            RUNS.pop(g.bldg.doId, None)
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()


# ---- him boarding a Cog building elevator: bots come along ----------------------------------------------------
def _elevWatch(obj, fieldName, args):
    if not fieldName.startswith('fillSlot') or not args[0]:
        return
    d = bb.HUB.director
    avId = args[0]
    if avId in d.bots:
        return
    area = d.world.zoneToArea.get(obj.zoneId)
    if area is None or area.kind != 'street':
        return
    bldgId = (obj.get('setBldgDoId') or (0,))[0]
    blds = [x for x in suitBuildings(d, area) if x[0].doId == bldgId]
    if not blds:
        return
    g = RUNS.get(bldgId)
    if g is not None:
        g.withPlayer = True
        g.go = True
        return
    b, e, door, floors = blds[0]
    g = Group(b, e, door, floors)
    g.withPlayer = True
    g.go = True
    RUNS[bldgId] = g
    want = random.choice((1, 2, 3, 3))
    now = globalClock.getRealTime()
    cands = []
    for bot in d.bots.values():
        if bot.area is not area or bot.state != 'present' or bot.travel is not None or bot.node is None:
            continue
        if bot.activity is not None and (not bot.activity.interruptible or bot.activity.name in ('battle', 'cogbuilding')):
            continue
        if not (bb.healthy(bot, 0.5) and bb.stocked(bot)) or not d.canWalkTo(bot, door):
            continue
        dist = math.hypot(bot.pos[0] - door['pos'][0], bot.pos[1] - door['pos'][1])
        if dist < 180.0:
            cands.append((dist, bot))
    cands.sort(key=lambda c: c[0])
    sent = []
    for dist, bot in cands[:want]:
        if bot.startActivity(CogBuilding(bot, role='member', group=g)):
            bot.nextTick = min(bot.nextTick, now)
            sent.append(bot.avId)
    if not sent:
        RUNS.pop(bldgId, None)
    bb.STATS.count('bldg_player_boarded')
    bb.STATS.note('real player %s boarded building %s (%d floors): bots %s come along' % (avId, bldgId, floors, sent))


bb.HUB.classHooks.setdefault('DistributedElevatorExt', []).append(_elevWatch)
