"""Toon Blitz (DistributedTwoDGame): a side-scroller. The level is 5 sections the AI picked
(setSectionsSelected: section type, the enemies, treasures (with values) and save points in it)
between a start and an end section; the toon runs along x (12 ft/s) and jumps (30 ft/s up, gravity
64.3) on the sections' blocks, y = 0, its position broadcast by the smooth node. The client sends:
  claimTreasure(section, i)     touching treasure i (r 1.3); an enemy's dropped treasure is i = n + e
  showShootGun(me, ts)          the water gun; claimEnemyShot(section, e) when the squirt (10 ft in
                                front, 0.15-0.4 s after the shot) hits enemy e
  toonHitByEnemy(me, ts)        an enemy touches it (-1; knocked back 9 ft, no control ~4 s)
  toonFellDown(me, ts)          it fell below z -2 (respawn at the last save point after 1 s)
  toonVictory(me, ts)           it reaches the end section's save point (bonus for the time left)
  reportDone()                  its timer ran out before that
The AI CANCELS the game unless every toon sends toonVictory or reportDone, so the bot always does.

The bot rebuilds the level exactly as the client does (ToonBlitzGlobals.SectionTypes, the start
section at x -48, section k at -24 + the lengths before it) and plays it with the blocks as
platforms (their model tops, moving blocks and enemies on the client's own ping-pong intervals, timed
from setGameStart): runs, jumps up steps and over gaps, rides lifts, grabs the treasures it passes
(and jumps for some), squirts the enemies in front of it, and gets hit or falls now and then, like a
kid. Stompers are left out (it never walks under one while it comes down)."""
import math
import random

from direct.distributed.ClockDelta import globalClockDelta

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import ToonBlitzGlobals as TBG

RUN = 12.0
JUMP_V = 30.0
GRAVITY = 64.348
FLOOR_UP = 1.59            # the toon stands this far above a block's origin (start: z 13.59 on z 12)
BLOCK_W = {'00': 24.0, '01': 12.0, '02': 6.0, '03': 3.0}
TREASURE_R = 1.3 + 1.0
TOON_H = 2.5              # the toon's height (head bumps, walls)
EDGE = 1.0                # the toon (r ~1.4) stands on a block with its middle this far past the edge
START_X = -48.0


def ts16():
    return globalClockDelta.getRealNetworkTime(bits=16)


def blend(u, kind):
    if kind == 'easeInOut':
        return u * u * (3.0 - 2.0 * u)
    return u


def pingPong(p0, p1, dur, t, kind='noBlend'):
    if dur <= 0:
        return p0
    c = t % (2.0 * dur)
    if c < dur:
        u = blend(c / dur, kind)
        a, b = p0, p1
    else:
        u = blend((c - dur) / dur, kind)
        a, b = p1, p0
    return (a[0] + (b[0] - a[0]) * u, a[2] + (b[2] - a[2]) * u)


class Platform:
    def __init__(self, x0, attribs, stomper=None):
        if stomper is not None:
            # a stomper's block: 12 ft wide, its top 3.55 above the stomper (the toon stands 0.81 up)
            self.vertical = self.up = False
            self.w, self.x0, self.p1, self.dur = 12.0, x0, None, 0
            self.p0 = (stomper[0] - 6.0, 0, stomper[2] + 3.55 - 0.78)
            self.extent()
            return
        kind = attribs[0]
        fileId, _, hpr, scale = TBG.BlockTypes[kind]
        w = BLOCK_W[fileId] * scale[0]
        pos = attribs[1]
        self.vertical = hpr[2] in (90, 270)
        self.up = hpr[2] == 270
        self.w = w
        self.x0 = x0
        self.p0 = pos[0]
        self.p1 = pos[1] if len(pos) == 3 else None
        self.dur = pos[2] if len(pos) == 3 else 0
        self.extent()

    def extent(self):
        """The x range it ever covers (a moving block's whole trip), to skip far blocks quickly."""
        xs = [self.p0[0]] + ([self.p1[0]] if self.p1 is not None else [])
        if self.vertical:
            self.minX, self.maxX = min(xs) + self.x0 - 0.8, max(xs) + self.x0 + 0.8
        else:
            self.minX, self.maxX = min(xs) + self.x0, max(xs) + self.x0 + self.w

    def at(self, t):
        """(left x, right x, top the toon stands on, underside z) at game time t. A vertical block
        (roll 270 'F' stands up from its origin, roll 90 'B' hangs down from it) is a wall."""
        if self.p1 is not None:
            x, z = pingPong(self.p0, self.p1, self.dur, t)
        else:
            x, z = self.p0[0], self.p0[2]
        x += self.x0
        if self.vertical:
            lo, hi = (z, z + self.w) if self.up else (z - self.w, z)
            return x - 0.8, x + 0.8, hi + FLOOR_UP - 0.8, lo - 0.8
        return x, x + self.w, z + FLOOR_UP, z - 0.78


