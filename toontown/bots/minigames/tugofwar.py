"""Tug-of-War (DistributedTugOfWarGame): sendGameType (toons vs toons / vs a Cog) arrives before
setGameReady; the client sends sendNewAvIdList(original order) in setGameReady and
reportPlayerReady(side) on setGameStart. sendGoSignal -> 3 s (Ready, Go) -> the 'tug' state:
every 0.2 s reportCurrentKeyRate(keyRate, force), force by the client's own computeForce with the
ideal-rate schedule (the last segment is all-out). The AI moves the rope on every numPlayers-th
report (sendCurrentPosition / sendSuitPosition); a client that sees a toon or the Cog reach the
water (|x| < 2) reports reportEndOfContest(losingSide), as every client does.

A kid mashes the two arrows: near the ideal rate, a key or two off now and then, all-out at the end."""
import math
import random

from direct.showbase.RandomNumGen import RandomNumGen

from toontown.bots.minigames.base import Brain as BaseBrain

TOON_VS_TOON, TOON_VS_COG = 0, 1
RATES = [[8, 6], [5, 7], [6, 8], [6, 10], [7, 11], [8, 12]]
SEND = 0.2
HANDYCAP = 2.0


def docks():
    return [(-9.0 + 1.5 * k) for k in range(5)] + [(3 + 1.5 * k) for k in range(5)]


class Brain(BaseBrain):
    name = 'tugofwar'

    def __init__(self, game):
        BaseBrain.__init__(self, game)
        self.gameType = TOON_VS_COG
        self.suitType = 1
        self.tugging = False
        self.ended = False
        self.award = 0.0
        self.skill = random.uniform(-1.2, 0.8)       # how close to the beat this kid keeps

    def onField(self, fieldName, args):
        if fieldName == 'sendGameType':
            self.gameType, self.suitType = args[0], args[1]
        elif fieldName == 'setGameReady':
            pass
        elif fieldName == 'sendGoSignal':
            self.at(3.0, self.startTug)
        elif fieldName == 'sendCurrentPosition':
            for avId, off in zip(args[0], args[1]):
                self.offsets[avId] = off
            self.checkFallen()
        elif fieldName == 'sendSuitPosition':
            self.suitOffset = args[0]
            self.checkFallen()
        elif fieldName == 'sendStopSignal':
            self.tugging = False

    def onGameReady(self):
        # calculatePositions: the sides every client computes (RandomNumGen(doId) shuffle)
        ids = list(self.avIds)
        n = len(ids)
        d = docks()
        self.pos = {}
        self.offsets = dict((a, 0.0) for a in ids)
        self.suitOffset = 0.0
        self.suitX = d[6] if n == 4 else d[7]
        if self.gameType == TOON_VS_COG:
            slots = {1: [2], 2: [1, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3]}[n]
        else:
            RandomNumGen(self.doId).shuffle(ids)
            slots = {2: [2, 7], 3: [1, 2, 7], 4: [1, 2, 7, 8]}.get(n, [2])
        for a, s in zip(ids, slots):
            self.pos[a] = d[s]
        self.side = 0 if self.pos.get(self.bot.avId, -1) < 0 else 1
        self.advantage = 1.0
        if n == 3 and self.gameType == TOON_VS_TOON:
            mine = [a for a in ids if (self.pos[a] < 0) == (self.side == 0)]
            if len(mine) == 1:
                self.advantage = 2.0
        self.send('sendNewAvIdList', [list(self.avIds)])

    def onGameStart(self, t0):
        self.at(random.uniform(0.1, 0.4), self.send, 'reportPlayerReady', [self.side])

    def startTug(self):
        self.tugging = True
        self.segment = 0
        self.segmentEnd = globalClock.getRealTime() + RATES[0][0]
        self.every(SEND, self.report)

    def report(self):
        if not self.tugging:
            return
        now = globalClock.getRealTime()
        while now >= self.segmentEnd and self.segment < len(RATES) - 1:
            self.segment += 1
            self.segmentEnd += RATES[self.segment][0]
        ideal = RATES[self.segment][1]
        allOut = self.segment == len(RATES) - 1
        if allOut:
            k = max(0, int(round(random.gauss(10.5 + self.skill, 1.8))))
            force = 0.75 * k
        else:
            k = max(0, int(round(random.gauss(ideal + self.skill * 0.6, 1.1))))
            self.award = self.award + 0.3 if k in (ideal, ideal + 1) else 0.0
            sd = 0.25 * ideal
            force = self.advantage * (self.award + 4 + 0.4 * ideal) * math.exp(-(k - ideal) ** 2 / (2.0 * sd * sd))
        self.send('reportCurrentKeyRate', [k, force])

    def checkFallen(self):
        if self.ended or not self.tugging:
            return
        for a, dock in self.pos.items():
            x = dock + self.offsets.get(a, 0.0) / HANDYCAP
            if -2 < x < 0 or 0 < x < 2:
                self.ended = True
                losing = 0 if dock < 0 else 1
                self.send('reportEndOfContest', [losing])
                self.note['end'] = 'side %d fell' % losing
                return
        if self.gameType == TOON_VS_COG:
            x = self.suitX + self.suitOffset
            if -2 < x < 0 or 0 < x < 2:
                self.ended = True
                self.send('reportEndOfContest', [1])
                self.note['end'] = 'cog fell'

    def onGameExit(self):
        self.tugging = False
