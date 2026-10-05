"""Cog Thief (DistributedCogThiefGame): Cogs walk in to steal 4 barrels; the toons run about
(OrthoDrive 9 ft/s, turn instantly) throwing pies. The AI picks each Cog's goal and broadcasts
updateSuitGoal(ts, clientTs, cog, goalType, goalId, x, y, z); every client walks the Cogs itself
(straight at cogSpeed to a barrel or a return spot, chasing a toon). The AI waits on nobody (60 s).

Like a client the bot runs that same Cog walk, and:
  throwingPie(me, ts, heading, x, y, z)   when it throws (at most once a second; a kid every 1-2.5 s)
  pieHitSuit(me, ts, cog, cx, cy, cz)     only when its simulated pie (leaves 0.67 s after the throw,
                                          60 ft/s along its heading, ~49 ft of range) really passes
                                          within 1.75 ft of the Cog's simulated position
  hitBySuit(me, ts, cog, cx, cy, cz)      when a Cog walks into it (< 2.65 ft); then it is knocked
                                          back to its start spot (5.7 s, no moving or throwing)
  cogHitBarrel / cogAtReturnPos           every client reports these from its own Cog walk; one bot
                                          (the first bot in the list) does, so a bots-only game still
                                          loses barrels honestly (the AI takes the first report)
It aims with a kid's error (and leads the Cog poorly), so about half its pies hit."""
import math
import random

from direct.distributed.ClockDelta import globalClockDelta

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import CogThiefGameGlobals as CTG

SPEED = CTG.ToonSpeed
HALF_W, HALF_H = CTG.StageHalfWidth, CTG.StageHalfHeight
PIE_DELAY = 16.0 / 24.0
PIE_SPEED = 60.0
PIE_TIME = 0.81
HIT_R = 1.75
TOUCH = 2.65
KNOCK_TIME = 5.7


def ts32():
    return globalClockDelta.getRealNetworkTime(bits=32)


class Cog:
    __slots__ = ('i', 'x', 'y', 'goal', 'goalId', 'carry', 'start')

    def __init__(self, i):
        p = CTG.CogStartingPositions[i]
        self.i = i
        self.start = (p[0], p[1])
        self.x, self.y = p[0], p[1]
        self.goal = CTG.NoGoal
        self.goalId = -1
        self.carry = -1


