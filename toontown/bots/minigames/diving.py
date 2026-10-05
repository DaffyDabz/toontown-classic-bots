"""Treasure Dive (DistributedDivingGame): swim down (x/z plane, y = -2), touch a chest ->
pickupTreasure(chestId), swim up to the boat -> treasureRecovered(). The toon's own smooth node
broadcasts the full pos + hpr (it swims on its back, H 180 P 180, steering with R). The AI runs a
fixed 65 s timer and waits on nobody. The bot does not send the fish/crab collisions (a client only
does when it is really hit; they are optional to the AI).

Chests: chest i starts at (-15 + 10 i, -36); after a recovery the AI's incrementScore(avId, newSpot)
moves the chest that toon held to (newSpot - 15, -36); a dropped chest (setTreasureDropped) sinks
straight down from where its holder was. A kid swims at about the client's speed with detours,
pauses at the chest, and brings up 2-4."""
import math
import random

from panda3d.core import Quat, Vec3

from toontown.bots.minigames.base import Brain as BaseBrain

SWIM = 8.0              # ft/s forward (client: 8 x the up vector)
BOAT = (0.0, 34.0)      # x, z just under the boat's sphere (0, 3, 38.85) r ~8.4
Y = -2.0


def _rollTable():
    out = []
    for r in range(0, 360, 3):
        q = Quat()
        q.setHpr(Vec3(180, 180, r))
        up = q.getUp()
        out.append((r, up[0], up[2]))
    return out


ROLLS = _rollTable()


def rollFor(dx, dz):
    d = math.hypot(dx, dz) or 1.0
    ux, uz = dx / d, dz / d
    return max(ROLLS, key=lambda e: e[1] * ux + e[2] * uz)[0]


class Brain(BaseBrain):
    name = 'diving'

    def onGameStart(self, t0):
        self.chests = {}          # chestId -> [x, z, holder]
        n = self.numPlayers
        for i in range(n):
            self.chests[i] = [-15.0 + 10.0 * i, -36.0, 0]
        self.speed = SWIM * random.uniform(0.42, 0.62)
        self.state = 'dive'
        self.goal = None
        self.carrying = None
        self.pauseUntil = 0.0
        self.wobble = random.uniform(0, 6.28)
        self.placeAt(-10 + self.index * 5, Y, 36.0, 180.0, 180.0, 0.0)
        self.note['recovered'] = 0
        self.at(random.uniform(0.5, 2.0), self.go)

    def go(self):
        self.state = 'seek'

    def onField(self, fieldName, args):
        if fieldName == 'setTreasureGrabbed':
            avId, chestId = args
            if chestId in self.chests:
                self.chests[chestId][2] = avId
        elif fieldName == 'incrementScore':
            avId, newSpot = args[0], args[1]
            for c in self.chests.values():
                if c[2] == avId:
                    c[0], c[1], c[2] = newSpot - 15.0, -36.0, 0
        elif fieldName == 'setTreasureDropped':
            avId = args[0]
            v = self.game.act.mgView
            o = v.objects.get(avId) if v is not None else None
            for cid, c in self.chests.items():
                if c[2] == avId:
                    if o is not None:
                        c[0] = max(-20.0, min(20.0, o.pos[0]))
                    elif avId == self.bot.avId:
                        c[0] = self.bot.pos[0]
                    c[1], c[2] = -36.0, 0
                    if avId == self.bot.avId:
                        self.carrying = None
                        self.state = 'seek'

    def pickChest(self):
        mine = self.game.record.setdefault('divingTargets', {})
        free = [(cid, c) for cid, c in self.chests.items() if c[2] == 0 and mine.get(cid) in (None, self.bot.avId)]
        if not free:
            free = [(cid, c) for cid, c in self.chests.items() if c[2] == 0]
        if not free:
            return None
        x = self.bot.pos[0]
        cid, c = min(free, key=lambda e: abs(e[1][0] - x) + random.uniform(0, 8))
        mine[cid] = self.bot.avId
        return cid

    def swimToward(self, x, z, dt, down):
        px, pz = self.bot.pos[0], self.bot.pos[2]
        dx, dz = x - px, z - pz
        d = math.hypot(dx, dz)
        self.wobble += dt * 1.3
        sp = self.speed * (0.85 + 0.15 * math.sin(self.wobble))
        s = min(d, sp * dt)
        if d > 0.01:
            nx, nz = px + dx / d * s, pz + dz / d * s
            nx += math.sin(self.wobble * 0.7) * 0.05
            r = rollFor(dx, dz)
            self.moveTo(max(-20.0, min(20.0, nx)), Y, max(-38.0, min(36.0, nz)), 180.0, 180.0, r)
        return d - s < 1.2

    def tick(self, t):
        if t < self.pauseUntil or self.state == 'dive':
            return
        if self.gameTime() > 63.0:
            return
        if self.state == 'seek':
            if self.goal is None or self.chests[self.goal][2] != 0:
                self.goal = self.pickChest()
                if self.goal is None:
                    return
            c = self.chests[self.goal]
            if self.swimToward(c[0], -35.2, 0.1, True):
                self.pauseUntil = t + random.uniform(0.4, 1.0)
                self.send('pickupTreasure', [self.goal])
                c[2] = self.bot.avId
                self.carrying = self.goal
                self.goal = None
                self.state = 'up'
                self.boatX = random.uniform(-4.5, 4.5)
        elif self.state == 'up':
            if self.carrying is None:
                self.state = 'seek'
                return
            if self.swimToward(self.boatX, BOAT[1], 0.1, False):
                self.send('treasureRecovered', [])
                self.note['recovered'] += 1
                self.carrying = None
                self.state = 'rest'
                self.pauseUntil = t + random.uniform(3.0, 7.0)
        elif self.state == 'rest':
            self.state = 'seek'
