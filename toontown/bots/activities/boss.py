"""P8b/P8c BOSS BATTLES: regulars whose suits are ready for promotion ride the lobby elevator (VP, CFO, CJ,
CEO) with a real toon and fight the boss, like players (the fight itself: toontown/bots/coghq/bosses.py).

The elevator is the lobby's DistributedBossElevator: the AI's own checkBoard (readyForPromotion, laff)
decides, as for a player; a bot that is not ready never tries. 8 seats, 30 s countdown (a full boss
elevator still waits for its countdown: DistributedElevatorExtAI.timeToGoTask).
The owner's lobby rules (LobbyKeeper below): 8 ready bots always wait at each boss elevator; a real toon
who sits down gets 7 bots with him at once; a real toon who walks up to the full elevator gets a bot's seat
(only a bot ever steps off); the fight always starts with 8 toons; bots NEVER ride a boss elevator
without a real toon (a bot-only boss run is a test command only: 'bossruns on' in bots-coghq.cmd, off at
every start); the director refills each lobby to 8 (BotWorld.AREA_TARGETS).
"""
import random

from toontown.bots import battlebrain as bb
from toontown.bots.activities import register
from toontown.bots.activities.coghq import BOSS_LAFF, HQRun, RUNS, fit, free, healer, makeGroup
from toontown.bots.coghq import common as cm
from toontown.bots.coghq import suits
from toontown.bots.coghq.bosses import BossBot, BossRun

MAX_BOSS_RUNS = 2


@register
class BossActivity(HQRun):
    name = 'boss'
    weight = 0.15
    kinds = ('coghq',)

    @classmethod
    def canRun(cls, bot):
        d = bot.director
        if bot.area is None or not d.clockSynced or not d.air.districtId:
            return False
        return False                   # the owner's rule: bots never start a boss run on their own (only a
                                       # real toon boarding does, LobbyKeeper; bot-only runs are test commands)
        if bot.area.players or sum(1 for g in RUNS if g.kind in cm.BOSSES and not g.withPlayer) >= MAX_BOSS_RUNS:
            return False
        return fit(bot, 0.85, 20) and bb.stocked(bot) and suits.readyForPromotion(bot, cm.BOSSES[kind]['dept'])

    def __init__(self, bot, group=None, role='member'):
        if group is not None:
            HQRun.__init__(self, bot, group, role)
        else:
            from toontown.bots.activities import Activity
            Activity.__init__(self, bot)
            self.group = None
        self.bb = None

    def start(self):
        if self.group is None:
            bot, d = self.bot, self.director
            bot.p8bNextBoss = globalClock.getRealTime() + random.uniform(600.0, 1500.0)
            kind = next(k for k, v in cm.BOSSES.items() if v['lobby'] == bot.area.id)
            dept = cm.BOSSES[kind]['dept']
            mates = [b for b in d.bots.values() if b is not bot and b.area is bot.area and free(b)
                     and fit(b, 0.7, 12) and bb.stocked(b) and suits.readyForPromotion(b, dept)]
            if len(mates) < 3:
                return False
            mates = random.sample(mates, min(len(mates), random.randint(3, 7)))
            g = makeGroup(d, kind, None, bot.area)
            if g is None:
                return False
            self.__init__(bot, g, 'leader')
            if not HQRun.start(self):
                RUNS.remove(g)
                return False
            for b in mates:
                if b.startActivity(BossActivity(b, g)):
                    b.nextTick = min(b.nextTick, globalClock.getRealTime())
            g.log.members = list(g.members)
            return True
        return HQRun.start(self)

    # -- inside ---------------------------------------------------------------------------------------------------
    def enterInside(self, now):
        g, bot = self.group, self.bot
        if g.run is None:
            g.run = BossRun(g.kind, self.director, g.log)
            g.run.members = list(g.members)
            g.run.skipTo = getattr(g, 'skipTo', None)
            g.run.players = set(getattr(g, 'playerIds', []))
            g.log.members = list(g.members)
        g.run.enterZone(self.zone)
        bot.relocate(self.zone)
        self.fast = True
        self.bb = BossBot(self, g.run)

    def stepInside(self, now):
        if self.bb is None:
            return False
        return self.bb.step(now)

    def onDirectInside(self, fieldName, args):
        if self.bb is not None:
            self.bb.onDirect(fieldName, args)

    def stopInside(self, why):
        if self.bb is not None:
            self.bb.stop()
            self.bb = None
        g = self.group
        if g is None or g.run is None:
            return
        if not any(b is not self.bot and isinstance(b.activity, BossActivity) and b.activity.group is g
                   and b.activity.phase in ('inside', 'ride') for b in self.director.bots.values()):
            if g.log.tEnd is None:
                g.log.finish('won' if g.run.won else 'left-' + why)
                g.log.counts['promoted'] = len(g.run.promoted)
            g.run.end()


