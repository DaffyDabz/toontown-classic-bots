"""Ice Slide (DistributedIceGame): 3 matches x 2 rounds of curling with inner tubes. Each round:
  inputChoice   the toon swings its force arrow (setForceArrowInfo(me, heading, force), 5 Hz while
                it moves; others see the arrow) and presses Ctrl: setAvatarChoice(force, heading)
  setTireInputs every client pushes every tire with those inputs and runs the SAME physics (ODE,
                60 steps/s, deterministic) until every tire has stopped, claims the barrels (+1) and
                TNT (-1) its own tire slid through, then reports all 4 ending positions
                (endingPositions); the AI averages the clients' reports -> setFinalPositions
  scoring       after round 2 the AI scores the match; reportScoringMovieDone after the movie

The bot runs the client's physics headless (DistributedIceWorld's world, walls, tires and obstacles
built in code, the client's step / dampening / auto-disable settings, pitch and roll zeroed every
step), so its endingPositions are the real ones and its claims are what its tire really touched.
It aims like a kid: works out the push that would stop its tire nearest the middle (trying a few
pushes in the same physics), then misses it by a bit."""
import math
import random

from panda3d.core import Quat, Vec3, Vec4, BitMask32, Point3
from panda3d.ode import OdeWorld, OdeSimpleSpace, OdeJointGroup, OdeUtil, OdeBody, OdeMass, OdeSphereGeom, \
    OdePlaneGeom, OdeBoxGeom
from direct.showbase.RandomNumGen import RandomNumGen

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import IceGameGlobals as IGG

MetersToFeet = 3.2808399
STEP = 1.0 / 60.0
MAX_STEPS = 60 * 40
MAX_LOCAL_FORCE = 100.0
MAX_PHYSICS_FORCE = 25000.0
FLOOR = BitMask32(1)
WALL = BitMask32(1 << 1)
OBST = BitMask32(1 << 2)
TIRE_IDS = [1 << 8, 1 << 9, 1 << 10, 1 << 11]
ALL_TIRES = BitMask32(TIRE_IDS[0] | TIRE_IDS[1] | TIRE_IDS[2] | TIRE_IDS[3])


