"""TTBOTS P8: the SHARED bot battle brain (street battles, Cog buildings; later factories, mints, DA
offices, CGC and the boss battles reuse it). A bot fights exactly as a client does: every message is
a clsend field sent with the bot's client channel (bot.send), every ack comes after a human delay
and always before the AI's own timer (the per-round bot loop).

API (for the Cog HQ / boss builder):
    brain = BattleBrain(bot, kind)          kind 'street' | 'bldg' | 'level' (any DistributedBattleBase)
    brain.startOn(suitObj, pos, h, className='DistributedSuit')   walks-into-a-Cog: requestBattle
    brain.join(battleObj)                   toonRequestJoin (a battle that is already there)
    brain.attach(battleObj)                 the AI already put the bot in the battle (building floor,
                                            factory cell, boss round): just play it
    brain.step(now) -> bool                 call from the activity's step; False = it is over
    brain.result                            'won' | 'lost' | 'died' | 'ran' | 'denied' | 'gone' | ...
    brain.onDirect(field, args)             forward the activity's onDirect (denyBattle, denyLocalToonJoin)
    brain.leave(why)                        stop acking (logout); the AI's avatar-exit handles the rest
    HUB (module global): hub.watch(doId, fn) routes any object's broadcasts to fn(obj, field, args)
    (elevators, interiors, buildings); hub.record(...) keeps the per-battle barrier bookkeeping.

What the brain does each round (all barriers from DistributedBattleBaseAI):
  FaceOff    faceOffDone  street: only the toon that walked into the Cog; building: every toon
  join       joinDone(id) for every joining suit/toon, after its walk-in (AI waits 22 s)
  adjust     adjustDone   after the AI's own estimate (AI waits estimate + 2 s)
  WaitForInput  requestAttack after 3-8 s of 'thinking' (gag picker below), PASS when out of gags,
             a rare toonRequestRun when about to go sad (street only)
  PlayMovie  movieDone    after the client's movie length (estimated from setMovie), +0.5-1.5 s
  Reward / BuildingReward  rewardDone after the reward panel length (estimated from setBattleExperience)
             (a building FLOOR reward is not acked: the real client never sends it either)
Gag picker, a good kid's way: heal the lowest ally below ~50% (60% for a real player); lure when
there are many Cogs (never a Cog already lured or being lured); trap a Cog someone is luring (never one
already trapped); finish off a low Cog with the smallest gag that does it; else join a teammate's
Cog with the same track; Sound when 3+ Cogs and none lured; never Drop on a lured Cog.

Proof numbers go to <run>/bots-battle.json/.txt (every 15 s).
"""
import json
import math
import os
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal
from direct.showbase.DirectObject import DirectObject
from panda3d.core import Point3

notify = DirectNotifyGlobal.directNotify.newCategory('BotBattle')

HEAL, TRAP, LURE, SOUND, THROW, SQUIRT, DROP = range(7)
ATTACK_TRACKS = (SOUND, THROW, SQUIRT, DROP)
PASS = 98
NO_ATTACK = -1
UN_ATTACK = -2
TRACK_NAMES = ('heal', 'trap', 'lure', 'sound', 'throw', 'squirt', 'drop')

# client movie lengths per piece (s), calibrated against a real client's own PlayMovie->movieDone
# and Reward->rewardDone times (see vtt_p8battle.py 'calib' lines)
MOVIE = {'intro': 1.0, HEAL: 6.5, TRAP: 3.3, LURE: 7.0, SOUND: 5.5, THROW: 4.8, SQUIRT: 5.0, DROP: 4.6,
         'throwMore': 1.5, 'suitDied': 6.0, 'diedMore': 1.5, 'suitAtk': 6.3, 'toonDied': 2.0}
SUIT_SPEED = 4.8
TOON_SPEED = 8.0
FACEOFF_TAUNT = 3.5
# P8b: the reward states a client acks with rewardDone, per battle kind (a building / level FLOOR
# reward and a boss battle's Reward are never acked by the client: they run on the AI's timer)
LEVEL_REWARDS = ('FactoryReward', 'MintReward', 'StageReward', 'CountryClubReward')
REWARD_ACK = {'street': ('Reward',), 'bldg': ('BuildingReward',), 'level': LEVEL_REWARDS, 'boss': ()}
ALL_REWARDS = ('Reward', 'BuildingReward') + LEVEL_REWARDS


def now_():
    return globalClock.getRealTime()


def isBattle(obj):
    d = obj.dclass
    return d.getFieldByName('requestAttack') is not None and d.getFieldByName('setMembers') is not None


def objOf(air, doId):
    v = air.doView.get(doId)
    return v.objects.get(doId) if v is not None else None


def toonHp(air, avId):
    o = objOf(air, avId)
    if o is None:
        return None, None
    hp = (o.get('setHp') or (None,))[0]
    mx = (o.get('setMaxHp') or (None,))[0]
    return hp, mx


def botHp(bot):
    f = bot.ownFields
    hp = (f.get('setHp') or (None,))[0]
    mx = (f.get('setMaxHp') or (None,))[0]
    if hp is None or mx is None:
        hp2, mx2 = toonHp(bot.air, bot.avId)
        hp = hp if hp is not None else hp2
        mx = mx if mx is not None else mx2
    return hp, mx


class _Holder:
    """Just enough of a toon for InventoryBase / Experience (the client's own classes)."""

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


def loadInventory(bot):
    """(InventoryBase, Experience) from the owner view of the toon, or (None, None)."""
    from toontown.toon.Experience import Experience
    from toontown.toon.InventoryBase import InventoryBase
    f = bot.ownFields
    try:
        exp = Experience(f['setExperience'][0])
        tracks = list(f['setTrackAccess'][0])
        maxCarry = f['setMaxCarry'][0]
        blob = f['setInventory'][0]
    except (KeyError, IndexError, TypeError):
        return None, None
    h = _Holder(exp, maxCarry, tracks, (f.get('setAccess') or (2,))[0])
    inv = InventoryBase(h, blob)
    h.inventory = inv
    return inv, exp


def gagCount(bot, tracks=None):
    inv, exp = loadInventory(bot)
    if inv is None:
        return 0
    return sum(inv.numItem(t, l) for t in (tracks or range(7)) for l in range(7))


STOCKED = 0.8


def pouchRoom(inv, exp):
    """How many gags a full shop trip gives this toon (coghq.loadout.fill, the restock): each track's top three
    levels to InventoryBase's own limit and two of each smaller one, lure / toon-up / trap capped - or its max
    carry when that is less. So a toon with few tracks or low exp is 'stocked' at what it can actually carry."""
    from toontown.bots.coghq import loadout
    toon = inv.toon
    room = 0
    for t in range(7):
        if not toon.trackArray[t]:
            continue
        top = loadout.topLevel(exp, t)
        n = sum(inv.getMax(t, l) if l >= top - 2 else min(2, inv.getMax(t, l)) for l in range(top + 1))
        room += min(n, loadout.SUPPORT_CAP.get(t, n))
    return min(toon.getMaxCarry(), room)


def stocked(bot, share=STOCKED):
    """The owner's gag check (09-25): before a building, factory, mint / office / CGC or boss a bot is stocked up
    like a normal player - its pouch at least `share` full of what it can carry."""
    inv, exp = loadInventory(bot)
    if inv is None:
        return False
    have = sum(inv.numItem(t, l) for t in range(7) for l in range(7))
    return have >= share * pouchRoom(inv, exp)


def healthy(bot, frac):
    hp, mx = botHp(bot)
    return hp is not None and mx and hp >= frac * mx


# ---- stats -------------------------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.c = {}
        self.started = time.time()
        self.events = []           # last few notable lines
        self.barrierLate = []      # details of every barrier that closed with a bot pending

    def count(self, key, n=1):
        self.c[key] = self.c.get(key, 0) + n

    def note(self, line):
        self.events = (self.events + ['%s %s' % (time.strftime('%H:%M:%S'), line)])[-60:]


STATS = Stats()


