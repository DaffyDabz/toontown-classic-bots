"""Maze Game (DistributedMazeGame): 60 s in a maze full of treasures, with Cogs walking it. The
toon walks it (OrthoWalk, 8 ft/s, 8 directions, never through a wall) and claims a treasure when it
touches it: claimTreasure(n). A Cog that touches the toon knocks it flying (hitBySuit(me, ts), a
broadcast the AI never reads) to a spot every client picks with that toon's own RNG. The game ends
early when every treasure is gone.

Everything is rebuilt exactly as the client builds it, from the game's doId:
  the maze        MazeData (maze_<n>player), MazeBase's tile maths
  start spots     RandomNumGen(doId).shuffle(startPosHTable)
  toon RNGs       one RandomNumGen per player (where a knocked toon lands)
  the Cogs        their periods (the client's tables) shuffled, one RandomNumGen each, and
                  MazeSuit's walk (think every period tics of 256/s, one vertex per think)
So the bot walks real corridors (BFS over the walkable vertices), picks up the treasures it really
touches, sees the Cogs where the player sees them and dodges them, like a kid: not always."""
import math
import random
import functools
from collections import deque

from direct.distributed.ClockDelta import globalClockDelta
from direct.showbase.RandomNumGen import RandomNumGen
from panda3d.core import NodePath

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import MazeData, MazeGameGlobals as MGG
from toontown.minigame.MazeBase import MazeBase

SPEED = 8.0
TOON_R = 1.4
TREASURE_R = 0.7
SUIT_R = 2.0
FLY_DUR = 2 * 50 / 32.0
DIRS = ((0, 1), (0, -1), (-1, 0), (1, 0))       # MazeSuit DIR_UP, DOWN, LEFT, RIGHT
OPP = [1, 0, 3, 2]
_tables = {}


def _dictAfter(src, name):
    i = src.index('self.%s = {' % name) + len('self.%s = ' % name)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return eval(src[i:j + 1])
    raise ValueError(name)


def tables():
    """The client's Cog period tables (DistributedMazeGame.__init__), read from the client source."""
    if not _tables:
        import os
        import toontown.minigame as pkg
        src = open(os.path.join(os.path.dirname(pkg.__file__), 'DistributedMazeGame.py')).read()
        for n in ('slowerSuitPeriods', 'slowerSuitPeriodsCurve', 'fasterSuitPeriods', 'fasterSuitPeriodsCurve'):
            _tables[n] = _dictAfter(src, n)
    return _tables


class Suit:
    """MazeSuit without the model: the same RNG draws, tiles and timing."""

    def __init__(self, i, maze, parentRng, period, difficulty):
        self.i = i
        self.maze = maze
        self.rng = RandomNumGen(parentRng)
        self.difficulty = difficulty
        sp = MGG.SUIT_START_POSITIONS[i]
        self.startTile = (sp[0] * maze.width, sp[1] * maze.height)
        self.ticPeriod = int(period)
        self.walkDur = float(self.ticPeriod) / MGG.SUIT_TIC_FREQ

    def onstage(self):
        sTX, sTY = int(self.startTile[0]), int(self.startTile[1])
        c = lim = toggle = direction = 0
        while not self.maze.isWalkable(sTX, sTY):
            if direction == 0:
                sTX -= 1
            elif direction == 1:
                sTY -= 1
            elif direction == 2:
                sTX += 1
            elif direction == 3:
                sTY += 1
            c += 1
            if c > lim:
                c = 0
                direction = (direction + 1) % 4
                toggle += 1
                if not toggle & 1:
                    lim += 1
        self.TX, self.TY = sTX, sTY
        self.direction = 1
        self.nextTX, self.nextTY = self.TX, self.TY
        self.moveStart = -1.0

    def gameStart(self):
        self.occupiedTiles = [(self.nextTX, self.nextTY)]
        self.nextThinkTic = self.i * MGG.SUIT_TIC_FREQ // 20

    def apply(self, d, TX, TY):
        return TX + DIRS[d][0], TY + DIRS[d][1]

    def choose(self, unwalkables):
        m = self.maze
        if not self.rng.randrange(MGG.WALK_SAME_DIRECTION_PROB):
            if m.isWalkable(*self.apply(self.direction, self.TX, self.TY), unwalkables):
                return self.direction
        if self.difficulty >= 0.5:
            if not self.rng.randrange(MGG.WALK_TURN_AROUND_PROB):
                o = OPP[self.direction]
                if m.isWalkable(*self.apply(o, self.TX, self.TY), unwalkables):
                    return o
        cands = [0, 1, 2, 3]
        cands.remove(OPP[self.direction])
        while cands:
            d = self.rng.choice(cands)
            if m.isWalkable(*self.apply(d, self.TX, self.TY), unwalkables):
                return d
            cands.remove(d)
        return OPP[self.direction]

    def ticsDue(self, curTic):
        if curTic < self.nextThinkTic:
            return []
        return list(range(self.nextThinkTic, curTic + 1, self.ticPeriod))

    def prepareToThink(self):
        self.occupiedTiles = [(self.nextTX, self.nextTY)]

    def think(self, unwalkables):
        self.TX, self.TY = self.nextTX, self.nextTY
        self.direction = self.choose(unwalkables)
        self.nextTX, self.nextTY = self.apply(self.direction, self.TX, self.TY)
        self.occupiedTiles = [(self.TX, self.TY), (self.nextTX, self.nextTY)]
        self.moveStart = float(self.nextThinkTic) / MGG.SUIT_TIC_FREQ
        self.nextThinkTic += self.ticPeriod

    def pos(self, t):
        a = self.maze.tile2world(self.TX, self.TY)
        b = self.maze.tile2world(self.nextTX, self.nextTY)
        u = 0.0 if self.moveStart < 0 else max(0.0, min(1.0, (t - self.moveStart) / self.walkDur))
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u


