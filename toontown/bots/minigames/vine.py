"""Jungle Vines (DistributedVineGame): swing on vine i, jump (setJumpingFromVine: where from and
the launch velocity; every other client flies the toon with the same physics), catch vine i+1
(setNewVine(me, i+1, t, facingRight): the only message the AI tracks), grab the bananas on the
way (claimTreasure(n), banana n hangs between vines n and n+1). A missed catch is a fall
(setFallingFromMidair) and the toon comes back on the vine it jumped from (setNewVine(i, 0.1)).
The AI runs a 70 s timer and ends early when everybody hangs on the last vine (19).

Vine i hangs from (30 i, 30) with the length of its course section (setVineSections); t is the
fraction down the vine. A kid jumps every 2-3.5 s, falls now and then, finishes in ~50-65 s."""
import random

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import VineGameGlobals as VG

G = 32.0


class Brain(BaseBrain):
    name = 'vine'

    def onGameStart(self, t0):
        secs = list(self.fieldOf('setVineSections', ([0, 0, 0, 0],))[0])
        self.lengths = []
        for i in range(VG.NumVines):
            sec = VG.CourseSections[secs[min(i // 5, len(secs) - 1)]]
            self.lengths.append(sec[i % 5][0])
        self.vine = 0
        self.grabbed = set()
        self.falls = 0
        self.fallRate = random.uniform(0.06, 0.16)
        self.bananaRate = random.uniform(0.3, 0.6)
        self.pace = random.uniform(2.2, 3.4)
        self.send('setNewVine', [self.bot.avId, 0, VG.VineStartingT, 1])
        self.note['treasures'] = 0
        self.at(random.uniform(1.5, 3.0), self.jump)

    def onField(self, fieldName, args):
        if fieldName == 'setTreasureGrabbed':
            self.grabbed.add(args[1])

    def attach(self, i, t):
        return VG.VineXIncrement * i, VG.VineHeight - t * self.lengths[i]

    def jump(self):
        if self.vine >= VG.NumVines - 1 or self.gameTime() > VG.GameDuration - 1.0:
            return
        i = self.vine
        t0 = random.uniform(0.45, 0.8)
        x0, z0 = self.attach(i, t0)
        x0 += random.uniform(2.0, 5.0)              # swinging forward when it lets go
        t1 = random.uniform(0.35, 0.65)
        x1, z1 = self.attach(i + 1, t1)
        T = random.uniform(0.75, 1.05)
        vx = (x1 - x0) / T
        vz = (z1 - z0 + 0.5 * G * T * T) / T
        self.send('setJumpingFromVine', [self.bot.avId, i, 1, x0, z0, vx, int(round(vz))])
        n = i                                        # the banana between vine i and i+1
        if n < VG.NumVines - 1 and n not in self.grabbed and random.random() < self.bananaRate:
            self.at(T * 0.5, self.banana, n)
        if random.random() < self.fallRate and i > 0:
            self.at(T * 0.9, self.miss, i, x0 + vx * T * 0.9, z0 + vz * T * 0.9 - 0.5 * G * (T * 0.9) ** 2)
        else:
            self.at(T, self.land, i + 1, t1)

    def banana(self, n):
        if n not in self.grabbed:
            self.send('claimTreasure', [n])
            self.grabbed.add(n)
            self.note['treasures'] += 1

    def land(self, i, t):
        self.vine = i
        self.send('setNewVine', [self.bot.avId, i, t, 1])
        if i < VG.NumVines - 1:
            self.at(random.uniform(self.pace * 0.7, self.pace * 1.3), self.jump)
        else:
            self.note['end'] = round(self.gameTime(), 1)

    def miss(self, i, x, z):
        self.falls += 1
        self.note['falls'] = self.falls
        self.send('setFallingFromMidair', [self.bot.avId, 1, x, z, 0.0, -1, 1])
        self.at(random.uniform(1.2, 1.8), self.backOn, i)

    def backOn(self, i):
        self.vine = i
        self.send('setNewVine', [self.bot.avId, i, VG.VineFellDownT, 1])
        self.at(random.uniform(1.5, 3.0), self.jump)