class Record:
    """Bookkeeping for one battle / interior shared by all bots in it: who owes which ack.
    A barrier that the SERVER closes while a bot still owes its ack = the server's timer ran out with a
    bot pending (the server only moves on when all acked or its timer fired)."""

    def __init__(self, doId, kind):
        self.doId = doId
        self.kind = kind
        self.t0 = now_()
        self.owed = {}             # key -> {avId: sentTime or None}
        self.state = None
        self.stateT = self.t0
        self.rounds = 0
        self.bots = set()
        self.players = set()
        self.suitHp = {}
        self.toonHp = {}               # P8b: laff while the battle owns it (from each movie)
        self.roundT = self.t0          # P8b pick order: when this WaitForInput began
        self.queue = []                # bots still to pick this round, in order
        self.picking = None
        self.pickT = 0.0
        self.nextAt = 0.0
        self.chosen = {}               # avId -> (track, level, target) as the AI echoed them
        self.pickOrder = []            # avIds in the order their picks were echoed this round
        self.members = None
        self.result = None
        self.reward = False
        self.startedBy = None
        self.maxToons = 0

    def need(self, key, avId):
        self.owed.setdefault(key, {}).setdefault(avId, None)

    def acked(self, key, avId, late=None):
        d = self.owed.get(key)
        if d is not None and avId in d:
            d[avId] = now_()
        STATS.count('ack_' + (key[0] if isinstance(key, tuple) else key))

    def drop(self, avId):
        for d in self.owed.values():
            d.pop(avId, None)

    def close(self, key):
        d = self.owed.pop(key, None)
        if not d:
            return
        STATS.count('barriers_closed')
        pending = [a for a, t in d.items() if t is None]
        if pending:
            STATS.count('barrier_timeout_bot_pending')
            STATS.count('barrier_timeout_bot_pending_' + str(key[0]))
            line = '%s %s %s: bot(s) %s had not acked when the server moved on' % (
                self.kind, self.doId, key, pending)
            STATS.barrierLate = (STATS.barrierLate + [line])[-30:]
            notify.warning('[TTBOTS] ' + line)

    def closePrefix(self, prefix):
        for k in [k for k in self.owed if k[0] == prefix]:
            self.close(k)


# ---- members ------------------------------------------------------------------------------------------
class Members:
    def __init__(self, args):
        suits, js, ps, acts, lur, traps, toons, jt, pt, at, rt = args[:11]
        self.suits = list(suits)
        self.toons = list(toons)

        def pick(s, lst):
            out = []
            for c in s:
                i = int(c)
                if i < len(lst):
                    out.append(lst[i])
            return out
        self.joiningSuits = pick(js, suits)
        self.pendingSuits = pick(ps, suits)
        self.activeSuits = pick(acts, suits)
        self.luredSuits = pick(lur, suits)
        self.traps = {}
        for i, c in enumerate(traps):
            if c != '9' and i < len(suits):
                self.traps[suits[i]] = int(c)
        self.joiningToons = pick(jt, toons)
        self.pendingToons = pick(pt, toons)
        self.activeToons = pick(at, toons)
        self.runningToons = pick(rt, toons)


def parseMovie(args):
    """setMovie -> (active, toonIds, suitIds, toonAttacks[(idx, track, level, target, hp[], died)],
    suitAttacks[(idx, atk, target, hp[], died)])."""
    active, toons, suits = args[0], list(args[1]), list(args[2])
    ta, sa = [], []
    for i in range(4):
        b = 3 + 10 * i
        a = args[b:b + 10]
        ta.append((a[0], a[1], a[2], a[3], list(a[4]), a[8]))
    for j in range(4):
        b = 43 + 7 * j
        a = args[b:b + 7]
        sa.append((a[0], a[1], a[2], list(a[3]), a[4]))
    return active, toons, suits, ta, sa


def movieLength(args):
    """How long the client's battle movie runs, from setMovie (Movie.py plays heal, trap, lure,
    sound, throw, squirt, drop, then the Cogs' attacks; each gag track once per target)."""
    active, toons, suits, ta, sa = parseMovie(args)
    t = MOVIE['intro']
    byTrack = {}
    died = 0
    for idx, track, level, target, hps, d in ta:
        if idx < 0 or track < 0 or track > DROP:
            continue
        byTrack.setdefault(track, set()).add(target)
        died |= d
    for track, targets in byTrack.items():
        if track in (SOUND,):
            t += MOVIE[track]
        elif track == HEAL:
            t += MOVIE[HEAL] * len(targets)
        elif track == THROW:
            t += MOVIE[THROW] + MOVIE['throwMore'] * (len(targets) - 1)   # throws at several Cogs overlap
        else:
            t += MOVIE[track] * max(1, len(targets))
    nd = bin(died).count('1')
    if nd:
        t += MOVIE['suitDied'] + MOVIE['diedMore'] * (nd - 1)        # Cogs that go down together
    for idx, atk, target, hps, tdied in sa:
        if idx >= 0 and atk >= 0:
            t += MOVIE['suitAtk']
            t += MOVIE['toonDied'] * bin(tdied).count('1')
    return t


def rewardLength(args):
    """How long the reward panel runs (RewardPanel.getExpTrack per toon), from setBattleExperience."""
    t = 0.5
    for i in range(4):
        b = 9 * i
        avId = args[b]
        if avId in (0, -1):
            continue
        earned = args[b + 2]
        items = args[b + 4]
        merits = args[b + 7]
        parts = args[b + 8]
        t += 2.2                                   # frame 1.0 + 0.75 + 0.25 (+ frame time)
        t += 1.0 * sum(1 for e in earned if e > 0)
        if items:
            t += 0.75 + 1.0 * len(items)
        t += 0.6 * sum(1 for m in (merits or []) if m > 0)
        if any(parts or []):
            t += 1.75
    return t


