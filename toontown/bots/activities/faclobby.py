"""Sweep fix: nobody to help at the factory. The owner's lobby idea (as activities/boss.py LobbyKeeper does
for the boss lobbies), for the facility elevators: a few ready regulars always WAIT at each facility
elevator (Sellbot factory front / side in the Factory Exterior, the three mints in Cashbot HQ, the four DA
offices in the DA Office Lobby, the three golf karts in Bossbot HQ), so a kid who walks up finds toons there.

  - FacWait: a regular with the kit for that facility (coghq.kitFor) stands 6-16 ft from an elevator whose
    laff minimum it meets, the elevator with the fewest waiters first; interruptible, so when a real toon
    sits down, coghq._elevWatch (unchanged) takes the nearest free bots aboard WITH him.
  - FacLobbyKeeper: keeps WAIT waiters per elevator (the director's target for the area = the waiters + the
    area's bots away inside a facility, like the boss lobbies), and when a real toon walks up to a car that
    bots filled on their own, one bot of that bot-only group steps off for him (requestExit; never a real
    toon). Bot-only runs still happen (coghq.FacilityActivity), but only from bots beyond the waiters
    (spareForRun), so an elevator is never left empty. Battle picks are coghq/level.py's, untouched.
"""
import math
import random

from toontown.bots import BotWorld
from toontown.bots import battlebrain as bb
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.activities.coghq import HQRun, RUNS, VARIANTS, elevatorsIn, kitFor, free
from toontown.bots.BotToon import WALK_SPEED
from toontown.bots.coghq import common as cm

WAIT = 2                    # waiters at a facility's entry elevator (factory front, coin mint, office A, front three)
WAIT_HIGH = 1               # waiters at each higher-laff elevator (the regulars' pool is shared with the HQs)
RUN_SPARE = 2               # a bot-only run may leave only from bots beyond the waiters
WALKUP = 22.0               # a real toon this close to a car full of bots gets a seat
FAC_AREAS = dict((v['ext'], k) for k, v in cm.FACILITIES.items())      # area id -> facility kind
WAITING = {}                # (area id, variant) -> set of waiting bot avIds
STATS = {'waits': 0, 'stepoff': 0, 'blockedRuns': 0}


def want(kind, variant):
    return WAIT if VARIANTS[kind][variant][2] == 0 else WAIT_HIGH


def quota(areaId):
    kind = FAC_AREAS[areaId]
    return sum(want(kind, v) for v in VARIANTS[kind])


BotWorld.AREA_TARGETS.update(dict((a, (quota(a), quota(a) + RUN_SPARE)) for a in FAC_AREAS))


def waitersAt(areaId, variant):
    return WAITING.get((areaId, variant), set())


def spareForRun(area):
    """coghq.FacilityActivity may lead a bot-only run from here only when the elevators keep their waiters."""
    if area is None or area.id not in FAC_AREAS:
        return True
    d = getattr(simbase.air, 'botDirector', None)
    if d is None:
        return True
    here = sum(1 for b in d.bots.values() if b.area is area and b.state == 'present' and b.travel is None
               and b.zoneId in area.zones and not isinstance(b.activity, HQRun))
    if here >= quota(area.id) + RUN_SPARE:
        return True
    STATS['blockedRuns'] += 1
    return False


@register
class FacWait(Activity):
    """A ready regular waits by a facility elevator (so a kid who boards gets company at once)."""
    name = 'facwait'
    weight = 6.0
    kinds = ('coghq',)
    label = 'facility-elevator'

    @classmethod
    def canRun(cls, bot):
        a = bot.area
        if a is None or a.id not in FAC_AREAS or bot.node is None or bot.zoneId not in a.zones:
            return False
        kind = FAC_AREAS[a.id]
        if not kitFor(bot, kind):
            return False
        return cls.pick(bot) is not None

    @staticmethod
    def pick(bot):
        a = bot.area
        kind = FAC_AREAS[a.id]
        hp = bb.botHp(bot)[1] or 0
        opts = [(len(waitersAt(a.id, v)), random.random(), v) for v, t in VARIANTS[kind].items()
                if t[2] <= hp and len(waitersAt(a.id, v)) < want(kind, v)]
        return min(opts)[2] if opts else None

    def start(self):
        bot = self.bot
        a = bot.area
        self.variant = self.pick(bot)
        if self.variant is None:
            return False
        placeName = VARIANTS[FAC_AREAS[a.id]][self.variant][1]
        self.place = next((p for p in a.wm.places() if p['name'] == placeName), None)
        if self.place is None:
            return False
        k = a.nodeNear(self.place['pos'][0], self.place['pos'][1], random.uniform(6.0, 16.0))
        if k is None or not bot.walkTo(k, WALK_SPEED):
            return False
        self.key = (a.id, self.variant)
        WAITING.setdefault(self.key, set()).add(bot.avId)
        self.until = globalClock.getRealTime() + random.uniform(300.0, 700.0)
        STATS['waits'] += 1
        return True

    def step(self, now):
        if self.bot.path:
            return True
        if not getattr(self, 'faced', False):
            self.faced = True
            self.bot.faceTo(self.place['pos'])
        return now < self.until

    def stop(self, why):
        s = WAITING.get(getattr(self, 'key', None))
        if s is not None:
            s.discard(self.bot.avId)
        if self.bot.path and why not in ('travel', 'logout'):
            self.bot.stopWalking()