class Brain(BaseBrain):
    name = 'cogthief'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        n = self.numPlayers
        self.cogSpeed = CTG.calculateCogSpeed(n, self.hood)
        self.cogs = [Cog(i) for i in range(CTG.calculateCogs(n, self.hood))]
        self.barrels = [[b[0], b[1], -1] for b in CTG.BarrelStartingPositions]   # x, y, carried-by
        self.stolen = set()
        bots = [a for a in self.avIds if self.isBot(a)]
        self.reporter = bots and bots[0] == self.bot.avId
        self.reported = set()          # (cog, 'hit', barrel) / (cog, 'ret') already reported this trip
        p = CTG.ToonStartingPositions[self.index]
        self.home = (p[0], p[1])
        self.placeAt(p[0], p[1], 0.0, 0.0)
        self.bot.setAnim('neutral')
        self.aimErr = random.uniform(5.0, 11.0)       # degrees
        self.lead = random.uniform(0.0, 0.6)          # how well it leads a walking Cog (1 = perfectly)
        self.rate = random.uniform(1.2, 2.5)
        self.nextThrow = t0 + random.uniform(1.5, 3.0)
        self.pies = []                                # [t0, x, y, heading]
        self.knockedUntil = 0.0
        self.target = None
        self.wander = None
        self.note.update({'throws': 0, 'hits': 0, 'hitBy': 0})

    # ---- the Cog walk (CogThief.think, straight lines; a chase heads at the toon) ----------------
    def goalPos(self, c):
        if c.goal == CTG.BarrelGoal and 0 <= c.goalId < len(self.barrels):
            b = self.barrels[c.goalId]
            return b[0], b[1]
        if c.goal == CTG.RunAwayGoal and 0 <= c.goalId < len(CTG.CogReturnPositions):
            p = CTG.CogReturnPositions[c.goalId]
            return p[0], p[1]
        if c.goal == CTG.ToonGoal:
            p = self.posOf(c.goalId)
            if p is not None:
                return p[0], p[1]
        return None

    def walkCogs(self, dt):
        for c in self.cogs:
            g = self.goalPos(c)
            if g is None:
                continue
            dx, dy = g[0] - c.x, g[1] - c.y
            d = math.hypot(dx, dy)
            step = self.cogSpeed * dt
            if d <= step:
                if c.goal != CTG.ToonGoal:
                    c.x, c.y = g
            else:
                c.x += dx / d * step
                c.y += dy / d * step
            if c.carry >= 0:
                self.barrels[c.carry][0], self.barrels[c.carry][1] = c.x, c.y
            if self.reporter:
                self.report(c)

    def report(self, c):
        """cogHitBarrel / cogAtReturnPos, as every client sends them from its own Cog walk."""
        if c.carry < 0 and c.goal != CTG.NoGoal:
            for bi, b in enumerate(self.barrels):
                if bi in self.stolen or b[2] >= 0:
                    continue
                if math.hypot(b[0] - c.x, b[1] - c.y) < 2.25 and (c.i, 'hit', bi) not in self.reported:
                    self.reported.add((c.i, 'hit', bi))
                    self.send('cogHitBarrel', [ts32(), c.i, bi, c.x, c.y, 0.0])
                    break
        if c.carry >= 0 and c.goal == CTG.RunAwayGoal and 0 <= c.goalId < len(CTG.CogReturnPositions):
            p = CTG.CogReturnPositions[c.goalId]
            if math.hypot(p[0] - c.x, p[1] - c.y) < 0.01 and (c.i, 'ret') not in self.reported:
                self.reported.add((c.i, 'ret'))
                self.send('cogAtReturnPos', [ts32(), c.i, c.carry])

    def knockBack(self, c):
        """The client's respondToPieHit / respondToToonHit: straight back to its start, no goal."""
        if c.carry >= 0:
            b = self.barrels[c.carry]
            b[0], b[1], b[2] = c.x, c.y, -1          # it drops the barrel where it was hit
            c.carry = -1
        c.x, c.y = c.start
        c.goal, c.goalId = CTG.NoGoal, -1

    # ---- the AI's broadcasts ---------------------------------------------------------------------
    def onField(self, fieldName, args):
        if fieldName == 'updateSuitGoal':
            _, _, i, goal, goalId, x, y, z = args
            if 0 <= i < len(self.cogs):
                c = self.cogs[i]
                c.x, c.y, c.goal, c.goalId = x, y, goal, goalId
                self.reported.discard((i, 'ret'))
                for bi in range(len(self.barrels)):
                    self.reported.discard((i, 'hit', bi))
        elif fieldName == 'makeCogCarryBarrel':
            _, _, i, bi, x, y, z = args
            if 0 <= i < len(self.cogs) and 0 <= bi < len(self.barrels):
                self.cogs[i].carry = bi
                self.barrels[bi][2] = i
        elif fieldName == 'makeCogDropBarrel':
            _, _, i, bi, x, y, z = args
            if 0 <= bi < len(self.barrels):
                self.barrels[bi][0], self.barrels[bi][1], self.barrels[bi][2] = x, y, -1
            if 0 <= i < len(self.cogs) and self.cogs[i].carry == bi:
                self.cogs[i].carry = -1
        elif fieldName == 'markBarrelStolen':
            self.stolen.add(args[2])

    # ---- the kid ---------------------------------------------------------------------------------
    def pickTarget(self):
        me = self.bot.pos
        best, bestScore = None, 1e9
        for c in self.cogs:
            if c.goal == CTG.NoGoal or (c.x, c.y) == c.start:
                continue
            d = math.hypot(c.x - me[0], c.y - me[1])
            # a Cog with a barrel (or about to grab one) first, then the one coming for me
            urgency = 0.0
            if c.carry >= 0:
                urgency = -25.0
            elif c.goal == CTG.BarrelGoal:
                urgency = -10.0
            elif c.goal == CTG.ToonGoal and c.goalId == self.bot.avId:
                urgency = -12.0
            s = d + urgency + random.uniform(0, 6)
            if s < bestScore:
                best, bestScore = c, s
        return best

    def tick(self, t):
        dt = 0.1
        self.walkCogs(dt)
        self.flyPies(t)
        if t < self.knockedUntil:
            return
        if getattr(self, 'knocked', False):
            self.knocked = False
            self.placeAt(self.home[0], self.home[1], 0.0, 0.0)
            self.bot.setAnim('neutral')
            return
        me = self.bot.pos
        # a Cog walked into me: the client's own collision, then the knock-back
        for c in self.cogs:
            if c.goal != CTG.NoGoal and (c.x, c.y) != c.start and math.hypot(c.x - me[0], c.y - me[1]) < TOUCH:
                self.send('hitBySuit', [self.bot.avId, ts32(), c.i, c.x, c.y, 0.0])
                self.knockBack(c)
                self.note['hitBy'] += 1
                self.knocked = True
                self.knockedUntil = t + KNOCK_TIME
                self.bot.setAnim('neutral')
                return
        if self.target is None or self.target.goal == CTG.NoGoal or random.random() < 0.02:
            self.target = self.pickTarget()
        c = self.target
        if c is None:
            # nothing to throw at: mill about near the barrels
            if self.wander is None or math.hypot(self.wander[0] - me[0], self.wander[1] - me[1]) < 1.0:
                self.wander = (random.uniform(-12, 12), random.uniform(-10, 10))
            self.step(self.wander[0], self.wander[1], dt)
            return
        d = math.hypot(c.x - me[0], c.y - me[1])
        want = random.uniform(10.0, 22.0) if not hasattr(self, 'want') else self.want
        self.want = want
        if d > want + 4.0:
            self.step(c.x, c.y, dt)
        elif d < 5.0 and c.goal == CTG.ToonGoal and c.goalId == self.bot.avId:
            # too close: back off (kids run away from the Cog chasing them)
            self.step(me[0] - (c.x - me[0]), me[1] - (c.y - me[1]), dt)
        else:
            self.bot.setAnim('neutral')
            self.face(c.x, c.y)
        if t >= self.nextThrow and d < 40.0:
            self.throw(t, c)

    def step(self, x, y, dt):
        px, py = self.bot.pos[0], self.bot.pos[1]
        dx, dy = x - px, y - py
        d = math.hypot(dx, dy)
        if d < 0.3:
            return
        s = min(d, SPEED * dt)
        nx = max(-HALF_W, min(HALF_W, px + dx / d * s))
        ny = max(-HALF_H, min(HALF_H, py + dy / d * s))
        self.moveTo(nx, ny, 0.0, math.degrees(math.atan2(-dx, dy)))
        self.bot.setAnim('run')

    def face(self, x, y):
        me = self.bot.pos
        h = math.degrees(math.atan2(-(x - me[0]), y - me[1]))
        if abs(((h - self.bot.h) + 180) % 360 - 180) > 3:
            self.moveTo(me[0], me[1], 0.0, h)

    def throw(self, t, c):
        me = self.bot.pos
        # where the kid thinks the Cog will be when the pie gets there (leads it only partly)
        g = self.goalPos(c)
        tx, ty = c.x, c.y
        if g is not None:
            dx, dy = g[0] - c.x, g[1] - c.y
            dg = math.hypot(dx, dy)
            if dg > 0.01:
                flight = PIE_DELAY + math.hypot(c.x - me[0], c.y - me[1]) / PIE_SPEED
                ahead = min(dg, self.cogSpeed * flight * self.lead)
                tx, ty = c.x + dx / dg * ahead, c.y + dy / dg * ahead
        h = math.degrees(math.atan2(-(tx - me[0]), ty - me[1])) + random.gauss(0.0, self.aimErr)
        self.moveTo(me[0], me[1], 0.0, h)
        self.send('throwingPie', [self.bot.avId, ts32(), h, me[0], me[1], 0.0])
        self.bot.setAnim('neutral')
        self.pies.append([t + PIE_DELAY, me[0], me[1], h])
        self.nextThrow = t + max(1.05, self.rate * random.uniform(0.7, 1.3))
        self.note['throws'] += 1

    def flyPies(self, t):
        """The pie's flight, as the thrower's client runs it: a hit is only claimed when it hits."""
        keep = []
        for p in self.pies:
            t0, x0, y0, h = p
            if t < t0:
                keep.append(p)
                continue
            s = t - t0
            if s > PIE_TIME:
                continue
            fx, fy = -math.sin(math.radians(h)), math.cos(math.radians(h))
            a = max(0.0, s - 0.1) * PIE_SPEED + 0.97
            b = s * PIE_SPEED + 0.97
            hit = None
            for c in self.cogs:
                if (c.x, c.y) == c.start:
                    continue
                # distance from the Cog to the pie's path this tick
                rx, ry = c.x - x0, c.y - y0
                along = rx * fx + ry * fy
                if a - HIT_R <= along <= b + HIT_R and abs(rx * fy - ry * fx) < HIT_R:
                    hit = c
                    break
            if hit is not None:
                self.send('pieHitSuit', [self.bot.avId, ts32(), hit.i, hit.x, hit.y, 0.0])
                self.knockBack(hit)
                self.note['hits'] += 1
                continue
            keep.append(p)
        self.pies = keep
