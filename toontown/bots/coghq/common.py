"""P8b shared plumbing: the facility / boss table, barrier acks, elevator rides, the run log.

BARRIERS. Every scripted phase in a boss battle (and a boss battle's ReservesJoining) waits in
DistributedObjectAI.beginBarrier: the AI broadcasts setBarrierData([(context, name, avIds)]) and each
listed client sends setBarrierReady(context) when its own movie for that phase is over
(DistributedObject.doneBarrier). A bot does the same, after the length of the client's movie
(DELAYS, always inside the AI's timeout). A bot never acks a barrier it is not listed in, never acks
twice, and never acks 'allToonsGone' (the level's leave barrier: the client acks it on its way out).

ELEVATORS. Boarding is the client's requestBoard; the seat comes back as fillSlotN(avId); the ride
ends with the elevator's set<Where>Zone(zoneId) sent to the avatar (onDirect).

RUN LOG. <run>/bots-coghq.json/.txt: every facility / boss run with its members, timings, result,
and the barrier timeouts the AI logged against a bot (Barrier '... timeout expired' in ai.log is the
authority; this file keeps what the bots saw).
"""
import json
import os
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal

from toontown.bots import battlebrain as bb

notify = DirectNotifyGlobal.directNotify.newCategory('BotCogHQ')

C, L, M, S = range(4)
# facility kind -> what the client does to get in and where it comes out
FACILITIES = {
    'factory': {'dept': S, 'ext': 11200, 'hq': 11000, 'elevClass': 'DistributedFactoryElevatorExt',
                'zoneField': 'setFactoryInteriorZone', 'label': 'Sellbot Factory'},
    'mint': {'dept': M, 'ext': 12000, 'hq': 12000, 'elevClass': 'DistributedMintElevatorExt',
             'zoneField': 'setMintInteriorZone', 'label': 'Cashbot Mint'},
    'stage': {'dept': L, 'ext': 13200, 'hq': 13000, 'elevClass': 'DistributedLawOfficeElevatorExt',
              'zoneField': 'setLawOfficeInteriorZone', 'label': 'DA Office'},
    'cgc': {'dept': C, 'ext': 10000, 'hq': 10000, 'elevClass': 'DistributedCogKart',
            'zoneField': 'setCountryClubInteriorZone', 'label': 'Cog Golf Course'},
}
BOSSES = {
    'vp': {'dept': S, 'lobby': 11100, 'hq': 11000, 'elevClass': 'DistributedVPElevator',
           'bossClass': 'DistributedSellbotBoss', 'label': 'VP'},
    'cfo': {'dept': M, 'lobby': 12100, 'hq': 12000, 'elevClass': 'DistributedCFOElevator',
            'bossClass': 'DistributedCashbotBoss', 'label': 'CFO'},
    'cj': {'dept': L, 'lobby': 13100, 'hq': 13000, 'elevClass': 'DistributedCJElevator',
           'bossClass': 'DistributedLawbotBoss', 'label': 'CJ'},
    'ceo': {'dept': C, 'lobby': 10100, 'hq': 10000, 'elevClass': 'DistributedBBElevator',
            'bossClass': 'DistributedBossbotBoss', 'label': 'CEO'},
}
ELEV_CLASSES = dict([(v['elevClass'], k) for k, v in FACILITIES.items()] +
                    [(v['elevClass'], k) for k, v in BOSSES.items()])
BOARDABLE = ('waitEmpty', 'waitCountdown')


def now_():
    return globalClock.getRealTime()


def seats(elev, n=8):
    out = []
    for i in range(n):
        f = elev.get('fillSlot%d' % i)
        out.append(f[0] if f else 0)
    return out


def elevState(elev):
    return (elev.get('setState') or ('',))[0]


# ---- barriers ------------------------------------------------------------------------------------------
# (className, barrier name) -> (lo, hi) seconds the client's movie for it takes (ack after), or None
# = never ack (the client acks it only on its way out). Defaults by name, then 0.6-0.8 of the timeout.
DELAYS = {
    'WaitForToons': (0.4, 1.1),              # the client acks once every toon is generated
    'Elevator': (9.0, 12.0),                 # two elevator rides + the doors (ElevatorUtils)
    'Introduction': (24.0, 34.0),
    'RollToBattleTwo': (18.0, 26.0),
    'PrepareBattleTwo': (10.0, 16.0),
    'RollToBattleThree': (9.0, 14.0),
    'PrepareBattleThree': (12.0, 20.0),
    'PrepareBattleFour': (14.0, 22.0),
    'Victory': (6.0, 8.5),
    'Defeat': (6.0, 8.0),
    'ReservesJoining': (5.0, 8.0),           # the reserves walk out of the doors (DistributedBattleFinal)
    'allToonsGone': None,
}
TIMEOUTS = {'WaitForToons': 5, 'Elevator': 30, 'Introduction': 45, 'RollToBattleTwo': 45,
            'PrepareBattleTwo': 30, 'PrepareBattleThree': 30, 'Victory': 10, 'ReservesJoining': 15,
            'Defeat': 10, 'RollToBattleThree': 20, 'PrepareBattleFour': 45}