# ---- the hub: one listener for every battle / building object in any bot view ------------------------
class Hub(DirectObject):
    def __init__(self):
        DirectObject.__init__(self)
        self.director = None
        self.brains = {}           # battle doId -> {avId: brain}
        self.records = {}          # doId -> Record
        self.waitStart = {}        # avId -> brain waiting for its new battle to show up
        self.watchers = {}         # doId -> [fn(obj, field, args)]
        self.enterHooks = []       # fn(view, obj) for every new object (building / battle finders)
        self.classHooks = {}       # className -> [fn(obj, field, args)] for every broadcast of that class
        self.helpHook = None       # fn(battleObj, record): a real player's battle needs help
        self.started = False
        self.recent = []           # times bots started street battles (rate limit)
        self.inSafe = set()        # bots that told the SafeZoneManager they are in a playground

    def ensure(self, director):
        if self.started:
            return
        self.started = True
        self.director = director
        self.accept('botview-enter', self.__enter)
        self.accept('botview-exit', self.__exit)
        self.accept('botview-field', self.__field)
        taskMgr.doMethodLater(2.0, self.__tick, 'bots-battle-hub')
        taskMgr.doMethodLater(15.0, self.__write, 'bots-battle-write')
        taskMgr.doMethodLater(0.25, self.__pickTick, 'bots-battle-picks')
        notify.info('[TTBOTS] battle hub up')

    # -- routing --------------------------------------------------------------------------------------
    def record(self, doId, kind):
        r = self.records.get(doId)
        if r is None:
            r = Record(doId, kind)
            self.records[doId] = r
        return r

    def watch(self, doId, fn):
        self.watchers.setdefault(doId, []).append(fn)

    def unwatch(self, doId, fn):
        lst = self.watchers.get(doId)
        if lst and fn in lst:
            lst.remove(fn)
            if not lst:
                del self.watchers[doId]

    def register(self, brain):
        self.brains.setdefault(brain.battleId, {})[brain.bot.avId] = brain

    def unregister(self, brain):
        d = self.brains.get(brain.battleId)
        if d is not None and d.get(brain.bot.avId) is brain:
            del d[brain.bot.avId]
            if not d:
                del self.brains[brain.battleId]

    def __enter(self, view, obj):
        try:
            if isBattle(obj):
                self.__battleSeen(obj)
            for fn in list(self.enterHooks):
                fn(view, obj)
        except Exception:
            self.error('hub enter', traceback.format_exc())

    def __exit(self, view, obj, deleted=False):
        try:
            for b in list(self.brains.get(obj.doId, {}).values()):
                b.onGone()
            for fn in list(self.watchers.get(obj.doId, [])):
                fn(obj, '__exit__', (deleted,))
            r = self.records.pop(obj.doId, None)
            if r is not None:
                for k in list(r.owed):
                    r.owed.pop(k)       # gone with it: nothing can close any more
                self.__finishRecord(r)
        except Exception:
            self.error('hub exit', traceback.format_exc())

    def __field(self, view, obj, fieldName, args, sender):
        try:
            if isBattle(obj):
                r = self.records.get(obj.doId)
                if r is not None:
                    self.__recordField(r, obj, fieldName, args)
                for b in list(self.brains.get(obj.doId, {}).values()):
                    b.onField(obj, fieldName, args)
                if fieldName == 'setMembers':
                    self.__battleSeen(obj)
            for fn in list(self.watchers.get(obj.doId, [])):
                fn(obj, fieldName, args)
            for fn in self.classHooks.get(obj.className, ()):
                fn(obj, fieldName, args)
        except Exception:
            self.error('hub field %s' % fieldName, traceback.format_exc())

    def __battleSeen(self, obj):
        m = obj.get('setMembers')
        if not m:
            return
        toons = m[6]
        for av in toons:
            b = self.waitStart.pop(av, None)
            if b is not None:
                b.attach(obj)
        d = self.director
        if d is None:
            return
        real = [a for a in toons if a not in d.bots]
        r = self.records.get(obj.doId)
        if real and r is None:
            r = self.record(obj.doId, 'street' if obj.className == 'DistributedBattle' else 'bldg')
        if r is not None:
            r.players.update(real)
            r.maxToons = max(r.maxToons, len(toons))
        if real and self.helpHook is not None and len(toons) < 4:
            st = (obj.get('setState') or ('',))[0]
            if st not in ('Reward', 'Resume', 'BuildingReward'):
                self.helpHook(obj, r)

    def __recordField(self, r, obj, fieldName, args):
        if fieldName == 'setState':
            st = args[0]
            prev, r.state, r.stateT = r.state, st, now_()
            if prev == 'FaceOff':
                r.closePrefix('faceoff')
            if prev == 'WaitForInput' and st != 'WaitForInput':
                r.closePrefix('input')
            if prev == 'PlayMovie' and st != 'PlayMovie':
                r.closePrefix('movie')
            if prev in ALL_REWARDS and st not in ALL_REWARDS:
                r.closePrefix('reward')
            if st == 'WaitForInput':
                r.rounds += 1
                STATS.count('rounds')
                r.roundT = now_()
                r.queue = []
                r.picking = None
                r.chosen = {}
                r.pickOrder = []
                r.nextAt = r.roundT + random.uniform(2.5, 4.5)      # the first bot looks the Cogs over
            if st in ALL_REWARDS:
                r.reward = True
        elif fieldName == 'setChosenToonAttacks':
            ids, tracks, levels, targets = args
            new = dict((a, (tracks[i], levels[i], targets[i])) for i, a in enumerate(ids))
            for a, v in new.items():                   # the order the picks came in (the owner's pick order)
                if 0 <= v[0] <= DROP and r.chosen.get(a) != v:
                    if a in r.pickOrder:
                        r.pickOrder.remove(a)
                    r.pickOrder.append(a)
            r.chosen = dict((a, new[a]) for a in r.pickOrder if a in new)
            r.chosen.update((a, v) for a, v in new.items() if a not in r.chosen)
        elif fieldName == 'setMovie' and args[0] == 1:
            # the movie is made: every wait for input is over (the state may come a frame later)
            r.closePrefix('input')
            self.__audit(r)
            try:
                active, toons, suits, ta, sa = parseMovie(args)
                dmg = {}
                for idx, track, level, target, hps, d in ta:
                    if idx < 0 or track in (HEAL, NO_ATTACK, UN_ATTACK) or track > DROP:
                        continue
                    for j, h in enumerate(hps):
                        if h > 0 and j < len(suits):
                            dmg[suits[j]] = dmg.get(suits[j], 0) + h
                for s in suits:
                    o = objOf(self.director.air, s)
                    hp = (o.get('setHP') or (None,))[0] if o is not None else None
                    if hp is not None:
                        r.suitHp[s] = hp - dmg.get(s, 0)
                # P8b: a toon's laff is owned by the battle while it lasts (hpOwnedByBattle: no setHp goes
                # out), so the clients read it off the movie; so does the brain (its Toon-Up choice needs it)
                th = r.toonHp
                for t in toons:
                    if t not in th:
                        # P8c: a bot's own laff from its owner channel (setHp ownrecv, always current); a zone
                        # view can hold a copy of the toon from a zone it already left (a floor ago, stale)
                        b = self.director.bots.get(t)
                        h, mx = toonHp(self.director.air, t)
                        if b is not None:
                            h2 = botHp(b)[0]
                            h = h2 if h is None else (h if h2 is None else min(h, h2))
                        if h is not None:
                            th[t] = h
                for idx, track, level, target, hps, d in ta:
                    if idx >= 0 and track == HEAL:
                        for j, h in enumerate(hps):
                            if h > 0 and j < len(toons) and toons[j] in th:
                                mx = toonHp(self.director.air, toons[j])[1] or th[toons[j]] + h
                                th[toons[j]] = min(mx, th[toons[j]] + h)
                for idx, atk, target, hps, died in sa:
                    if idx >= 0 and atk >= 0:
                        for j, h in enumerate(hps):
                            if h > 0 and j < len(toons) and toons[j] in th:
                                th[toons[j]] -= h
            except Exception:
                self.error('movie parse', traceback.format_exc())
        elif fieldName == 'setMembers':
            m = Members(args)
            r.members = m
            for k in [k for k in r.owed if k[0] == 'join']:
                if k[1] not in m.joiningSuits and k[1] not in m.joiningToons:
                    r.close(k)

    # -- P8b: the pick order (the owner's rule 1: the real players first, then one bot at a time) ---------
    def __pickTick(self, task):
        try:
            now = now_()
            d = self.director
            for r in list(self.records.values()):
                if r.state != 'WaitForInput' or not r.queue:
                    continue
                if r.picking is not None:
                    b = r.picking
                    if b.echo or b.phase == 'done' or b.state != 'WaitForInput' or now - r.pickT > 3.0:
                        r.picking = None
                        r.nextAt = now + random.uniform(0.9, 1.7)      # the next kid reads the new pick
                        if PICK_DEADLINE - (now - r.roundT) < 1.2 * len(r.queue) + 1.5:
                            r.nextAt = now + random.uniform(0.3, 0.5)  # short of time (he picked late): quick picks
                    else:
                        continue
                if now < r.nextAt:
                    continue
                m = r.members
                players = [t for t in (m.activeToons if m else []) if t not in d.bots]
                done = all(r.chosen.get(p, (NO_ATTACK,))[0] not in (NO_ATTACK, UN_ATTACK) for p in players)
                # the owner (09-25): a bot almost never picks before the real player - they wait for him until only
                # the time the bots in the queue still need is left before the deadline (1.2 s a bot at the fast pace)
                wait = max(6.0, PICK_DEADLINE - 1.0 - 1.2 * len(r.queue))
                if players and not done and now - r.roundT < wait:
                    continue
                # (09-30) a bot with an attack gag picks before one without: the owner's 'a bot that picks first
                # must NEVER pass' - a heal/lure-only bot can see the attacks queued (a lure only pays if cashed in)
                r.queue.sort(key=lambda x: gagCount(x.bot, ATTACK_TRACKS) <= 0)
                while r.queue:
                    b = r.queue.pop(0)
                    if b.phase == 'in' and not b.echo and b.state == 'WaitForInput':
                        r.picking = b
                        r.pickT = now
                        b.choose()
                        break
        except Exception:
            self.error('pick tick', traceback.format_exc())
        return task.again

    def __audit(self, r):
        """The round's picks as made: the owner's rules checked (double lures, lure + sound, redundant heals,
        overkill), bot-made problems counted in STATS and logged to bots-picks.log."""
        try:
            from toontown.bots import gagplan
            brains = list(self.brains.get(r.doId, {}).values())
            if not brains or r.members is None:
                return
            b = brains[0]
            b.members = r.members
            board = b.board(withMine=True)
            # the queued order is the order the picks were echoed; the dict keeps insertion order
            players = set(t for t in r.members.toons if t not in self.director.bots)
            dbl, conf, red, kills, over, weak = gagplan.audit(board, players, STATS)
            gagplan.log(self.director, 'battle %s round %d AUDIT picks %s | double lures %d, lure+sound %d, '
                        'redundant heals %d, kills %d, overkill %d, danger weakest-target %d' % (
                            r.doId, r.rounds, ['%s:%s:%s>%s' % (p[0], NAMES_SHORT.get(p[1], p[1]), p[2], p[3])
                                               for p in board.picks], dbl, conf, red, kills, over, weak))
        except Exception:
            self.error('pick audit', traceback.format_exc())

    def __finishRecord(self, r):
        if not r.bots:
            return
        res = 'won' if r.reward else 'lost'
        STATS.count('battles_%s_%s' % (r.kind, res))
        STATS.count('battles_%s' % res)
        if r.players:
            STATS.count('battles_with_player_%s' % res)
        STATS.note('battle %s %s %s: %d rounds, bots %d, players %s, started by %s' % (
            r.kind, r.doId, res, r.rounds, len(r.bots), sorted(r.players), r.startedBy))

    # -- periodic: playground healing + sad toons home -------------------------------------------------
    def __tick(self, task):
        try:
            self.__heal()
        except Exception:
            self.error('hub tick', traceback.format_exc())
        return task.again

    def safeZoneMgr(self):
        v = self.director.mgmtView
        o = v.first('SafeZoneManager') if v is not None else None
        return o.doId if o is not None else None

    def __heal(self):
        """What the client's Playground does: SafeZoneManager.enterSafeZone on the way in (the AI then
        heals the toon every 30 s), exitSafeZone on the way out. A toon that went sad on a street goes
        to its playground (a teleport), as a player does."""
        d = self.director
        szm = self.safeZoneMgr()
        if szm is None:
            return
        for bot in list(d.bots.values()):
            if bot.state != 'present' or bot.area is None:
                if bot.avId in self.inSafe:
                    self.inSafe.discard(bot.avId)
                continue
            inPg = bot.area.kind == 'playground' and bot.zoneId == bot.area.id and bot.travel is None
            if inPg and bot.avId not in self.inSafe:
                bot.send('enterSafeZone', [], doId=szm, className='SafeZoneManager')
                self.inSafe.add(bot.avId)
                STATS.count('safezone_enter')
            elif not inPg and bot.avId in self.inSafe and bot.travel is None:
                bot.send('exitSafeZone', [], doId=szm, className='SafeZoneManager')
                self.inSafe.discard(bot.avId)
            if bot.area.kind == 'street' and bot.travel is None and \
                    (bot.activity is None or bot.activity.interruptible):
                hp, mx = botHp(bot)
                if hp is not None and hp <= 0:
                    self.goHeal(bot, 'sad')

    def goHeal(self, bot, why):
        d = self.director
        pg = d.world.areas.get(bot.area.hood) if bot.area is not None else None
        if pg is None or bot.travel is not None:
            return False
        if d.travel(bot, pg, why='P8 %s: to the playground' % why, via='teleport'):
            STATS.count('to_playground_' + why)
            return True
        return False

    # -- output --------------------------------------------------------------------------------------------
    def error(self, what, tb):
        STATS.count('errors')
        if self.director is not None:
            self.director.error('P8 ' + what, tb)
        else:
            notify.warning('[TTBOTS] %s: %s' % (what, tb))

    def __write(self, task):
        try:
            self.writeNow()
        except Exception:
            self.error('write', traceback.format_exc())
        return task.again

    def writeNow(self):
        d = self.director
        if d is None:
            return
        live = []
        for doId, r in self.records.items():
            live.append({'doId': doId, 'kind': r.kind, 'state': r.state, 'rounds': r.rounds,
                         'bots': len(r.bots), 'players': sorted(r.players), 'age': int(now_() - r.t0)})
        inBattle = sum(len(v) for v in self.brains.values())
        data = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'uptime': int(time.time() - STATS.started),
                'counters': dict(sorted(STATS.c.items())), 'live': live, 'botsInBattle': inBattle,
                'barrierLate': STATS.barrierLate, 'events': STATS.events[-30:]}
        blds = []
        try:
            from toontown.bots.activities import cogbuilding as cb
            for a in d.world.areas.values():
                if a.kind == 'street':
                    for b, e, door, fl in cb.suitBuildings(d, a):
                        blds.append([a.id, b.doId, door['extra'].get('block'), fl, (e.get('setState') or ('',))[0]])
        except Exception:
            self.error('buildings list', traceback.format_exc())
        data['suitBuildings'] = blds
        path = os.path.join(d.runDir, 'bots-battle.json')
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        c = STATS.c
        lines = ['TTBOTS P8 battles  %s  up %ds' % (data['time'], data['uptime']),
                 'street battles: started by bots %d (requests %d, denied %d), bots joined %d (asked %d, denied %d)' % (
                     c.get('street_started', 0), c.get('request_battle', 0), c.get('deny_battle', 0),
                     c.get('joined', 0), c.get('join_request', 0), c.get('deny_join', 0)),
                 'battles won %d lost %d (street %d/%d, bldg floors %d/%d), rounds %d, with a real player won %d lost %d' % (
                     c.get('battles_won', 0), c.get('battles_lost', 0), c.get('battles_street_won', 0),
                     c.get('battles_street_lost', 0), c.get('battles_bldg_won', 0), c.get('battles_bldg_lost', 0),
                     c.get('rounds', 0), c.get('battles_with_player_won', 0), c.get('battles_with_player_lost', 0)),
                 'attacks sent %d accepted %d rejected %d, passes %d, heals %d (on a real player %d), lures %d, traps %d, runs %d, sad %d' % (
                     c.get('attack_sent', 0), c.get('attack_accepted', 0), c.get('attack_rejected', 0),
                     c.get('attack_pass', 0), c.get('attack_heal', 0), c.get('heal_real_player', 0),
                     c.get('attack_lure', 0), c.get('attack_trap', 0), c.get('ran', 0), c.get('died', 0)),
                 'barriers closed %d, closed with a bot pending (server timeout) %d, late adjust acks %d' % (
                     c.get('barriers_closed', 0), c.get('barrier_timeout_bot_pending', 0), c.get('adjust_late', 0)),
                 'buildings: boarded %d, entered %d (bot-only %d), cleared %d (bot-only %d), floors cleared %d, tallest cleared %d' % (
                     c.get('bldg_boarded', 0), c.get('bldg_entered', 0), c.get('bldg_entered_botonly', 0),
                     c.get('bldg_cleared', 0), c.get('bldg_cleared_botonly', 0), c.get('bldg_floors', 0),
                     c.get('bldg_tallest', 0)),
                 'helpers sent to a real player\'s battle %d, joined it %d; bots in battle now %d; errors %d' % (
                     c.get('help_sent', 0), c.get('help_joined', 0), inBattle, c.get('errors', 0))]
        byFloors = {}
        for x in blds:
            byFloors[x[3]] = byFloors.get(x[3], 0) + 1
        lines.append('Cog buildings now on %d streets, by floors: %s' % (len(set(x[0] for x in blds)), sorted(byFloors.items())))
        lines += ['late: ' + x for x in STATS.barrierLate[-5:]]
        lines += ['  ' + e for e in STATS.events[-12:]]
        with open(os.path.join(d.runDir, 'bots-battle.txt'), 'w') as f:
            f.write('\n'.join(lines) + '\n')