# ---- the owner's lobby rules: 8 ready bots wait at every boss elevator; a real toon always gets in -----------
import math
import time
from direct.showbase.DirectObject import DirectObject
from toontown.bots import BotWorld
from toontown.bots.activities import Activity
from toontown.bots.activities.coghq import Group
from toontown.bots.BotToon import WALK_SPEED

LOBBIES = dict((v['lobby'], k) for k, v in cm.BOSSES.items())
PARTY = 8
BotWorld.AREA_TARGETS.update(dict((lobby, (PARTY, PARTY)) for lobby in LOBBIES))


def countdownOf(kind):
    from toontown.building import ElevatorConstants as EC
    t = {'vp': EC.ELEVATOR_VP, 'cfo': EC.ELEVATOR_CFO, 'cj': EC.ELEVATOR_CJ, 'ceo': EC.ELEVATOR_BB}[kind]
    return EC.ElevatorData[t]['countdown']


@register
class LobbyWait(Activity):
    """A ready regular waits by the boss elevator (so a real toon's elevator can fill at once)."""
    name = 'lobbywait'
    weight = 8.0
    kinds = ('coghq',)
    label = 'lobby'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.area.id in LOBBIES and bot.node is not None

    def start(self):
        bot = self.bot
        kind = LOBBIES[bot.area.id]
        place = next((p for p in bot.area.wm.places() if p['name'] == 'elevator_' + kind), None)
        if place is None:
            return False
        k = bot.area.nodeNear(place['pos'][0], place['pos'][1], random.uniform(8.0, 20.0))
        if k is None or not bot.walkTo(k, WALK_SPEED):
            return False
        self.place = place
        self.until = globalClock.getRealTime() + random.uniform(240.0, 600.0)
        return True

    def step(self, now):
        if self.bot.path:
            return True
        if not getattr(self, 'faced', False):
            self.faced = True
            self.bot.faceTo(self.place['pos'])
        return now < self.until


def boardable(b, dept):
    """A regular the lobby can put in the elevator at once (the AI's own checks, and a run's kit)."""
    from toontown.bots.coghq import loadout
    # the owner (09-25): nobody boards a boss without a gag check - stocked up like a normal player
    return free(b) and fit(b, 0.7, 35) and bb.stocked(b) and loadout.quality(b) >= 20 and suits.hasWholeSuit(b, dept) \
        and suits.readyForPromotion(b, dept) and (bb.botHp(b)[1] or 0) >= BOSS_LAFF.get(dept, 0)