class Barriers:
    """Acks every barrier a bot is listed in on the objects it watches (one per bot run)."""

    def __init__(self, bot, delayFn=None, log=None):
        self.bot = bot
        self.delayFn = delayFn          # fn(obj, name) -> seconds or None (overrides DELAYS)
        self.log = log                  # RunLog
        self.done = set()               # (doId, context) acked or scheduled
        self.pending = {}               # (doId, context) -> (name, sentAt or None, seenAt)
        self.watching = []
        self.stopped = False
        self.held = {}                  # name -> True: wait for release(name) before acking

    def watch(self, obj):
        if obj.doId in self.watching:
            return
        self.watching.append(obj.doId)
        bb.HUB.watch(obj.doId, self.__field)
        data = obj.get('setBarrierData')
        if data:
            self.__field(obj, 'setBarrierData', data)

    def stop(self):
        self.stopped = True
        for doId in self.watching:
            bb.HUB.unwatch(doId, self.__field)
        self.watching = []

    def __field(self, obj, fieldName, args):
        if fieldName != 'setBarrierData' or self.stopped:
            return
        me = self.bot.avId
        for context, name, avIds in args[0]:
            key = (obj.doId, context)
            if me not in avIds or key in self.done:
                continue
            self.done.add(key)
            delay = self.delayFn(obj, name) if self.delayFn is not None else None
            if delay is None:
                d = DELAYS.get(name, 'default')
                if d is None:
                    continue
                if d == 'default':
                    t = TIMEOUTS.get(name, 30)
                    d = (0.55 * t, 0.75 * t)
                delay = random.uniform(*d)
            if delay < 0:
                continue                   # the brain acks this one itself (release)
            self.pending[key] = [name, None, now_()]
            self.__later(delay, key, obj)

    def ackNow(self, obj, name):
        """Ack a held barrier now (a phase whose end the bot decides, e.g. a serving round)."""
        for key, p in list(self.pending.items()):
            if key[0] == obj.doId and p[0] == name and p[1] is None:
                self.__send(key, obj)

    def __later(self, delay, key, obj):
        def run(task):
            if not self.stopped and self.bot.state == 'present':
                self.__send(key, obj)
            return task.done
        taskMgr.doMethodLater(delay, run, 'botbarrier-%d-%d-%d' % (self.bot.avId, key[0], key[1]))

    def __send(self, key, obj):
        p = self.pending.get(key)
        if p is None or p[1] is not None:
            return
        p[1] = now_()
        self.bot.send('setBarrierReady', [key[1]], doId=key[0], className=obj.className)
        bb.STATS.count('hq_barrier_ack')
        if self.log is not None:
            self.log.barrier(p[0], p[1] - p[2])


# ---- the run log --------------------------------------------------------------------------------------
class RunLog:
    RUNS = []                          # every run this process (newest last, capped)
    NEXT = [1]

    def __init__(self, kind, where, members, withPlayer=False):
        self.id = RunLog.NEXT[0]
        RunLog.NEXT[0] += 1
        self.kind = kind
        self.where = where
        self.members = list(members)
        self.players = []
        self.withPlayer = withPlayer
        self.t0 = time.time()
        self.tInside = None
        self.tEnd = None
        self.result = None
        self.events = []
        self.counts = {}
        self.barriers = {}
        RunLog.RUNS.append(self)
        RunLog.RUNS[:] = RunLog.RUNS[-60:]

    def note(self, line):
        self.events.append('%s %s' % (time.strftime('%H:%M:%S'), line))
        self.events = self.events[-40:]
        notify.info('[TTBOTS-P8b] run %d %s: %s' % (self.id, self.kind, line))

    def count(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def barrier(self, name, waited):
        b = self.barriers.setdefault(name, [0, 0.0])
        b[0] += 1
        b[1] = max(b[1], round(waited, 1))

    def finish(self, result):
        if self.tEnd is not None:
            return
        self.tEnd = time.time()
        self.result = result
        bb.STATS.count('hq_%s_%s' % (self.kind, result))
        self.note('%s after %.0f s (inside %.0f s)' % (result, self.tEnd - self.t0,
                                                      self.tEnd - (self.tInside or self.t0)))

    def asDict(self):
        return {'id': self.id, 'kind': self.kind, 'where': self.where, 'members': self.members,
                'players': self.players, 'withPlayer': self.withPlayer,
                'start': time.strftime('%H:%M:%S', time.localtime(self.t0)),
                'secs': int((self.tEnd or time.time()) - self.t0),
                'insideSecs': int((self.tEnd or time.time()) - self.tInside) if self.tInside else None,
                'result': self.result, 'counts': self.counts, 'barriers': self.barriers,
                'events': self.events[-12:]}


def writeLog(director):
    runs = [r.asDict() for r in RunLog.RUNS]
    data = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'runs': runs,
            'counters': dict((k, v) for k, v in sorted(bb.STATS.c.items()) if k.startswith('hq_'))}
    path = os.path.join(director.runDir, 'bots-coghq.json')
    with open(path + '.tmp', 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(path + '.tmp', path)
    lines = ['TTBOTS P8b Cog HQ  %s' % data['time']]
    for r in runs[-25:]:
        lines.append('run %3d %-8s %-24s %-10s %5ss (inside %ss) bots %s players %s %s' % (
            r['id'], r['kind'], r['where'], r['result'] or 'running', r['secs'], r['insideSecs'],
            len(r['members']), r['players'], ' '.join('%s=%s' % kv for kv in sorted(r['counts'].items()))))
    lines.append('counters: ' + ' '.join('%s=%s' % kv for kv in sorted(data['counters'].items())))
    with open(os.path.join(director.runDir, 'bots-coghq.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')


def safe(fn):
    """Decorator: log a traceback through the hub instead of letting a task die."""
    def run(*args, **kw):
        try:
            return fn(*args, **kw)
        except Exception:
            bb.HUB.error('P8b %s' % fn.__name__, traceback.format_exc())
    run.__name__ = fn.__name__
    return run