ENTRY = {'factory': 'front', 'mint': 'coin', 'stage': 'a', 'cgc': 'front'}    # the no-laff-minimum elevator
TASK_WAIT = 240.0           # PROGRESSION: s a toon with a facility ToonTask waits at the elevator asking for help
TASK_HELPERS = 3            # ... toons it waits for (a full elevator of 4: a party of 3 tier-7 toons went sad
TASK_LEAST = 2              #     in 2 of 3 factories in the 09-30 soak); after the wait it goes with 2
ASK_GAP = (20.0, 35.0)      # s between its asks


class TaskFacWait(Activity):
    """PROGRESSION (owner 09-30): "If they need to go to the factory ... they wait outside the factory asking for
    help." A toon with a factory / mint task stands at the entry elevator and asks ("Let's go in the Factory!",
    "Can you help me?", its ToonTask line); the toons standing here (the elevator's waiters, other toons with the
    same task first) come over, and the party boards together (coghq.FacilityActivity, the same run a kid gets).
    Interruptible: a real toon who boards that elevator takes it along (coghq._elevWatch)."""
    name = 'facwait'
    progress = True
    anchored = True
    label = 'facility-task'

    def __init__(self, bot, goal, kind):
        Activity.__init__(self, bot)
        self.goal = goal
        self.kind = kind
        self.variant = ENTRY[kind]
        self.asks = 0
        self.why = ''

    def start(self):
        bot, a = self.bot, self.bot.area
        placeName = VARIANTS[self.kind][self.variant][1]
        self.place = next((p for p in a.wm.places() if p['name'] == placeName), None)
        if self.place is None:
            self.why = 'no elevator place'
            return False
        k = a.nodeNear(self.place['pos'][0], self.place['pos'][1], random.uniform(6.0, 12.0))
        if k is None or not bot.walkTo(k, WALK_SPEED):
            self.why = 'no walk to the elevator'
            return False
        now = globalClock.getRealTime()
        self.t0 = now
        self.until = now + TASK_WAIT
        self.nextAsk = now + random.uniform(4.0, 10.0)
        self.key = (a.id, self.variant)
        WAITING.setdefault(self.key, set()).add(bot.avId)
        STATS['taskWaits'] = STATS.get('taskWaits', 0) + 1
        return True

    def helpers(self):
        """Free toons standing here that can come along: the ones with the same task first, then the rest."""
        bot, d = self.bot, self.director
        out = []
        for b in d.bots.values():
            if b is bot or b.area is not bot.area or b.zoneId not in bot.area.zones or b.pinned or not free(b):
                continue
            if not kitFor(b, self.kind):         # the owner's gag check: stocked and healthy, like any run
                continue
            g = getattr(b, 'goal', None) or {}
            same = g.get('kind') == 'facility' and _kindOf(g) == self.kind
            out.append((0 if same else 1, math.hypot(b.pos[0] - bot.pos[0], b.pos[1] - bot.pos[1]), b))
        out.sort(key=lambda x: (x[0], x[1]))
        return [x[2] for x in out]

    def step(self, now):
        bot = self.bot
        if bot.path:
            return True
        if not getattr(self, 'faced', False):
            self.faced = True
            bot.faceTo(self.place['pos'])
        if now >= self.nextAsk:
            self.nextAsk = now + random.uniform(*ASK_GAP)
            self.__ask(now)
            self.asks += 1
        mates = self.helpers()
        # one more toon comes each time it asks (as the building leader's recruit does); enough -> go
        ready = mates[:min(TASK_HELPERS, self.asks)]
        if len(ready) >= TASK_HELPERS or (now >= self.until and len(ready) >= TASK_LEAST):
            return self.__go(ready)
        if now >= self.until:
            from toontown.bots import progress
            progress.failed(bot, 'nobody came to the %s' % self.kind)
            progress.log(bot, 'FACWAIT %s: nobody came in %.0f s' % (self.kind, now - self.t0))
            return False
        return True

    def __ask(self, now):
        from toontown.bots import progress
        r = self.asks % 3
        if r == 0:
            kit.say(self.bot, 1116 if self.kind == 'factory' else 514, now, force=True)    # "Let's go in the Factory!"
        elif r == 1:
            kit.say(self.bot, 514, now, force=True)                                       # "Can you help me?"
        else:
            progress.sayTask(self.bot, self.goal.get('qid'))

    def __go(self, mates):
        from toontown.bots import progress
        from toontown.bots.activities import coghq
        bot, d = self.bot, self.director
        g = coghq.makeGroup(d, self.kind, self.variant, bot.area)
        if g is None:
            self.why = 'no elevator'
            return False
        progress.log(bot, 'FACWAIT %s: going in with %s after %.0f s' % (
            self.kind, [b.avId for b in mates], globalClock.getRealTime() - self.t0))
        STATS['taskRuns'] = STATS.get('taskRuns', 0) + 1
        self.went = True
        lead = coghq.FacilityActivity(bot, g, 'leader')
        lead.taskGoal = self.goal
        lead.progress = lead.anchored = True
        if not bot.startActivity(lead):          # this activity ends ('replaced')
            if g in coghq.RUNS:
                coghq.RUNS.remove(g)
            return False
        for b in mates:
            if b.startActivity(coghq.FacilityActivity(b, g)):
                b.nextTick = min(b.nextTick, globalClock.getRealTime())
        g.log.members = list(g.members)
        g.log.note('ToonTask party: %s asked for help, %s came' % (bot.avId, [b.avId for b in mates]))
        return True

    def stop(self, why):
        s = WAITING.get(getattr(self, 'key', None))
        if s is not None:
            s.discard(self.bot.avId)
        if self.bot.path and why not in ('travel', 'logout', 'replaced'):
            self.bot.stopWalking()