class LobbyKeeper(DirectObject):
    """Every boss lobby (the owner's rules): 8 boardable regulars wait there; once a real toon sits in the
    elevator the bots fill every other seat at once (a full boss elevator still waits for its countdown); a real
    toon who walks up to the elevator (within WALKUP ft) while it is full gets a seat: a seated bot steps off
    (requestExit), only ever a bot; if he does not sit down a bot takes the seat back; from 6 s before the doors
    close every seat is filled, so the fight starts with 8 toons. Bots never hold a seat with no real toon
    aboard (unless 'bossruns on', the test command). Every departure is counted (bots-coghq log, STATS)."""
    WALKUP = 22.0

    def __init__(self):
        DirectObject.__init__(self)
        self.trips = {}          # lobby -> Group of the elevator trip being filled
        self.cdStart = {}        # lobby -> when the countdown (re)began
        self.lastState = {}
        self.short = {}          # lobby -> when it fell below 8
        self.refills = []        # (lobby, seconds)
        self.lastSeats = {}      # lobby -> seats last seen while boarding
        self.departures = []     # (kind, real avIds, bots aboard, time)
        self.testRuns = False    # 'bossruns on': bot-only boss runs allowed (test command, off by default)
        self.lastSlow = {}       # lobby -> last unfit-waiter check
        self.lastSearch = {}     # lobby -> last summon search (the whole pool)
        taskMgr.doMethodLater(0.5, self.__tick, 'bots-lobby-keeper')

    @property
    def director(self):
        return getattr(simbase.air, 'botDirector', None)

    def __tick(self, task):
        d = self.director
        try:
            if d is not None and d.world is not None and d.air.districtId:
                for lobby, kind in LOBBIES.items():
                    self.__lobby(d, lobby, kind)
        except Exception:
            import traceback
            d.error('P8b lobby keeper', traceback.format_exc())
        return task.again

    def __departed(self, d, lobby, kind, seats):
        real = [s for s in seats if s and s not in d.bots]
        bots = [s for s in seats if s and s in d.bots]
        if not real and not bots:
            return
        self.departures.append((kind, real, len(bots), time.strftime('%H:%M:%S')))
        self.departures = self.departures[-60:]
        if real:
            bb.STATS.count('hq_lobby_depart_real')
            bb.STATS.count('hq_lobby_depart_toons_%d' % (len(real) + len(bots)))
        else:
            bb.STATS.count('hq_lobby_depart_botonly' + ('_test' if self.testRuns else ''))
        cm.notify.info('[TTBOTS-P8c] %s elevator departs: real %s + %d bots = %d toons%s' % (
            kind, real, len(bots), len(real) + len(bots), '' if real else ' (BOT-ONLY%s)' % (' test' if self.testRuns else '')))

    def __lobby(self, d, lobby, kind):
        now = globalClock.getRealTime()
        area = d.world.areas[lobby]
        dept = cm.BOSSES[kind]['dept']
        # a bot still counted in this lobby's area while it rides or fights the boss (its zone is the office) is
        # not waiting here: the lobby's own 8 are the bots standing in the lobby zone; the director's target grows
        # by the ones away, so it neither refills short nor pulls the new waiters out (the owner's rule 5)
        away = [b for b in d.bots.values() if b.area is area and b.state == 'present' and b.zoneId != lobby]
        here = [b for b in d.bots.values() if b.area is area and b.state == 'present' and b.travel is None
                and b.zoneId == lobby]
        quota = PARTY + len(away)
        if getattr(area, 'baseTarget', None) != quota:
            area.baseTarget = area.target = quota
            area.max = max(quota, PARTY)
        # refill timing (the owner's rule 5)
        if len(here) < PARTY and lobby not in self.short:
            self.short[lobby] = now
        elif len(here) >= PARTY and lobby in self.short:
            secs = now - self.short.pop(lobby)
            if secs > 1.0:
                self.refills.append((lobby, round(secs, 1)))
                self.refills = self.refills[-40:]
                cm.notify.info('[TTBOTS-P8b] lobby %s back to %d bots in %.1f s' % (lobby, PARTY, secs))
                bb.STATS.count('hq_lobby_refills')
        # a waiting regular who can no longer board (hurt, out of gags) makes room: home to heal / restock
        # (checked every 5 s: a pouch check parses the inventory, and this runs for 4 lobbies twice a second)
        slow = now - self.lastSlow.get(lobby, 0.0) > 5.0
        if slow:
            self.lastSlow[lobby] = now
        for b in (here if slow else ()):
            if free(b) and not boardable(b, dept) and now - b.arrivedAt > 30.0 and \
                    now - getattr(b, 'p8cUnfitAt', 0.0) > 60.0:
                b.p8cUnfitAt = now
                home = d.world.areas.get(b.home)
                if home is not None and d.travel(b, home, why='P8c: out of the %s lobby to heal / restock' % kind,
                                                 via='teleport'):
                    bb.STATS.count('hq_lobby_unfit_out')
                break
        # top the lobby up to 8 (the owner's rule 5): boardable regulars teleport in, one every few seconds
        incoming = [b for b in d.bots.values() if b.travel is not None and getattr(b.travel, 'dest', None) is area]
        if len(here) + len(incoming) < PARTY and now - getattr(self, 'lastSummon', {}).get(lobby, 0.0) > 2.0 \
                and now - self.lastSearch.get(lobby, 0.0) > 3.0:
            self.lastSearch[lobby] = now          # cheap checks first: the pouch checks parse the inventory
            cands = [b for b in d.bots.values() if free(b) and b.area is not None and b.area.id not in LOBBIES
                     and suits.higherHood(b) and not getattr(b, 'p8bWantGags', False) and d.eligible(b, area)
                     and fit(b, 0.8, 25) and boardable(b, dept)]
            if cands:
                b = random.choice(cands)
                if sum(1 for x in here if healer(x)) < 3 and any(healer(x) for x in cands):
                    b = random.choice([x for x in cands if healer(x)])     # keep healers in every lobby
                if d.travel(b, area, why='P8b: waits at the %s elevator' % kind, via='teleport'):
                    self.__dict__.setdefault('lastSummon', {})[lobby] = now
                    bb.STATS.count('hq_lobby_summoned')
        from toontown.bots.activities.coghq import findElevator
        e, placeName = findElevator(d, kind, None, area)
        if e is None:
            return
        st = cm.elevState(e)
        seats = cm.seats(e, PARTY)
        if st != self.lastState.get(lobby):
            prev = self.lastState.get(lobby)
            self.lastState[lobby] = st
            if st == 'waitCountdown' and prev != 'waitCountdown':
                self.cdStart[lobby] = now
            if st in ('allAboard', 'closing') and prev in cm.BOARDABLE:
                self.__departed(d, lobby, kind, self.lastSeats.get(lobby) or seats)
        if st in cm.BOARDABLE:
            self.lastSeats[lobby] = seats
        real = [s for s in seats if s and s not in d.bots]
        seated = [s for s in seats if s and s in d.bots]
        g = self.trips.get(lobby)
        if g is not None and (getattr(g, 'departed', False) or g.elevId != e.doId):
            g = None
            self.trips.pop(lobby, None)
        if not real:
            # nobody real sits there: a bot never holds or heads for a seat on its own (the owner's rule 4)
            if not self.testRuns and st in cm.BOARDABLE:
                for b in list(d.bots.values()):
                    act = b.activity
                    if isinstance(act, HQRun) and act.group.kind == kind and act.group.area is area and \
                            act.phase in ('walk', 'atelev', 'boarding', 'seated') and act.zone is None and \
                            not getattr(act.group, 'departed', False):
                        b.endActivity('no real toon aboard')
                        bb.STATS.count('hq_lobby_bot_unseated')
                if g is not None and not g.members:
                    self.trips.pop(lobby, None)
            return
        if st not in cm.BOARDABLE:
            return
        if g is None:
            log = cm.RunLog(kind, '%s (real toon %s)' % (cm.BOSSES[kind]['label'], real), [], withPlayer=True)
            g = Group(kind, None, area, placeName, e.doId, log)
            g.withPlayer = True
            g.go = True
            g.playerIds = list(real)
            log.players = list(real)
            RUNS.append(g)
            self.trips[lobby] = g
            log.note('real toon(s) %s in the %s elevator: the bots fill it to %d' % (real, kind, PARTY))
        for a in real:
            if a not in g.playerIds:
                g.playerIds.append(a)
                g.log.players.append(a)
        # real toons walking up to the elevator (standing near it, not seated yet)
        place = g.place()
        view = d.viewOf(lobby)
        near = []
        for a in area.players:
            if a in seats:
                continue
            o = view.objects.get(a) if view is not None else None
            if o is None or place is None or math.hypot(o.pos[0] - place['pos'][0], o.pos[1] - place['pos'][1]) < self.WALKUP:
                near.append(a)
        departAt = self.cdStart.get(lobby, now) + countdownOf(kind)
        reserve = 0 if now >= departAt - 6.0 else len(near)
        want = max(0, PARTY - len(real) - reserve)
        coming = [b for b in d.bots.values() if isinstance(b.activity, HQRun) and b.activity.group is g
                  and b.activity.phase in ('walk', 'atelev', 'boarding')]
        have = len(seated) + len(coming)
        if have < want:
            cands = [b for b in here if boardable(b, dept)]
            if place is not None:
                cands.sort(key=lambda b: math.hypot(b.pos[0] - place['pos'][0], b.pos[1] - place['pos'][1]))
            # two healers ride along (the owner's rule 5: someone must be able to keep him up)
            aboard = [d.bots[a] for a in seated if a in d.bots] + coming
            if sum(1 for b in aboard if healer(b)) < 2:
                cands.sort(key=lambda b: not healer(b))
            for b in cands[:want - have]:
                if b.startActivity(BossActivity(b, g)):
                    b.nextTick = min(b.nextTick, now)
                    g.log.members = list(g.members)
            if len(cands) < want - have:
                bb.STATS.count('hq_lobby_short_boardable')
        elif len(seated) > want and not coming:
            # a real toon walked up to the full elevator: a bot steps off for him (only ever a bot)
            for a in seated[:len(seated) - want]:
                b = d.bots.get(a)
                if b is not None and b.activity is not None:
                    g.log.note('bot %s steps off for real toon(s) %s' % (a, near))
                    g.log.count('stepped_off')
                    bb.STATS.count('hq_lobby_stepoff')
                    cm.notify.info('[TTBOTS-P8c] %s elevator: bot %s steps off for real toon(s) %s' % (kind, a, near))
                    # the boss elevators are antiShuffle: an exit sets the countdown to min(left + 10, full)
                    # (DistributedElevatorExtAI.acceptExiter)
                    full = countdownOf(kind)
                    left = max(0.0, departAt - now)
                    self.cdStart[lobby] = now + min(left + 10.0, full) - full
                    departAt = self.cdStart[lobby] + full
                    b.endActivity('step off for a real toon')
                    if a in g.members:
                        g.members.remove(a)


KEEPER = LobbyKeeper()