HUB = Hub()


# ---- the brain -------------------------------------------------------------------------------------------
class BattleBrain:
    def __init__(self, bot, kind='street'):
        self.bot = bot
        self.air = bot.air
        self.kind = kind
        self.battleId = None
        self.obj = None
        self.className = None
        self.phase = 'idle'            # idle / starting / joining / in / done
        self.result = None
        self.until = 0.0
        self.members = None
        self.state = None
        self.round = 0
        self.chosen = None             # (track, level, target) I sent this round
        self.echo = False              # the AI showed my choice back (accepted)
        self.others = {}               # avId -> (track, level, target) chosen this round
        self.joinAcked = set()
        self.initiator = False
        self.tasks = []
        self.adjusts = 0
        self.movieArgs = None
        self.expArgs = None
        self.wasActive = False
        self.pos = None                # where the battle stands (street coords)
        self.suitPos = None

    # -- life -----------------------------------------------------------------------------------------------
    def later(self, delay, fn, *args):
        name = 'botbattle-%d-%d' % (self.bot.avId, random.randint(0, 1 << 30))

        def run(task):
            if name in self.tasks:
                self.tasks.remove(name)
            if self.phase in ('in', 'joining', 'starting') and self.bot.state == 'present':
                try:
                    fn(*args)
                except Exception:
                    HUB.error('brain %s' % getattr(fn, '__name__', fn), traceback.format_exc())
            return task.done
        self.tasks.append(name)
        taskMgr.doMethodLater(max(0.0, delay), run, name)

    def cancel(self):
        for name in self.tasks:
            taskMgr.remove(name)
        self.tasks = []

    def send(self, field, args):
        self.bot.send(field, args, doId=self.battleId, className=self.className)

    def record(self):
        return HUB.records.get(self.battleId)

    def startOn(self, suitObj, pos, h, className='DistributedSuit'):
        """Walked into a Cog: requestBattle (the suit's own position and heading, as the client sends)."""
        HUB.waitStart[self.bot.avId] = self
        self.phase = 'starting'
        self.initiator = True
        self.until = now_() + 8.0
        self.bot.send('requestBattle', [pos[0], pos[1], pos[2], h, 0.0, 0.0], doId=suitObj.doId,
                      className=className)
        STATS.count('request_battle')

    def join(self, battleObj):
        self.battleId = battleObj.doId
        self.obj = battleObj
        self.className = battleObj.className
        p = battleObj.get('setPosition') or (0, 0, 0)
        b = self.bot.pos
        self.phase = 'joining'
        self.until = now_() + 8.0
        HUB.register(self)
        self.send('toonRequestJoin', [b[0] - p[0], b[1] - p[1], b[2] - p[2]])
        STATS.count('join_request')

    def attach(self, battleObj):
        self.battleId = battleObj.doId
        self.obj = battleObj
        self.className = battleObj.className
        if self.phase != 'joining':
            self.phase = 'joining'
            self.until = now_() + 8.0
        HUB.register(self)
        r = HUB.record(self.battleId, self.kind)
        r.bots.add(self.bot.avId)
        if self.initiator and r.startedBy is None:
            r.startedBy = 'bot'
        # catch up with what the battle already is
        if battleObj.get('setMembers'):
            self.onField(battleObj, 'setMembers', battleObj.get('setMembers'))
        st = battleObj.get('setState')
        if st:
            self.catchUp = now_() - (HUB.records[self.battleId].stateT if self.battleId in HUB.records else now_())
            self.onField(battleObj, 'setState', st)
            self.catchUp = 0.0

    def step(self, now):
        if self.phase in ('starting', 'joining') and now > self.until:
            if HUB.waitStart.get(self.bot.avId) is self:
                del HUB.waitStart[self.bot.avId]
            self.done('no-battle' if self.phase == 'starting' else 'join-timeout')
        return self.phase != 'done'

    def done(self, result):
        if self.phase == 'done':
            return
        self.cancel()
        self.phase = 'done'
        self.result = result
        if HUB.waitStart.get(self.bot.avId) is self:
            del HUB.waitStart[self.bot.avId]
        r = self.record()
        if r is not None:
            r.drop(self.bot.avId)
        if self.battleId is not None:
            HUB.unregister(self)
        STATS.count('brain_' + result)
        if result == 'died':
            STATS.count('died')

    def leave(self, why):
        self.done('left-' + why)

    def onGone(self):
        r = self.record()
        self.done('won' if r is not None and r.reward else 'gone')

    def onDirect(self, fieldName, args):
        if fieldName == 'denyBattle' and self.phase == 'starting':
            STATS.count('deny_battle')
            self.done('denied')
        elif fieldName == 'denyLocalToonJoin' and self.phase == 'joining':
            STATS.count('deny_join')
            self.done('denied')

    def myIndexPos(self):
        """Where the battle put me (its toonPoints, rotated to face the Cogs), for after the battle."""
        from toontown.battle.BattleBase import BattleBase
        o = self.obj
        if o is None or self.members is None:
            return None
        p = o.get('setPosition')
        s = o.get('setInitialSuitPos')
        if not p or not s:
            return None
        toons = [t for t in self.members.activeToons] or self.members.toons
        if self.bot.avId not in toons or len(toons) > 4:
            return Point3(p[0], p[1], p[2])
        i = toons.index(self.bot.avId)
        pt = BattleBase.toonPoints[len(toons) - 1][i][0]
        dx, dy = s[0] - p[0], s[1] - p[1]
        h = math.atan2(-dx, dy)
        c, sn = math.cos(h), math.sin(h)
        return Point3(p[0] + pt[0] * c - pt[1] * sn, p[1] + pt[0] * sn + pt[1] * c, p[2])

    # -- the battle's broadcasts --------------------------------------------------------------------------------
    def onField(self, obj, fieldName, args):
        if self.phase == 'done':
            return
        me = self.bot.avId
        r = HUB.record(self.battleId, self.kind)
        if fieldName == 'setMembers':
            m = Members(args)
            self.members = m
            if me in m.toons:
                if self.phase == 'joining':
                    self.phase = 'in'
                    r.bots.add(me)
                    if not self.initiator and self.kind == 'street':
                        STATS.count('joined')
                    elif self.kind == 'street':
                        STATS.count('street_started')
                        HUB.recent.append(now_())
                        if r.startedBy is None:
                            r.startedBy = 'bot'
                # a joining Cog or toon: every toon in the battle acks its walk-in (22 s on the AI)
                for av in m.joiningSuits + m.joiningToons:
                    if av not in self.joinAcked:
                        self.joinAcked.add(av)
                        r.need(('join', av), me)
                        self.later(self.__joinTime(av), self.__joinDone, av)
                if self.state == 'WaitForInput' and me in m.activeToons and not self.wasActive:
                    self.wasActive = True
                    self.__startThinking(r, first=False)
            elif self.phase == 'in':
                hp, mx = botHp(self.bot)
                self.done('died' if hp is not None and hp <= 0 else ('ran' if self.result == 'running' else 'removed'))
        elif fieldName == 'setState':
            self.__onState(r, args[0])
        elif fieldName == 'adjust':
            if self.members is not None and me in self.members.toons:
                est = self.__adjustEstimate()
                self.adjusts += 1
                delay = est + random.uniform(0.2, 0.9)
                if delay >= est + BB_SERVER_BUFFER:
                    STATS.count('adjust_late')
                self.later(delay, self.__adjustDone)
        elif fieldName == 'setMovie':
            if args[0] == 1:
                self.movieArgs = args
        elif fieldName == 'setChosenToonAttacks':
            ids, tracks, levels, targets = args
            self.others = {}
            for i, av in enumerate(ids):
                if av == me:
                    if self.chosen is not None and not self.echo and tracks[i] == self.chosen[0]:
                        self.echo = True
                        STATS.count('attack_accepted')
                        r.acked(('input', self.round), me)
                    elif self.chosen is not None and self.echo and tracks[i] == UN_ATTACK:
                        # my heal target ran (the AI reset my choice): choose again
                        self.echo = False
                        self.chosen = None
                        self.later(random.uniform(1.0, 2.5), self.__choose, self.round)
                else:
                    self.others[av] = (tracks[i], levels[i], targets[i])
        elif fieldName == 'setBattleExperience':
            self.expArgs = args

    def __onState(self, r, st):
        me = self.bot.avId
        self.state = st
        m = self.members
        inIt = m is not None and me in m.toons
        if st == 'FaceOff':
            if inIt and (self.kind != 'street' or (self.initiator and m.toons and m.toons[0] == me)):
                r.need(('faceoff',), me)
                # joined mid-FaceOff (attached late): the faceoff already ran that long
                left = self.__faceoffTime() - getattr(self, 'catchUp', 0.0)
                self.later(max(0.3, left) + random.uniform(0.3, 1.0), self.__faceOffDone)
        elif st == 'WaitForInput':
            self.round += 1
            self.chosen = None
            self.echo = False
            self.others = {}
            self.wasActive = inIt and me in m.activeToons
            if self.wasActive:
                self.__startThinking(r, first=True)
        elif st == 'PlayMovie':
            if inIt and (me in m.activeToons or me in m.pendingToons) and self.movieArgs is not None:
                r.need(('movie', self.round), me)
                t = movieLength(self.movieArgs)
                # a real player's movie must never be cut short: late is fine (the AI waits 5 s after
                # his ack), early is not
                if self.__realIn():
                    # P8c: never before him. The first movieDone gives everyone else 5 s (TIMEOUT_PER_USER), and in
                    # boss battles his real movie ran 2-3x this estimate (43.8 s against ~12), so a bot's ack cut
                    # it short and the cut movie's finish() hit an empty NodePath on his client (a crash). A bot
                    # acks only near the AI's own bound (12 s a toon + 12 s a Cog + 2); his own ack ends the
                    # wait 5 s later, so the round never waits on the bots by more than that.
                    m = self.members
                    bound = 12.0 * len(m.activeToons) + 12.0 * len(getattr(m, 'activeSuits', m.suits)) + 2.0
                    delay = max(t + random.uniform(4.0, 6.0), bound - random.uniform(2.5, 4.0))
                else:
                    delay = t + random.uniform(0.5, 1.5)
                self.later(delay, self.__movieDone)
                self.movieArgs = None
        elif st in REWARD_ACK.get(self.kind, ('Reward',)):
            if inIt and me in m.activeToons:
                r.need(('reward',), me)
                t = rewardLength(self.expArgs) if self.expArgs else 8.0
                pad = (1.5, 2.5) if self.__realIn() else (0.8, 2.0)
                self.later(t + random.uniform(*pad), self.__rewardDone)
        elif st == 'Resume':
            self.done('won' if r.reward else 'over')

    # -- acks -----------------------------------------------------------------------------------------------------
    def __ack(self, key, field, args):
        self.send(field, args)
        r = self.record()
        if r is not None:
            r.acked(key, self.bot.avId)

    def __faceOffDone(self):
        if self.state != 'FaceOff':
            return
        self.__ack(('faceoff',), 'faceOffDone', [])

    def __joinDone(self, av):
        self.__ack(('join', av), 'joinDone', [av])

    def __adjustDone(self):
        self.send('adjustDone', [])
        STATS.count('ack_adjust')

    def __movieDone(self):
        if self.state != 'PlayMovie':
            return                         # the AI already moved on (counted by the Record)
        self.__ack(('movie', self.round), 'movieDone', [])
        hp, mx = botHp(self.bot)
        if hp is not None and hp <= 0:
            self.send('toonDied', [])           # the client's 'died' event (the AI ignores it once removed)

    def __rewardDone(self):
        if self.state not in ALL_REWARDS:
            return
        self.__ack(('reward',), 'rewardDone', [])

    def __faceoffTime(self):
        if self.kind != 'street':
            return 30.0 / TOON_SPEED + FACEOFF_TAUNT
        o = self.obj
        p = Point3(*(o.get('setPosition') or (0, 0, 0)))
        s = Point3(*(o.get('setInitialSuitPos') or (0, 0, 0)))
        facing = p - s
        if facing.length() < 0.01:
            return FACEOFF_TAUNT
        facing.normalize()
        dest = p - facing * 6.0
        return (dest - s).length() / SUIT_SPEED + FACEOFF_TAUNT

    def __adjustEstimate(self):
        m = self.members
        if m is None:
            return 0.5
        if m.pendingSuits:
            return math.hypot(-4 - 0, 8.2 - 5) / SUIT_SPEED
        if m.pendingToons:
            return math.hypot(-3 - 0, -8 + 6) / TOON_SPEED
        return 0.3

    def __joinTime(self, av):
        """The client's join interval: the joiner walks from where it stands to its pending spot."""
        o = objOf(self.air, av)
        p = self.obj.get('setPosition') if self.obj is not None else None
        if o is not None and p and o.className == 'DistributedToon':
            d = math.hypot(o.pos[0] - p[0], o.pos[1] - p[1])
            return min(14.0, max(1.5, d / TOON_SPEED + 1.0)) + random.uniform(0.3, 1.2)
        return random.uniform(3.0, 5.5)

    # -- choosing a gag -----------------------------------------------------------------------------------------
    def __startThinking(self, r, first):
        """P8b: no bot picks on its own clock any more: the Hub's pick order calls choose() when it is this
        bot's turn (after the real players, one bot at a time, each seeing every pick queued)."""
        me = self.bot.avId
        r.need(('input', self.round), me)
        if self not in r.queue:
            r.queue.append(self)
        left = max(2.0, PICK_DEADLINE - (now_() - r.roundT))
        self.later(left, self.__fallback, self.round)

    def choose(self):
        self.__choose(self.round)

    def __fallback(self, rnd):
        """Never make the battle wait on me: no answer echoed by now -> PASS (a valid response)."""
        if rnd == self.round and self.state == 'WaitForInput' and not self.echo and self.members and self.bot.avId in self.members.activeToons:
            if self.chosen is not None:
                STATS.count('attack_rejected')
                STATS.note('attack %s by %s got no echo: PASS' % (self.chosen, self.bot.avId))
            else:
                STATS.note('bot %s never chose in round %d (state %s): PASS' % (self.bot.avId, self.round, self.state))
            self.chosen = (PASS, -1, -1)
            self.echo = True
            self.__ack(('input', self.round), 'requestAttack', [PASS, -1, -1])
            STATS.count('attack_pass')
            STATS.count('attack_pass_fallback')

    def __choose(self, rnd=None):
        me = self.bot.avId
        m = self.members
        if rnd is not None and rnd != self.round:
            return                         # scheduled for a round that is already over
        if self.state != 'WaitForInput' or m is None or me not in m.activeToons or self.echo:
            if not self.echo:
                STATS.note('bot %s choose skipped: state %s active %s round %d' % (
                    me, self.state, m is not None and me in m.activeToons, self.round))
            return
        if self.__shouldRun():
            # the battle must be runable: WaitForInput is
            self.result = 'running'
            self.send('toonRequestRun', [])
            STATS.count('ran')
            r = self.record()
            if r is not None:
                r.acked(('input', self.round), me)
            return
        try:
            track, level, target = self.__plan()
        except Exception:
            HUB.error('gag plan', traceback.format_exc())
            track, level, target = pickAttack(self)
        self.chosen = (track, level, target)
        self.echo = track == PASS
        STATS.count('attack_sent')
        if track == PASS:
            STATS.count('attack_pass')
            inv, exp = loadInventory(self.bot)
            STATS.note('bot %s PASS: gags %s' % (me, [[inv.numItem(t, l) for l in range(7)] for t in range(7)] if inv else None))
            if inv is not None and not getattr(self, 'saidOut', False) and \
                    sum(inv.numItem(t, l) for t in ATTACK_TRACKS for l in range(7)) == 0:
                self.saidOut = True             # the owner 09-30: "if they run out of gags, they say I'm out of gags"
                self.bot.say(1413)              # "I need more gags."
                STATS.count('said_out_of_gags')
        else:
            STATS.count('attack_' + TRACK_NAMES[track])
            if track == HEAL and target not in HUB.director.bots and target != -1:
                STATS.count('heal_real_player')
                STATS.note('bot %s heals real player %s (level %d)' % (me, target, level))
        if track == PASS:
            self.__ack(('input', self.round), 'requestAttack', [track, level, target])
        else:
            # an attack only counts as a response once the AI echoes it (setChosenToonAttacks);
            # a silent reject (no such gag) is covered by a PASS 2.5 s later
            self.send('requestAttack', [track, level, target])
            self.later(2.5, self.__checkEcho, self.round)

    def __checkEcho(self, rnd):
        if rnd != self.round or self.echo or self.state != 'WaitForInput':
            return
        STATS.count('attack_rejected')
        STATS.note('attack %s by %s not echoed (rejected?): PASS' % (self.chosen, self.bot.avId))
        self.chosen = (PASS, -1, -1)
        self.echo = True
        self.__ack(('input', self.round), 'requestAttack', [PASS, -1, -1])
        STATS.count('attack_pass')

    def board(self, withMine=False):
        """gagplan.Board of this round from the battle's own broadcasts (the picks the AI echoed)."""
        from toontown.bots import gagplan
        m, r, d = self.members, self.record(), HUB.director
        me = self.bot.avId
        chosen = dict((r.chosen if r is not None else {}))
        if not withMine:
            chosen.pop(me, None)
        suitHp = {}
        for s in m.activeSuits:
            hp = r.suitHp.get(s) if r is not None else None
            if hp is None:
                o = objOf(self.air, s)
                hp = (o.get('setHP') or (0,))[0] if o is not None else 0
            suitHp[s] = max(0, hp)
        toonHpD, toonMax = {}, {}
        for t in m.activeToons:
            hp, mx = toonHp(self.air, t)
            if t == me:
                hp, mx = botHp(self.bot)
            if r is not None and t in r.toonHp:
                hp = r.toonHp[t]
            toonHpD[t], toonMax[t] = hp, mx

        def expOf(av):
            b = d.bots.get(av)
            return loadInventory(b)[1] if b is not None else None
        board = gagplan.Board(self, chosen, m.activeSuits, suitHp, m.activeToons, toonHpD, toonMax,
                              m.luredSuits, m.traps, expOf)
        # (09-30) what each bot still carries, so a bot knows whether anyone can cash a lure in (gagplan.canCash);
        # a real player's pouch is not ours to read: None (unknown)
        board.pouches = {}
        for t in m.activeToons:
            b = d.bots.get(t)
            inv = loadInventory(b)[0] if b is not None else None
            board.pouches[t] = None if inv is None else dict(
                (x, [l for l in range(7) if inv.numItem(x, l) > 0]) for x in range(7)
                if any(inv.numItem(x, l) > 0 for l in range(7)))
        board.threat = {}
        for s in m.activeSuits:
            o = objOf(self.air, s)
            if o is not None and o.get('setDNAString'):
                board.threat[s] = gagplan.suitThreat(o.get('setDNAString')[0], (o.get('setLevelDist') or (1,))[0])
                board.defense[s] = gagplan.cogDefense(o.get('setDNAString')[0], (o.get('setLevelDist') or (0,))[0])
        return board

    def __plan(self):
        from toontown.bots import gagplan
        inv, exp = loadInventory(self.bot)
        m, d = self.members, HUB.director
        if inv is None or m is None:
            return PASS, -1, -1
        have = {}
        for t in range(7):
            for l in range(7):
                if inv.numItem(t, l) > 0:
                    have.setdefault(t, []).append(l)
        board = self.board()
        players = set(t for t in m.toons if t not in d.bots)
        r = self.record()
        inDanger, left, weight, atRisk = gagplan.danger(board)
        if not board.picks or not getattr(r, 'dangerLogged', None) == self.round:
            if r is not None:
                r.dangerLogged = self.round
            gagplan.log(d, 'battle %s round %d DANGER %s: %d Cogs able to attack (threat %d, biggest hit %d) vs laff %s; at risk %s' % (
                self.battleId, self.round, 'YES' if inDanger else 'no', len(left), int(weight),
                int(max([board.threat.get(s, (0, 0, 0))[1] for s in left] or [0])),
                dict((t, board.toonHp.get(t)) for t in board.toons), atRisk))
        self.__callForHelp(r, inDanger, len(board.toons))
        t, l, tg, why = gagplan.plan(self, board, have, exp, players, d.bots)
        r = self.record()
        order = len(board.picks) + 1
        rem = board.remaining().get(tg) if tg in board.suits else None
        q = board.healQueued()
        tgo = objOf(self.air, tg) if tg not in (-1, None) else None
        # setLevelDist is the Cog's table row, not its level (09-25 fix): the log shows the level a player sees
        lvlOf = lambda o: gagplan.cogLevel((o.get('setDNAString') or (b'',))[0], (o.get('setLevelDist') or (0,))[0])
        cogLvl = lvlOf(tgo) if tgo is not None and tgo.get('setLevelDist') else \
            max([lvlOf(objOf(self.air, s)) for s in board.suits if objOf(self.air, s)] or [0])
        left = [sum(inv.numItem(tt, ll) for ll in range(7)) - (1 if tt == t else 0) for tt in range(7)]
        gagplan.log(d, 'battle %s round %d pick %d bot %s tier %s vs cog level %s, gags left h/tr/lu/so/th/sq/dr %s' % (
            self.battleId, self.round, len(board.picks) + 1, self.bot.avId, (l + 1) if 0 <= t <= 6 else '-', cogLvl,
            '/'.join(str(x) for x in left)))
        gagplan.log(d, 'battle %s round %d pick %d bot %s -> %s %s on %s (%s) | queued before: %s | %s' % (
            self.battleId, self.round, order, self.bot.avId, gagplan.NAMES[t] if 0 <= t <= 6 else 'pass', l, tg,
            why, ' '.join('%s:%s%s' % (NAMES_SHORT.get(p[1], p[1]), p[2], '' if p[0] in d.bots else '(player)')
                          for p in board.picks) or 'nothing',
            ('cog %s has %s left of %s' % (tg, int(rem), board.suitHp.get(tg))) if rem is not None
            else ('heal queued %s' % sum(q.values()))))
        return t, l, tg

    def __callForHelp(self, r, inDanger, nToons, force=False):
        """The owner (09-25): a bot in a fight it can't handle says so - "Help!" in danger, "We are in trouble."
        when the fight drags on (round 4+) - and every bot that can come runs over (activities/helpcall.py); one
        call per battle every 25 s, only on a street (a building's or HQ's battle can't be joined from outside) and
        only while there is a free spot. force (09-29): a bot losing its fight calls before it runs (__shouldRun)."""
        if self.kind != 'street' or (nToons >= 4 and not force) or r is None or not (force or inDanger or self.round >= 4):
            return
        now = now_()
        if not force and now - getattr(r, 'helpSaidAt', -1e9) < 25.0:
            return
        r.helpSaidAt = now
        r.helpRound = self.round
        try:
            from toontown.bots.activities import lifekit as kit
            from toontown.bots.activities import helpcall
            kit.say(self.bot, 1410 if inDanger else 1412, force=True, answer=False)
            STATS.count('help_said')
            helpcall.HELP.call(self.bot.avId, self.bot.zoneId, 'danger' if inDanger else 'round %d' % self.round)
        except Exception:
            HUB.error('help call', traceback.format_exc())

    def __realIn(self):
        return bool(self.members) and any(t not in HUB.director.bots for t in self.members.toons)

    def __shouldRun(self):
        """The owner (09-29): a bot runs from a fight it is losing (gagplan.losing) - but never while a real player is
        in the fight, and only in a street battle (never a boss - VP, CFO, CJ, CEO - nor a building / Cog HQ battle,
        where running strands the rest of the run). It calls for help first when there is a free spot; it runs when
        the next hit sends it sad, or when it is still losing a round after its call."""
        if self.kind != 'street' or self.__realIn():
            return False
        from toontown.bots import gagplan
        inv, exp = loadInventory(self.bot)
        m, r, d = self.members, self.record(), HUB.director
        if inv is None or m is None or r is None:
            return False
        have = {}
        for t in range(7):
            for l in range(7):
                if inv.numItem(t, l) > 0:
                    have.setdefault(t, []).append(l)
        me = self.bot.avId
        healers = any(t != me and t in d.bots and gagCount(d.bots[t], (HEAL,)) > 0 for t in m.activeToons)
        verdict = gagplan.losing(self.board(), me, have, exp, healers)
        if verdict is None:
            return False
        urgent, why = verdict
        # the owner (09-29): always a call for help before a run - said a round ahead when there is time; at one
        # hit from sad it says it and runs the same turn
        if getattr(self, 'myHelpRound', None) is None:       # each bot calls for itself before it runs
            self.myHelpRound = self.round
            self.__callForHelp(r, True, len(m.activeToons), force=True)
            if not urgent:
                return False
        elif not urgent and self.myHelpRound == self.round:
            return False                  # called this round: run next round if it is still lost
        STATS.count('ran_losing')
        gagplan.log(d, 'battle %s round %d RUN bot %s: %s' % (self.battleId, self.round, me, why))
        return True

    def __healerNear(self):
        return any(a[0] == HEAL for a in self.others.values())