class IceWorld:
    """DistributedIceWorld + MinigamePhysicsWorldBase, with no render."""

    def __init__(self, hood):
        self.world = OdeWorld()
        self.space = OdeSimpleSpace()
        self.group = OdeJointGroup()
        w = self.world
        w.setGravity(0, 0, -32.174)
        w.setAutoDisableFlag(1)
        w.setAutoDisableLinearThreshold(0.5 * MetersToFeet)
        w.setAutoDisableAngularThreshold(OdeUtil.getInfinity())
        w.setAutoDisableSteps(10)
        w.setCfm(1e-05 * MetersToFeet)
        w.initSurfaceTable(3)
        w.setSurfaceEntry(0, 1, 0.2, 0, 0, 0, 0, 0, 0.1)
        w.setSurfaceEntry(0, 0, 0.1, 0.9, 0.1, 0, 0, 0, 0)
        w.setSurfaceEntry(0, 2, 0.9, 0.9, 0.1, 0, 0, 0, 0)
        s = self.space
        self.geoms = []

        def plane(v, cat, surface=None, cid=None):
            g = OdePlaneGeom(s, v)
            g.setCollideBits(ALL_TIRES)
            g.setCategoryBits(cat)
            if surface is not None:
                s.setSurfaceType(g, surface)
            if cid is not None:
                s.setCollideId(g, cid)
            self.geoms.append(g)
        plane(Vec4(0.0, 0.0, 1.0, -20.0), FLOOR)
        plane(Vec4(1.0, 0.0, 0.0, IGG.MinWall[0]), WALL, 2, 1 << 1)
        plane(Vec4(-1.0, 0.0, 0.0, -IGG.MaxWall[0]), WALL, 2, 1 << 1)
        plane(Vec4(0.0, 1.0, 0.0, IGG.MinWall[1]), WALL, 2, 1 << 1)
        plane(Vec4(0.0, -1.0, 0.0, -IGG.MaxWall[1]), WALL, 2, 1 << 1)
        plane(Vec4(0.0, 0.0, 1.0, 0.0), FLOOR, 1, 1)
        s.setAutoCollideWorld(w)
        s.setAutoCollideJointGroup(self.group)
        self.bodies = []
        for i in range(4):
            b = OdeBody(w)
            m = OdeMass()
            m.setSphere(1, IGG.TireRadius)
            b.setMass(m)
            p = IGG.StartingPositions[i]
            b.setPosition(p[0], p[1], p[2])
            b.setAutoDisableDefaults()
            g = OdeSphereGeom(s, IGG.TireRadius)
            s.setSurfaceType(g, 0)
            s.setCollideId(g, TIRE_IDS[i])
            g.setCollideBits(ALL_TIRES | WALL | FLOOR | OBST)
            g.setCategoryBits(BitMask32(TIRE_IDS[i]))
            g.setBody(b)
            b.disable()
            self.geoms.append(g)
            self.bodies.append((b, m))
        cubic = IGG.ObstacleShapes[hood]
        self.obstacles = []
        for pos in IGG.Obstacles[hood]:
            if cubic:
                side = IGG.TireRadius * 2
                g = OdeBoxGeom(s, side, side, side)
            else:
                g = OdeSphereGeom(s, IGG.TireRadius)
            g.setCollideBits(ALL_TIRES)
            g.setCategoryBits(OBST)
            s.setCollideId(g, 1 << 2)
            g.setPosition(pos[0], pos[1], IGG.TireRadius)
            self.geoms.append(g)
            self.obstacles.append((pos[0], pos[1]))

    def destroy(self):
        for g in self.geoms:
            g.destroy()
        for b, m in self.bodies:
            b.destroy()
        self.group.empty()
        self.world.destroy()
        self.space.destroy()

    def setPositions(self, positions):
        q = Quat()
        q.setHpr((0, 0, 0))
        for (b, m), p in zip(self.bodies, positions):
            b.setPosition(p[0], p[1], p[2])
            b.setQuaternion(q)

    def positions(self):
        return [tuple(b.getPosition()) for b, m in self.bodies]

    def run(self, inputs, numPlayers, watch=None):
        """enterMoveTires + the simulation task until every tire is disabled.
        watch(step, positions) is called after each step. Returns (endingPositions, steps)."""
        for b, m in self.bodies:
            b.setAngularVel(0, 0, 0)
            b.setLinearVel(0, 0, 0)
        for i in range(min(numPlayers, len(inputs))):
            force, heading = inputs[i]
            rad = math.radians(heading + 90)
            f = force / MAX_LOCAL_FORCE * MAX_PHYSICS_FORCE
            self.bodies[i][0].addForce(Vec3(math.cos(rad), math.sin(rad), 0) * f)
        for b, m in self.bodies:
            b.enable()
        steps = 0
        while steps < MAX_STEPS:
            OdeUtil.randSetSeed(0)
            self.space.autoCollide()
            self.world.step(STEP)
            for b, m in self.bodies:
                self.world.applyDampening(STEP, b)
            self.group.empty()
            for b, m in self.bodies:
                # DistributedIceWorld.placeBodies: pitch and roll zeroed every step
                q = Quat(b.getQuaternion())
                h = q.getHpr()[0]
                q2 = Quat()
                q2.setHpr((h, 0, 0))
                b.setQuaternion(q2)
            steps += 1
            if watch is not None:
                watch(steps, self.bodies)
            if not any(b.isEnabled() for b, m in self.bodies):
                break
        for b, m in self.bodies:
            b.disable()
        return self.positions(), steps


def placeTreasures(rng, hood, obstacles):
    """DistributedIceGame.setupStartOfMatch's barrels and TNT, same draws."""
    margin = IGG.TireRadius + 1.0
    lo = (int(IGG.MinWall[0] + 5), int(IGG.MinWall[1] + 5))
    hi = (int(IGG.MaxWall[0] - 5), int(IGG.MaxWall[1] - 5))

    def ok(x, y, others, obst=True):
        if obst:
            for ox, oy in obstacles:
                # the obstacle's node sits at z 0, the barrel's at z 1.5
                if math.sqrt((x - ox) ** 2 + (y - oy) ** 2 + IGG.TireRadius ** 2) < margin:
                    return False
        for tx, ty in others:
            if math.hypot(x - tx, y - ty) < margin:
                return False
        return True
    treasures = []
    while len(treasures) < IGG.NumTreasures[hood]:
        x = rng.randrange(lo[0], hi[0])
        y = rng.randrange(lo[1], hi[1])
        if ok(x, y, treasures):
            treasures.append((x, y))
    penalties = []
    while len(penalties) < IGG.NumPenalties[hood]:
        x = rng.randrange(lo[0], hi[0])
        y = rng.randrange(lo[1], hi[1])
        if ok(x, y, treasures) and ok(x, y, penalties, obst=False):
            penalties.append((x, y))
    return treasures, penalties