def thinkSuits(suits, curT):
    """MazeSuit.thinkSuits, for game time curT."""
    curTic = int(curT * float(MGG.SUIT_TIC_FREQ))
    ups = []
    for i, s in enumerate(suits):
        ups.extend((tic, i) for tic in s.ticsDue(curTic))
    ups.sort(key=functools.cmp_to_key(lambda a, b: a[0] - b[0]))
    cur = 0
    for k, (tic, si) in enumerate(ups):
        if tic > cur:
            cur = tic
            j = k + 1
            while j < len(ups) and ups[j][0] <= tic:
                suits[ups[j][1]].prepareToThink()
                j += 1
        unw = []
        for o, s in enumerate(suits):
            if o != si:
                unw.extend(s.occupiedTiles)
        suits[si].think(unw)


class Brain(BaseBrain):
    name = 'maze'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        n = self.numPlayers
        name = MGG.getMazeName(self.doId, n, MazeData.mazeNames)
        data = MazeData.mazeData[name]
        self.maze = MazeBase(NodePath('botmaze'), data, MazeData.CELL_WIDTH, parent=NodePath('botmazeparent'))
        self.treasures = list(data['treasurePosList'])
        self.taken = set()
        rng = RandomNumGen(self.doId)
        table = [((0, 3), 0), ((0, -3), 180), ((3, 0), 270), ((-3, 0), 90)]
        rng.shuffle(table)
        self.toonRngs = [RandomNumGen(rng) for _ in range(n)]
        tb = tables()
        slower = tb['slowerSuitPeriodsCurve' if self.difficulty < 0.5 else 'slowerSuitPeriods']
        faster = tb['fasterSuitPeriodsCurve']
        numSuits = 4 * n
        periods = slower[self.hood][numSuits] + faster[self.hood][numSuits]
        rng.shuffle(periods)
        self.suits = [Suit(i, self.maze, rng, periods[i], self.difficulty) for i in range(numSuits)]
        for s in self.suits:
            s.onstage()
            s.gameStart()
        (x, y), h = ((0, 0), 180) if n == 1 else table[self.index]
        self.placeAt(x, y, 0.0, h)
        self.bot.setAnim('neutral')
        # the treasure of each vertex (they sit on vertices), for the path search
        self.atTile = {}
        for i, p in enumerate(self.treasures):
            self.atTile.setdefault(tuple(self.maze.world2tile(p[0] + 0.01, p[1] + 0.01)), []).append(i)
        self.path = []
        self.replanAt = 0.0
        self.flyUntil = 0.0
        self.flying = None
        self.pauseUntil = t0 + random.uniform(0.5, 1.5)
        self.caution = random.uniform(4.0, 7.0)       # ft: a Cog this close on the way -> back off
        self.dither = random.uniform(0.15, 0.3)       # chance a kid takes a different turning
        self.burst = (1.0, 2.5)                       # s of running before it stops to look
        self.look = (0.8, 2.6)                        # s it stands and looks
        self.burstEnd = t0 + random.uniform(*self.burst)
        self.note.update({'claimed': 0, 'hitBy': 0})

    # ---- broadcasts ------------------------------------------------------------------------------
    def onField(self, fieldName, args):
        if fieldName == 'setTreasureGrabbed':
            self.taken.add(args[1])
        elif fieldName == 'hitBySuit':
            # another player got knocked: every client replays that toon's RNG, so does the bot
            avId = args[0]
            if avId != self.bot.avId and avId in self.avIds:
                self.landing(self.avIds.index(avId))

    def landing(self, index):
        rng = self.toonRngs[index]
        m = self.maze
        while True:
            tile = [rng.randint(2, m.width - 1), rng.randint(2, m.height - 1)]
            if m.isWalkable(tile[0], tile[1]):
                break
        rng.randrange(1, 8)
        rng.choice([0, 1])
        rng.randrange(1, 3)
        rng.choice([0, 1])
        return m.tile2world(tile[0], tile[1])

    # ---- the walk ----------------------------------------------------------------------------------
    def myTile(self):
        p = self.bot.pos
        return tuple(self.maze.world2tile(p[0] + 0.01, p[1] + 0.01))

    def danger(self, gt):
        out = set()
        for s in self.suits:
            for tx, ty in ((s.TX, s.TY), (s.nextTX, s.nextTY)):
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        out.add((tx + dx, ty + dy))
        return out

    def plan(self, gt):
        """BFS from my vertex to the nearest vertex with a treasure left, around the Cogs."""
        m = self.maze
        start = self.myTile()
        bad = self.danger(gt)
        prev = {start: None}
        q = deque([start])
        found = None
        order = [0, 1, 2, 3]
        while q:
            cur = q.popleft()
            if cur != start and any(i not in self.taken for i in self.atTile.get(cur, ())):
                if random.random() > self.dither or found is not None:
                    found = cur
                    break
                found = cur
            random.shuffle(order)
            for d in order:
                nx, ny = cur[0] + DIRS[d][0], cur[1] + DIRS[d][1]
                if (nx, ny) in prev or not m.isWalkable(nx, ny) or (nx, ny) in bad:
                    continue
                prev[(nx, ny)] = cur
                q.append((nx, ny))
        if found is None:
            return []
        path = []
        cur = found
        while cur is not None and cur != start:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return [tuple(m.tile2world(*t)) for t in path]

    def tick(self, t):
        gt = self.gameTime()
        thinkSuits(self.suits, gt)
        if self.flying is not None:
            if t >= self.flyUntil:
                x, y = self.flying
                self.flying = None
                self.placeAt(x, y, 0.0, self.bot.h)      # OrthoWalk restarts: clearSmoothing + pos
                self.bot.setAnim('neutral')
                self.path = []
            return
        me = self.bot.pos
        # a Cog touches me: the client's collision, the knock-back flight
        for s in self.suits:
            sx, sy = s.pos(gt)
            if math.hypot(sx - me[0], sy - me[1]) < SUIT_R + TOON_R:
                self.send('hitBySuit', [self.bot.avId, globalClockDelta.getRealNetworkTime(bits=16)])
                self.flying = self.landing(self.index)
                self.flyUntil = t + FLY_DUR
                self.note['hitBy'] += 1
                self.bot.setAnim('neutral')
                return
        self.claimNear(me)
        if t < self.pauseUntil:
            self.bot.setAnim('neutral')
            return
        # a Cog close ahead: stop and replan around it
        if self.path:
            nx, ny = self.path[0]
            for s in self.suits:
                sx, sy = s.pos(gt)
                if math.hypot(sx - nx, sy - ny) < self.caution and math.hypot(sx - me[0], sy - me[1]) < self.caution + 2:
                    self.path = []
                    self.replanAt = 0.0
                    if random.random() < 0.3:
                        self.pauseUntil = t + random.uniform(0.3, 0.9)
                    break
        if t >= self.burstEnd:
            # a kid moves in spurts: runs a bit, stops to look at the maze and the Cogs
            self.burstEnd = t + random.uniform(*self.burst)
            self.pauseUntil = t + random.uniform(*self.look)
            self.bot.setAnim('neutral')
            return
        if not self.path and t >= self.replanAt:
            self.path = self.plan(gt)
            self.replanAt = t + 0.5
        if not self.path:
            self.bot.setAnim('neutral')
            return
        step = SPEED * random.uniform(0.85, 1.0) * 0.1
        while step > 0 and self.path:
            x, y = self.path[0]
            d = math.hypot(x - self.bot.pos[0], y - self.bot.pos[1])
            if d <= step:
                self.moveTo(x, y, 0.0, self.headingTo(x, y))
                self.path.pop(0)
                step -= d
                self.claimNear(self.bot.pos)
            else:
                self.walkToward(x, y, step / 0.1, 0.1)
                step = 0
        self.bot.setAnim('run')

    def headingTo(self, x, y):
        dx, dy = x - self.bot.pos[0], y - self.bot.pos[1]
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return self.bot.h
        return math.degrees(math.atan2(-dx, dy))

    def claimNear(self, me):
        tx, ty = self.maze.world2tile(me[0] + 0.01, me[1] + 0.01)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for i in self.atTile.get((tx + ddx, ty + ddy), ()):
                    if i in self.taken:
                        continue
                    p = self.treasures[i]
                    if math.hypot(p[0] - me[0], p[1] - me[1]) < TREASURE_R + TOON_R:
                        self.taken.add(i)
                        self.send('claimTreasure', [i])
                        self.note['claimed'] += 1