BB_SERVER_BUFFER = 2.0
PICK_DEADLINE = 19.3         # P8b: every bot has picked (or passed) this long into WaitForInput (the AI waits 22 s)
NAMES_SHORT = dict(enumerate(TRACK_NAMES))


def pickAttack(brain):
    """A good kid's gag choice -> (track, level, target) (see the module doc)."""
    from toontown.battle.BattleBase import attackAffectsGroup
    from toontown.toonbase import ToontownBattleGlobals as TBG
    bot, m = brain.bot, brain.members
    inv, exp = loadInventory(bot)
    if inv is None or m is None:
        return PASS, -1, -1
    have = {}
    for t in range(7):
        for l in range(7):
            if inv.numItem(t, l) > 0:
                have.setdefault(t, []).append(l)
    if not have:
        return PASS, -1, -1
    me = bot.avId
    d = HUB.director
    r = brain.record()
    suits = list(m.activeSuits)
    if not suits:
        return PASS, -1, -1

    def dmg(t, l):
        try:
            return TBG.getAvPropDamage(t, l, exp.getExp(t))
        except Exception:
            return TBG.AvPropDamage[t][l][0][0]

    def group(t, l):
        return bool(attackAffectsGroup(t, l))

    hpLeft = {}
    for s in suits:
        hp = r.suitHp.get(s) if r is not None else None
        if hp is None:
            o = objOf(bot.air, s)
            hp = (o.get('setHP') or (0,))[0] if o is not None else 0
        hpLeft[s] = max(0, hp)
    luring, trapping, targeted = set(), set(), {}
    groupLure = False
    for av, (t, l, tg) in brain.others.items():
        if t < 0 or t > DROP:
            continue
        if t == LURE:
            if group(t, l):
                groupLure = True
            else:
                luring.add(tg)
        elif t == TRAP:
            trapping.add(tg)
        elif t in ATTACK_TRACKS:
            base = TBG.AvPropDamage[t][l][0][0]
            if group(t, l):
                for s in suits:
                    hpLeft[s] = max(0, hpLeft[s] - base)
            elif tg in hpLeft:
                hpLeft[tg] = max(0, hpLeft[tg] - base)
                targeted.setdefault(tg, []).append(t)
    lured = set(m.luredSuits)

    # 1. heal the lowest ally below ~50% (60% for a real player); never myself (toon-up can't)
    if HEAL in have:
        hurt = []
        healed = set(tg for (t, l, tg) in brain.others.values() if t == HEAL)
        for t in m.activeToons:
            if t == me or t in m.runningToons or t in healed:
                continue
            hp, mx = toonHp(bot.air, t)
            if r is not None and t in r.toonHp:
                hp = r.toonHp[t]
            if hp is None or not mx or hp <= 0:
                continue
            real = t not in d.bots
            # P8b: Cog HQ Cogs hit much harder than street Cogs: a kid there heals sooner
            if hp < mx * (0.6 if real else (0.65 if brain.kind in ('level', 'boss') else 0.5)):
                hurt.append((hp / float(mx) - (0.15 if real else 0.0), t, mx - hp))
        if hurt:
            hurt.sort()
            frac, t, need = hurt[0]
            singles = [l for l in have[HEAL] if not group(HEAL, l)]
            groups = [l for l in have[HEAL] if group(HEAL, l)]
            if singles:
                enough = [l for l in singles if dmg(HEAL, l) >= need]
                l = min(enough) if enough else max(singles)
                return HEAL, l, t
            if groups and len(m.activeToons) >= 2:
                return HEAL, max(groups), -1

    # 2. lure when many Cogs (never one lured, or being lured)
    unlured = [s for s in suits if s not in lured and s not in luring and not groupLure]
    if LURE in have and not groupLure and len(unlured) >= (2 if brain.kind in ('level', 'boss') else 3) \
            and random.random() < 0.7:
        gl = [l for l in have[LURE] if group(LURE, l)]
        if gl:
            return LURE, max(gl), -1
        sl = [l for l in have[LURE] if not group(LURE, l)]
        if sl:
            s = max(unlured, key=lambda x: hpLeft[x])
            return LURE, max(sl), s
    if LURE in have and not groupLure and len(unlured) >= 2 and random.random() < 0.2:
        sl = [l for l in have[LURE] if not group(LURE, l)]
        if sl:
            s = max(unlured, key=lambda x: hpLeft[x])
            return LURE, max(sl), s

    # 3. trap a Cog someone is luring now (never one already trapped, lured, or being trapped)
    if TRAP in have and (luring or groupLure):
        cands = [s for s in suits if (groupLure or s in luring) and s not in m.traps and s not in lured
                 and s not in trapping]
        if cands:
            s = max(cands, key=lambda x: hpLeft[x])
            return TRAP, max(have[TRAP]), s

    # the attack gags I may use on Cog s
    def usable(s):
        out = []
        for t in ATTACK_TRACKS:
            if t not in have:
                continue
            if t == DROP and s in lured:
                continue                  # a drop misses a lured Cog
            if t == SOUND and lured:
                continue                  # sound would wake the lured ones
            for l in have[t]:
                out.append((t, l))
        return out

    alive = [s for s in suits if hpLeft[s] > 0] or suits
    # 4. finish off a low Cog with the smallest gag that does it
    for s in sorted(alive, key=lambda x: hpLeft[x]):
        kills = [(dmg(t, l), t, l) for (t, l) in usable(s) if dmg(t, l) >= hpLeft[s] and not group(t, l)]
        if kills and hpLeft[s] > 0:
            kills.sort()
            _, t, l = kills[0]
            return t, l, s

    # 5. join a teammate's Cog with the same track (the combo bonus)
    for s, tracks in targeted.items():
        if s in alive and hpLeft.get(s, 0) > 0:
            for t in tracks:
                ls = [l for (tt, l) in usable(s) if tt == t and not group(tt, l)]
                if ls:
                    return t, max(ls), s
            opts = [(dmg(t, l), t, l) for (t, l) in usable(s) if not group(t, l)]
            if opts:
                opts.sort()
                _, t, l = opts[-1]
                return t, l, s

    # 6. Sound for a crowd, else my best gag on the weakest Cog (lured ones take a throw/squirt knockback)
    hq = brain.kind in ('level', 'boss')       # P8b: Cog HQ Cogs have 3-4x the laff: a kid plays it safer
    if SOUND in have and len(alive) >= (2 if hq else 3) and not lured:
        return SOUND, max(have[SOUND]), -1
    target = min(alive, key=lambda x: hpLeft[x] - (20 if x in lured else 0))
    opts = [(dmg(t, l), t, l) for (t, l) in usable(target) if not group(t, l)]
    if opts:
        opts.sort()
        # a kid keeps the very best for later now and then
        pick = opts[-1] if len(opts) == 1 or hq or random.random() < 0.7 else opts[-2]
        return pick[1], pick[2], target
    if SOUND in have and not lured:
        return SOUND, max(have[SOUND]), -1
    return PASS, -1, -1
