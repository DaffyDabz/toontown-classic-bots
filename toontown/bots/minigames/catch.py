"""Catching Game (DistributedCatchGame): fruit and anvils fall on a grid; the toon runs under
fruit (OrthoWalk, position broadcast every 0.2 s) and the client sends claimCatch(objNum, typeId)
when the fruit touches it. After the LAST drop's interval ends the client sends reportDone - the
AI ABORTS the game if any toon never does, so the bot always does.

The drop schedule is rebuilt exactly as the client builds it (RandomNumGen(doId), the client's
difficulty constants, RegionDropPlacer), so the bot runs to where the fruit really lands and
catches it when it lands, the way the kid sees it. A kid goes for most fruit it can reach, dawdles,
and misses some."""
import math
import random

from direct.showbase.RandomNumGen import RandomNumGen

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import CatchGameGlobals
from toontown.minigame.DropPlacer import RegionDropPlacer
from toontown.minigame.DropScheduler import DropScheduler

FRUITS = {1000: 'orange', 2000: 'apple', 3000: 'watermelon', 4000: 'coconut', 5000: 'pear', 9000: 'pineapple'}


def lerp(a, b, t):
    return a + (b - a) * t


class _Game:
    """The attributes of DistributedCatchGame the drop placer reads (defineConstants +
    calcDifficultyConstants, copied from the client)."""
    class notify:
        @staticmethod
        def debug(*a):
            pass

    def __init__(self, doId, difficulty, numPlayers):
        self.randomNumGen = RandomNumGen(doId)
        self.numPlayers = numPlayers
        self.FirstDropDelay = 0.5
        self.FasterDropDelay = int(2.0 / 3 * CatchGameGlobals.GameDuration)
        self.FasterDropPeriodMult = 0.5
        self.ToonSpeed = lerp(16.0, 25.0, difficulty)
        areaScale = [1.0, 1.0, 3.0 / 2, 4.0 / 2][numPlayers - 1]
        linear = math.sqrt(areaScale)
        w, h = math.sqrt(areaScale * 20 * 20), math.sqrt(areaScale * 15 * 15)
        self.StageHalfWidth, self.StageHalfHeight = w / 2.0, h / 2.0
        distance = math.sqrt(w * w + h * h) / linear
        run = distance / self.ToonSpeed
        fraction = 1.0 / 3 * 0.85
        self.BaselineOnscreenDropDuration = run / (fraction * 2.0)
        self.OffscreenTime = self.BaselineOnscreenDropDuration
        self.BaselineDropDuration = self.BaselineOnscreenDropDuration + self.OffscreenTime
        self.MaxDropDuration = self.BaselineDropDuration
        self.doId = doId
        self.DropPeriod = self.MaxDropDuration / 2.0 / ((numPlayers - 1.0) * 0.75 + 1.0)
        sch = DropScheduler(CatchGameGlobals.GameDuration, self.FirstDropDelay, self.DropPeriod,
                            self.MaxDropDuration, self.FasterDropDelay, self.FasterDropPeriodMult)
        total = 0
        while not sch.doneDropping():
            sch.stepT()
            total += 1
        self.numFruits = int(total * 0.75)
        self.numAnvils = int(total - self.numFruits)
        self.DropRows, self.DropColumns = [[5, 5], [5, 5], [6, 6], [7, 7]][numPlayers - 1]

    def getNumPlayers(self):
        return self.numPlayers

    def grid2world(self, column, row):
        x = column / float(self.DropColumns - 1) * 2.0 - 1.0
        y = row / float(self.DropRows - 1) * 2.0 - 1.0
        return x * self.StageHalfWidth, y * self.StageHalfHeight


def schedule(doId, hood, difficulty, numPlayers):
    """[(landTime, objNum, name, x, y)] in game time, exactly the client's drops."""
    g = _Game(doId, difficulty, numPlayers)
    fruit = FRUITS.get(hood, 'apple')
    names = [fruit] * g.numFruits + ['anvil'] * g.numAnvils
    g.randomNumGen.shuffle(names)
    placer = RegionDropPlacer(g, numPlayers, names)
    out = []
    n = 0
    while not placer.doneDropping():
        t, name, coords = placer.getNextDrop()
        x, y = g.grid2world(*coords)
        dur = CatchGameGlobals.Name2DropObjectType[name].onscreenDurMult * g.BaselineOnscreenDropDuration
        out.append((t + g.OffscreenTime + dur, n, name, x, y))
        n += 1
    return g, out


class Brain(BaseBrain):
    name = 'catch'

    def onGameStart(self, t0):
        n = self.numPlayers
        self.g, self.drops = schedule(self.doId, self.hood, self.difficulty, n)
        self.speed = self.g.ToonSpeed * random.uniform(0.7, 0.9)
        self.want = random.uniform(0.55, 0.85)          # how many fruit this kid goes for
        self.caught = set()                            # objNums anyone caught (setObjectCaught)
        self.target = None
        x = (self.index - (n - 1) / 2.0) * 4.0
        self.placeAt(x, 0.0, 0.0, 180.0)
        self.bot.setAnim('neutral')
        self.claims = self.game.record.setdefault('catchTargets', {})
        last = max(d[0] for d in self.drops) if self.drops else 50.0
        self.at(max(1.0, last + 0.6 - self.gameTime()), self.done_)
        self.note['caught'] = 0

    def done_(self):
        self.send('reportDone', [])

    def onField(self, fieldName, args):
        if fieldName == 'setObjectCaught':
            self.caught.add(args[1])

    def pick(self, t):
        best = None
        for land, num, name, x, y in self.drops:
            if name == 'anvil' or num in self.caught or land < t + 0.3:
                continue
            if land > t + 6.0:
                break
            holder = self.claims.get(num)
            if holder not in (None, self.bot.avId):
                continue
            d = math.hypot(x - self.bot.pos[0], y - self.bot.pos[1])
            if d / self.speed + 0.4 > land - t:
                continue
            if random.random() > self.want:
                self.caught.add(num)                   # not this one (a kid lets some go)
                continue
            best = (land, num, name, x, y)
            break
        return best

    def tick(self, t):
        gt = self.gameTime()
        if self.target is None or self.target[1] in self.caught:
            self.target = self.pick(gt)
            if self.target is not None:
                self.claims[self.target[1]] = self.bot.avId
        if self.target is None:
            self.bot.setAnim('neutral')
            return
        land, num, name, x, y = self.target
        there = self.walkToward(x, y, self.speed, 0.1)
        self.bot.setAnim('neutral' if there else 'run')
        if gt >= land:
            if there or math.hypot(x - self.bot.pos[0], y - self.bot.pos[1]) < 1.6:
                self.send('claimCatch', [num, CatchGameGlobals.Name2DOTypeId[name]])
                self.note['caught'] += 1
            self.caught.add(num)
            self.target = None

    def onGameExit(self):
        self.bot.setAnim('neutral')