def _kindOf(goal):
    k = goal.get('facility')
    return 'stage' if k == 'da' else k


class FacLobbyKeeper:
    """Keeps the waiters topped up (the director's target) and gives a walking-up real toon a seat."""

    def __init__(self):
        self.lastLog = 0.0
        self.stepOffAt = {}     # elevator doId -> last step-off
        taskMgr.doMethodLater(1.0, self.__tick, 'bots-facility-lobby-keeper')

    def __tick(self, task):
        d = getattr(simbase.air, 'botDirector', None)
        try:
            if d is not None and d.world is not None and d.air.districtId:
                now = globalClock.getRealTime()
                for areaId, kind in FAC_AREAS.items():
                    area = d.world.areas.get(areaId)
                    if area is not None:
                        self.__area(d, area, kind, now)
                if now - self.lastLog > 60.0:
                    self.lastLog = now
                    cm.notify.info('[TTBOTS-SWEEP] facility waiters %s; stats %s' % (
                        dict(('%s/%s' % k, len(v)) for k, v in WAITING.items() if v), STATS))
        except Exception:
            import traceback
            if d is not None:
                d.error('sweep facility lobby keeper', traceback.format_exc())
        return task.again

    def __area(self, d, area, kind, now):
        # the waiters + whoever of this area is away inside a facility (so the director neither refills short
        # nor pulls the waiters out while a run is on)
        away = sum(1 for b in d.bots.values() if b.area is area and b.state == 'present' and b.zoneId not in area.zones)
        q = quota(area.id) + RUN_SPARE + away
        if getattr(area, 'baseTarget', None) != q:
            area.baseTarget = area.target = q
            area.max = max(area.max, q + RUN_SPARE)
        if not area.players:
            return
        view = d.viewOf(area.id)
        if view is None:
            return
        for e in elevatorsIn(d, area, cm.FACILITIES[kind]['elevClass']):
            if cm.elevState(e) not in cm.BOARDABLE:
                continue
            seats = cm.seats(e, 4)
            if any(not s for s in seats) or any(s not in d.bots for s in seats):
                continue           # a free seat, or a real toon already aboard (then _elevWatch made it his group)
            near = []
            for a in area.players:
                o = view.objects.get(a)
                if o is not None and math.hypot(o.pos[0] - e.pos[0], o.pos[1] - e.pos[1]) < WALKUP:
                    near.append(a)
            if not near:
                continue
            # a car full of bots on their own run, a kid walking up: one bot (not the leader) steps off for him
            for s in seats:
                b = d.bots.get(s)
                act = b.activity if b is not None else None
                if isinstance(act, HQRun) and not act.group.withPlayer and act.role != 'leader' \
                        and act.phase in ('boarding', 'seated'):
                    if now - self.stepOffAt.get(e.doId, 0.0) < 8.0:
                        break
                    self.stepOffAt[e.doId] = now
                    STATS['stepoff'] += 1
                    act.group.log.note('bot %s steps off the full %s car for real toon(s) %s' % (s, kind, near))
                    cm.notify.info('[TTBOTS-SWEEP] %s elevator %s: bot %s steps off for real toon(s) %s' % (
                        kind, e.doId, s, near))
                    b.endActivity('step off for a real toon')
                    break


KEEPER = FacLobbyKeeper()