class Brain(BaseBrain):
    name = 'ice'
    preStart = False

    def onGameStart(self, t0):
        self.world = IceWorld(self.hood)
        self.rng = RandomNumGen(self.doId)
        self.match = 0
        self.round = 0
        self.treasures, self.penalties = [], []
        self.tTaken, self.pTaken = set(), set()
        self.skill = random.uniform(0.6, 0.9)
        self.choice = None
        self.phase = None
        self.note.update({'barrels': 0, 'tnt': 0, 'reports': 0})

    def onGameExit(self):
        try:
            self.world.destroy()
        except Exception:
            pass

    def onField(self, fieldName, args):
        if fieldName == 'setMatchAndRound':
            self.match, self.round = args
        elif fieldName == 'setNewState':
            st = args[0]
            self.phase = st
            if st == 'inputChoice':
                self.startInput()
            elif st == 'scoring':
                self.at(random.uniform(5.0, 8.0), self.send, 'reportScoringMovieDone', [])
        elif fieldName == 'setTireInputs':
            self.slide([(i[0], i[1]) for i in args[0]])
        elif fieldName == 'setFinalPositions':
            self.world.setPositions([(p[0], p[1], p[2]) for p in args[0]])
        elif fieldName == 'setTreasureGrabbed':
            self.tTaken.add(args[1])
        elif fieldName == 'setPenaltyGrabbed':
            self.pTaken.add(args[1])

    # ---- aiming ----------------------------------------------------------------------------------
    def startInput(self):
        if self.round == 0:
            self.world.setPositions(IGG.StartingPositions)
            self.treasures, self.penalties = placeTreasures(self.rng, self.hood, self.world.obstacles)
            self.tTaken, self.pTaken = set(), set()
        # the kid looks at the ice a moment first (and the bots' physics runs don't all land on one frame)
        self.at(random.uniform(0.3, 2.0), self.decide, self.match, self.round)

    def decide(self, match, rnd):
        if (match, rnd) != (self.match, self.round) or self.phase != 'inputChoice':
            return
        me = self.world.positions()[self.index]
        h0 = math.degrees(math.atan2(me[0], -me[1]))          # headsUp(origin): -dx, dy with d = -me
        heading, force = self.aim(me, h0)
        self.choice = (force, heading)
        decide = random.uniform(2.5, 8.0)
        # the arrow swings from the start (at the middle, force 25) to the kid's choice
        n = int(decide / 0.4)
        for k in range(1, n + 1):
            u = k / float(n)
            self.at(k * 0.4, self.send, 'setForceArrowInfo',
                    [self.bot.avId, round(h0 + (heading - h0) * u, 2), round(25 + (force - 25) * u, 2)])
        self.at(decide + 0.2, self.send, 'setAvatarChoice', [round(force, 2), round(heading, 2)])

    def aim(self, me, h0):
        """The push that stops my tire nearest the middle if nobody else moved, then a kid's miss."""
        start = self.world.positions()
        best = (1e9, 50.0)
        for k in range(37):
            f = 6.0 + 1.5 * k
            inputs = [(0.0, 0.0)] * 4
            inputs[self.index] = (f, h0)
            ends, _ = self.world.run(inputs, self.numPlayers)
            self.world.setPositions(start)
            d = math.hypot(ends[self.index][0], ends[self.index][1])
            if d < best[0]:
                best = (d, f)
        err = 1.0 - self.skill
        heading = h0 + random.gauss(0.0, 5.0 + 12.0 * err)
        force = max(5.0, min(100.0, best[1] * random.gauss(1.0, 0.1 + 0.25 * err)))
        return heading, force

    # ---- the slide -------------------------------------------------------------------------------
    def slide(self, inputs):
        claims = []
        mine = self.index
        tt, pt = set(self.tTaken), set(self.pTaken)

        def watch(step, bodies):
            p = bodies[mine][0].getPosition()
            for i, (x, y) in enumerate(self.treasures):
                if i not in tt and math.hypot(p[0] - x, p[1] - y) < IGG.TireRadius + 1.0:
                    tt.add(i)
                    claims.append((step, 'claimTreasure', i))
            for i, (x, y) in enumerate(self.penalties):
                if i not in pt and math.hypot(p[0] - x, p[1] - y) < IGG.TireRadius + 1.0:
                    pt.add(i)
                    claims.append((step, 'claimPenalty', i))
        ends, steps = self.world.run(inputs, self.numPlayers, watch)
        for step, field, i in claims:
            self.at(step * STEP, self.send, field, [i])
            self.note['barrels' if field == 'claimTreasure' else 'tnt'] += 1
        # the client reports once its (real-time) simulation has every tire stopped
        self.at(steps * STEP + random.uniform(0.1, 0.4), self.report, [list(p) for p in ends])

    def report(self, ends):
        self.send('endingPositions', [ends])
        self.note['reports'] += 1