class Brain(BaseBrain):
    name = 'twod'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        self.length = TBG.GameDuration.get(self.hood, 150)
        self.buildLevel()
        self.x, self.z = TBG.ToonStartingPosition[0] + self.index, TBG.ToonStartingPosition[2]
        self.vz = 0.0
        self.ground = None
        self.air = None                 # the run speed while airborne (None = on a block)
        self.placeAt(self.x, 0.0, self.z, -90.0)
        self.bot.setAnim('neutral')
        self.pace = random.uniform(0.62, 0.85)
        self.greed = random.uniform(0.5, 0.8)           # jumps for a treasure above it this often
        self.aim = random.uniform(0.55, 0.8)            # squirts that hit
        self.stunUntil = 0.0
        self.pauseUntil = t0 + random.uniform(0.5, 2.0)
        self.nextShot = 0.0
        self.knock = None
        self.dead = None
        self.checkpoint = (self.x, self.z)
        self.finished = False
        self.reported = False
        self.lastT = None
        self.jumpedFor = set()
        self.nextLook = 0.0
        self.waited = 0.0
        self.nextPlan = 0.0
        self.cand = None
        self.dir = 1                      # +1 on through the level, -1 back to find another way
        self.backUntil = 0.0
        self.backFrom = 0.0
        self.at(self.length - self.gameTime() + random.uniform(0.2, 0.8), self.timeUp)
        self.note.update({'treasures': 0, 'shots': 0, 'kills': 0, 'hits': 0, 'falls': 0, 'victory': None})

    def buildLevel(self):
        sel = self.fieldOf('setSectionsSelected', ([],))[0]
        self.platforms = []
        self.treasures = []        # [section, index, x, z, alive]
        self.enemies = []          # [section, index, x0, p0, p1, dur, kind, health]
        self.saves = []            # (x, z save, x, z load)
        for b in TBG.BlockListStart:
            self.platforms.append(Platform(START_X, b))
        x = -24.0
        numP = self.numPlayers
        for s, info in enumerate(sel):
            idx, enemyIdx, treasureIdx, spawnIdx, stomperIdx = info[0], info[1], info[2], info[3], info[4]
            attribs = TBG.SectionTypes[idx]
            length, blocks, enemies, treasures, spawns = attribs[1], attribs[2], attribs[3], attribs[4], attribs[5]
            for b in blocks or ():
                self.platforms.append(Platform(x, b))
            for si in stomperIdx:
                self.platforms.append(Platform(x, None, stomper=attribs[6][si][1]))
            for k, (ti, value) in enumerate(treasureIdx):
                p = treasures[ti][0]
                self.treasures.append([s, k, x + p[0], p[2], True])
            nT = len(treasureIdx)
            for k, ei in enumerate(enemyIdx):
                e = enemies[ei]
                pos = e[1]
                p1 = pos[1] if len(pos) == 3 else None
                kind = 'easeInOut' if p1 is not None and abs(p1[2] - pos[0][2]) > 0 else 'noBlend'
                self.enemies.append([s, k, x, pos[0], p1, pos[2] if p1 is not None else 0, kind,
                                     TBG.EnemyBaseHealth * numP, nT + k])
            for si in spawnIdx:
                sp = spawns[si]
                save = sp[0]
                load = sp[1] if len(sp) > 1 else sp[0]
                self.saves.append((x + save[0], save[2], x + load[0], load[2]))
            x += length
        self.endX = x + TBG.SpawnPointListEnd[0][0][0]
        self.endZ = TBG.SpawnPointListEnd[0][0][2]
        for b in TBG.BlockListEnd:
            self.platforms.append(Platform(x, b))
        self.sections = len(sel)

    # ---- the AI's broadcasts ---------------------------------------------------------------------
    def onField(self, fieldName, args):
        if fieldName == 'setTreasureGrabbed':
            avId, s, i = args
            for t in self.treasures:
                if t[0] == s and t[1] == i:
                    t[4] = False
        elif fieldName == 'setEnemyShot':
            avId, s, e, health = args
            for en in self.enemies:
                if en[0] == s and en[1] == e:
                    en[7] = health
                    if health <= 0 and len(en) < 10:
                        ex, ez = self.enemyPos(en, self.gameTime())
                        en.append(True)
                        # its dropped treasure falls where it was
                        self.treasures.append([s, en[8], ex, max(ez, self.floorUnder(ex, ez + 3)), True])

    # ---- geometry --------------------------------------------------------------------------------
    def enemyPos(self, en, t):
        s, k, x0, p0, p1, dur, kind = en[:7]
        if p1 is None:
            return x0 + p0[0], p0[2]
        x, z = pingPong(p0, p1, dur, t, kind)
        return x0 + x, z

    def near(self, x, reach=24.0):
        """The blocks within reach of x (a jump covers ~12 ft)."""
        return [(i, p) for i, p in enumerate(self.platforms) if p.minX - reach <= x <= p.maxX + reach]

    def tops(self, t, x=None):
        x = self.x if x is None else x
        return [p.at(t) for i, p in self.near(x)]

    def floorUnder(self, x, z, t=None):
        """The highest block top under x that is not above z (+ a small step)."""
        best = -100.0
        for l, r, top, bot in self.tops(self.gameTime() if t is None else t, x):
            if l - EDGE <= x <= r + EDGE and top <= z + 0.6 and top > best:
                best = top
        return best

    def blocked(self, nx, z, t, d=1):
        """Running (d = +1 right, -1 left) into the side of a block higher than a step."""
        for l, r, top, bot in self.tops(t, nx):
            if top > z + 0.6 and bot < z + TOON_H and (
                    (d > 0 and l - EDGE <= nx <= l + 1.0 and nx < r) or (d < 0 and r - 1.0 <= nx <= r + EDGE and nx > l)):
                return True
        return False

    def physStep(self, x, z, vz, vx, dt, t):
        """One airborne step: gravity, the head bumping a block, the side of a block, landing on a
        top it comes down through. Returns (x, z, vz, platform landed on or None)."""
        nz = z + vz * dt - 0.5 * GRAVITY * dt * dt
        nvz = vz - GRAVITY * dt
        nx = x + vx * dt
        tops = [(i,) + p.at(t) for i, p in (self.cand if self.cand is not None else self.near(x))]
        if nvz > 0:
            for i, l, r, top, bot in tops:
                if l - 0.3 <= nx <= r + 0.3 and z + TOON_H <= bot <= nz + TOON_H:
                    nz, nvz = z, 0.0          # bonk
                    break
        for i, l, r, top, bot in tops:
            if top > nz + 0.6 and bot < nz + TOON_H and (
                    (vx > 0 and l - EDGE <= nx <= l + 1.0 and x < l) or (vx < 0 and r - 1.0 <= nx <= r + EDGE and x > r)):
                nx = x
                break
        if nvz <= 0:
            for i, l, r, top, bot in tops:
                if l - EDGE <= nx <= r + EDGE and nz <= top <= z + 0.01:
                    return nx, top, 0.0, i
        return nx, nz, nvz, None

    def planJump(self, gt, must):
        """Try a jump now at a few run speeds, in the same physics (the lifts where they will be).
        Kids stay high when they can: the landing highest up (then furthest on) wins; one that only
        lands back where it stands does not count when it has to get past an edge or a wall. A kid
        misjudges one now and then."""
        d = self.dir
        speeds = [d * RUN * self.pace, d * RUN] + [d * RUN * k / 10.0 for k in (9, 8, 7, 6, 5, 4, 3, 2, 1, 0)]
        best = None
        self.cand = self.near(self.x, 30.0)
        for v in speeds:
            x, z, vz, t = self.x, self.z, JUMP_V, gt
            for _ in range(60):
                t += 0.05
                x, z, vz, landed = self.physStep(x, z, vz, v, 0.05, t)
                if landed is not None:
                    l, r, top, bot = self.platforms[landed].at(t)
                    if min(x - l, r - x) < (0.0 if self.waited > 4.0 else min(0.6, (r - l) / 4.0)):
                        break                       # only just on the edge: not a safe landing
                    onLift = self.ground is not None and self.platforms[self.ground].p1 is not None
                    if onLift and (landed == self.ground or z < self.z - 0.6) and self.waited < 8.0:
                        break                       # rides the lift rather than hopping on it or off it down
                    if (must and d > 0 and x > self.x + 1.0) or z > self.z + 0.6:
                        key = (round(z, 1), x)
                        if best is None or key > best[0]:
                            best = (key, v, (round(x, 1), round(z, 1), landed, round(t - gt, 2)))
                    break
                if z < -2.0:
                    break
        self.lastPlan = best
        self.cand = None
        if best is None:
            return None
        if random.random() < 0.05:
            return best[1] * random.uniform(0.75, 1.2)   # a kid misjudges the run-up
        return best[1]

    # ---- the run ---------------------------------------------------------------------------------
    def timeUp(self):
        if not self.reported:
            self.reported = True
            self.send('reportDone', [])
            self.note['victory'] = False

    def tick(self, t):
        if self.reported:
            return
        gt = self.gameTime()
        dt = 0.1
        if self.dead is not None:
            if t >= self.dead:
                self.dead = None
                self.x, self.z = self.checkpoint
                self.vz, self.air, self.ground = 0.0, None, None
                self.placeAt(self.x, 0.0, self.z, -90.0)
            return
        if self.knock is not None:
            k0, kx = self.knock
            u = (t - k0) / 0.75
            if u < 1.0:
                self.x = kx - 9.0 * u
                self.z = max(self.floorUnder(self.x, self.z + 1.0), self.z)
                self.moveTo(self.x, 0.0, self.z, -90.0)
                return
            self.knock = None
            self.air = 0.0 if self.floorUnder(self.x, self.z) < self.z - 0.05 else None
        control = t >= self.stunUntil and t >= self.pauseUntil
        vx = RUN * self.pace if control else 0.0
        if control and self.air is None and random.random() < 0.004:
            self.pauseUntil = t + random.uniform(0.4, 1.5)      # a kid stops to look
        # ride a moving block
        if self.ground is not None and self.vz == 0.0 and self.air is None:
            p = self.platforms[self.ground]
            l, r, top, bot = p.at(gt)
            if p.p1 is not None:
                self.x += l - p.at(gt - 0.1)[0]      # carried along by a moving block
            if l - EDGE <= self.x <= r + EDGE:
                self.z = top
            else:
                self.ground = None
        if self.air is None:
            onFloor = self.floorUnder(self.x, self.z) >= self.z - 0.05
            if not onFloor:
                self.air = 0.0                      # walked off / the lift went away: fall
            elif control:
                d = self.dir
                if d < 0 and t > self.backUntil:
                    self.dir = d = 1                 # found nothing back there: try the way on again
                edge = self.floorUnder(self.x + 1.5 * d, self.z) < self.z - 0.7
                wall = self.blocked(self.x + 1.5 * d, self.z, gt, d)
                want = edge or wall
                if not want and t >= self.nextLook:
                    # something higher just ahead: kids jump up onto it
                    self.nextLook = t + 0.3
                    for l, r, top, bot in self.tops(gt):
                        if self.z + 0.6 < top <= self.z + 6.9 and (
                                (d > 0 and self.x - 1.0 < l < self.x + 7.0) or (d < 0 and self.x - 7.0 < r < self.x + 1.0)):
                            want = True
                            break
                for tr in self.treasures:
                    if tr[4] and 0.0 < tr[2] - self.x < 3.0 and self.z + 2.0 < tr[3] < self.z + 8.0 \
                            and id(tr) not in self.jumpedFor:
                        self.jumpedFor.add(id(tr))
                        if random.random() < self.greed:
                            want = True
                if want and t < self.nextPlan:
                    want = False
                    if edge or wall:
                        vx = 0.0
                        self.waited += dt
                if want:
                    v = self.planJump(gt, edge or wall)
                    if v is None:
                        self.nextPlan = t + random.uniform(0.4, 0.7)   # looks again in a moment
                    if v is not None:
                        self.waited = 0.0
                        self.air = v
                        self.vz = JUMP_V
                        self.ground = None
                        self.bot.setAnim('jump')
                    elif edge or wall:
                        vx = 0.0                     # waits at the edge (for the lift, or thinks)
                        self.waited += dt
                        if self.waited > 7.0 and d > 0:
                            # a dead end: go back and look for another way up (a lift, a step)
                            self.dir = -1
                            self.backUntil = t + random.uniform(8.0, 14.0)
                            self.backFrom = self.z
                            self.waited = 0.0
                        elif self.waited > 3.0 and d < 0:
                            self.dir = 1
                            self.waited = 0.0
        if self.air is not None:
            ax = self.air                           # a jump, once made, carries on
            landed = None
            for k in (1, 2):              # the planner's step size, so a planned jump lands where planned
                self.x, self.z, self.vz, landed = self.physStep(self.x, self.z, self.vz, ax, 0.05,
                                                                gt + 0.05 * k)
                if landed is not None:
                    break
            if landed is not None:
                self.air, self.vz, self.ground = None, 0.0, landed
                self.bot.setAnim('run' if control else 'neutral')
                if self.dir < 0 and self.z > self.backFrom + 0.6:
                    self.dir = 1                     # up a level: on we go
        else:
            nx = self.x + vx * self.dir * dt
            # a kid walks to the edge, not off it (going down is a jump the planner picked)
            if not self.blocked(nx, self.z, gt, self.dir) and self.floorUnder(nx, self.z, gt) >= self.z - 0.7:
                self.x = nx
            f = self.floorUnder(self.x, self.z)
            if f >= self.z - 0.7:
                self.z = f
                for i, p in enumerate(self.platforms):
                    l, r, top, bot = p.at(gt)
                    if abs(top - f) < 0.01 and l - EDGE <= self.x <= r + EDGE:
                        self.ground = i              # the block I stand on now (a lift, maybe)
                        break
            self.bot.setAnim('run' if vx > 0 else 'neutral')
        self.moveTo(self.x, 0.0, self.z, -90.0 if self.dir > 0 or self.air is not None and self.air >= 0 else 90.0)
        if self.z < -2.0:
            self.send('toonFellDown', [self.bot.avId, ts16()])
            self.note['falls'] += 1
            self.dead = t + 1.0 + random.uniform(0.0, 0.3)
            self.vz, self.ground = 0.0, None
            return
        self.pickUps()
        self.saveNear()
        self.enemiesNear(t, gt)
        if self.x >= self.endX - 3.0 and abs(self.z - self.endZ) < 6.0 and not self.finished:
            self.finished = True
            self.reported = True
            self.send('toonVictory', [self.bot.avId, ts16()])
            self.note['victory'] = round(gt, 1)
            self.bot.setAnim('victory')

    def pickUps(self):
        for tr in self.treasures:
            if tr[4] and abs(tr[2] - self.x) < TREASURE_R and abs(tr[3] - (self.z + 1.5)) < TREASURE_R + 1.0:
                tr[4] = False
                self.send('claimTreasure', [tr[0], tr[1]])
                self.note['treasures'] += 1

    def saveNear(self):
        for sx, sz, lx, lz in self.saves:
            if abs(sx - self.x) < 3.0 and abs(sz - (self.z + 1.5)) < 4.0:
                self.checkpoint = (lx, lz)

    def enemiesNear(self, t, gt):
        for en in self.enemies:
            if len(en) > 9:
                continue
            ex, ez = self.enemyPos(en, gt)
            dx = ex - self.x
            # an enemy walks into me: hit, knocked back
            if abs(dx) < 2.2 and abs((ez + 2.0) - (self.z + 1.5)) < 2.8 and t >= self.stunUntil:
                self.send('toonHitByEnemy', [self.bot.avId, ts16()])
                self.note['hits'] += 1
                self.knock = (t, self.x)
                self.stunUntil = t + 4.0
                self.vz, self.air = 0.0, None
                return
            # one in front of my gun: squirt it
            if 1.0 < dx < 11.0 and abs((ez + 2.0) - (self.z + 3.0)) < 3.0 and t >= self.nextShot and t >= self.stunUntil                     and self.air is None:
                self.send('showShootGun', [self.bot.avId, ts16()])
                self.note['shots'] += 1
                self.nextShot = t + random.uniform(0.5, 1.1)
                if random.random() < self.aim:
                    self.at(random.uniform(0.2, 0.4), self.squirtHit, en)
                if random.random() < 0.5:
                    self.pauseUntil = t + random.uniform(0.3, 0.8)     # stands and squirts
                return

    def squirtHit(self, en):
        if len(en) > 9 or self.reported:
            return
        ex, ez = self.enemyPos(en, self.gameTime())
        if 0.5 < ex - self.x < 12.5 and abs((ez + 2.0) - (self.z + 3.0)) < 3.5:
            self.send('claimEnemyShot', [en[0], en[1]])
