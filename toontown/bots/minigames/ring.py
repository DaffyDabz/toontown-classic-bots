"""Ring Game (DistributedRingGame): the toons swim forward (the rings come at them at 22.5 ft/s);
each toon steers in the x/z plane (10 ft/s per axis, +-10) through ITS ring of each of the 16 ring
groups. Group i reaches the toons at game time 6 + 3 i; the client then tests its toon against its
ring ((dx^2 + dz^2) <= (3.0 * 1.025)^2) and sends setToonGotRing(1/0), exactly once per group, in
order. The AI has NO timer: it waits for 16 reports from every toon, so the bot always sends all 16.

The rings are rebuilt exactly as the client builds them: RandomNumGen(doId) picks the hood's pattern
and each group's track (RingTrackGroups), the ring of player k follows track k (Ring/RingGroup math).
The bot steers toward where its ring will be when the group arrives, like a kid, with a visible sway
and a reaction lag, and it sometimes gives up on a hard one: ~80 % of the rings go through."""
import math
import random
import re

from direct.showbase.RandomNumGen import RandomNumGen

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import RingGameGlobals, RingTrackGroups
from toontown.toonbase import ToontownGlobals

NUM_GROUPS = 16
FIRST = 6.0                 # T_FIRST_RING_GROUP_ARRIVES
SPACING_T = 3.0             # RING_GROUP_SPACING / TOON_SWIM_VEL
SPEED = 10.0                # TOONXZ_SPEED
MAXXZ = RingGameGlobals.MAX_TOONXZ
HIT2 = (RingGameGlobals.RING_RADIUS * 1.025) ** 2
_patterns = {}


def patterns():
    """The client's difficultyPatterns table (a local of DistributedRingGame.__generateRings), read
    from the client source so the bot uses exactly the same table."""
    if not _patterns:
        import toontown.minigame as pkg
        import os
        src = open(os.path.join(os.path.dirname(pkg.__file__), 'DistributedRingGame.py')).read()
        m = re.search(r'difficultyPatterns = (\{.*?)\n\s*safezone = self.getSafezoneId', src, re.S)
        _patterns.update(eval(m.group(1), {'ToontownGlobals': ToontownGlobals}))
    return _patterns


class Group:
    def __init__(self, trackGroup, k):
        self.period = trackGroup.period
        self.reverse = trackGroup.reverseFlag
        self.tOffset = trackGroup.tOffset
        self.track = trackGroup.tracks[k]
        self.trackT = trackGroup.trackTOffsets[k]

    def pos(self, t):
        n = (t / self.period + self.tOffset) % 1.0
        if self.reverse:
            n = 1.0 - n
        p = self.track.eval((n + self.trackT) % 1.0)
        return p[0] * MAXXZ, p[1] * MAXXZ


def rebuild(doId, hood, numPlayers, index):
    rng = RandomNumGen(doId)
    pattern = rng.choice(patterns()[hood])
    out = []
    for i in range(NUM_GROUPS):
        tg = RingTrackGroups.getRandomRingTrackGroup(pattern[i], numPlayers, rng)
        out.append(Group(tg, index))
    return out


class Brain(BaseBrain):
    name = 'ring'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        n = self.numPlayers
        self.groups = rebuild(self.doId, self.hood, n, self.index)
        self.skill = random.uniform(0.56, 0.76)          # how often this kid lines up a ring
        self.lag = random.uniform(0.25, 0.6)             # reaction time (s)
        self.sway = random.uniform(0.4, 1.2)             # wobble amplitude (ft)
        self.phase = random.uniform(0, 6.28)
        self.next = 0
        self.plan = {}                                   # group -> go for it?
        self.hits = 0
        self.x, self.z = 4.0 * self.index - 4.0 * (n - 1) / 2.0, 0.0
        self.placeAt(self.x, 0.0, self.z, 0.0)
        self.bot.setAnim('swim')
        for i in range(NUM_GROUPS):
            self.at(FIRST + SPACING_T * i - self.gameTime() + random.uniform(0.02, 0.12), self.cross, i)
        self.note['rings'] = 0

    def goal(self, t):
        i = self.next
        if i >= NUM_GROUPS:
            return self.x, self.z
        if i not in self.plan:
            self.plan[i] = random.random() < self.skill
        tc = FIRST + SPACING_T * i
        if tc - t > 2.6:
            # the group is still far: drift back toward the middle, as kids do
            return self.x * 0.9, self.z * 0.9
        gx, gz = self.groups[i].pos(max(t, tc - self.lag))
        if not self.plan[i]:
            # did not read this one: aims where the ring was a moment ago, off by a bit
            gx, gz = self.groups[i].pos(t - 1.2)
            gx += random.choice((-1, 1)) * 4.0
        return gx, gz

    def tick(self, t):
        gt = self.gameTime()
        if self.next >= NUM_GROUPS:
            return
        gx, gz = self.goal(gt)
        self.phase += 0.1 * 2.3
        gx += math.sin(self.phase) * self.sway
        gz += math.cos(self.phase * 0.7) * self.sway * 0.6
        dt = 0.1
        # the arrow keys: each axis moves at most SPEED (diagonals are not normalised, as the client)
        dx = max(-SPEED * dt, min(SPEED * dt, gx - self.x))
        dz = max(-SPEED * dt, min(SPEED * dt, gz - self.z))
        self.x = max(-MAXXZ, min(MAXXZ, self.x + dx))
        self.z = max(-MAXXZ, min(MAXXZ, self.z + dz))
        self.moveTo(self.x, 0.0, self.z, 0.0)

    def cross(self, i):
        """Group i reaches the toon: the client's own test, one report per group, in order."""
        if i != self.next:
            return
        rx, rz = self.groups[i].pos(self.gameTime())
        got = int((self.x - rx) ** 2 + (self.z - rz) ** 2 <= HIT2)
        self.send('setToonGotRing', [got])
        self.hits += got
        self.next = i + 1
        self.note['rings'] = self.hits
