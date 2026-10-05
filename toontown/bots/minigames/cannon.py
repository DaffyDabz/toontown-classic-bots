"""Cannon Game (DistributedCannonGame): aim with the arrows (setCannonPosition at most every 0.5 s
while turning), fire (setCannonPosition + setCannonLit), the AI answers setCannonWillFire(fireTime)
and the client flies the toon; only when that flight lands in the tower's water does the client send
setToonWillLandInWater(timeOfImpact) (absolute game time), which ends the game for everybody.

The bot aims like a kid: it knows where the tower is (the client places it from RandomNumGen(doId),
rebuilt here), guesses the angle, and gets closer every shot. It claims the water ONLY when the
real trajectory (toontown.minigame.Trajectory, the client's own physics, from the barrel pivot)
lands well inside the rim, so what every client animates agrees with the claim."""
import math
import random

from direct.showbase.RandomNumGen import RandomNumGen
from panda3d.core import Point3, Vec3

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import CannonGameGlobals
from toontown.minigame.Trajectory import Trajectory

TOWER_Y_RANGE = CannonGameGlobals.TowerYRange
TOWER_X_RANGE = int(TOWER_Y_RANGE / 2.0)
TOWER_HEIGHT = 43.85
TOWER_RADIUS = 10.5
SAFE_RADIUS = 7.0            # claim water only this far inside the 10.5 ft rim (our launch point is estimated)
CANNON_Y = -int(TOWER_Y_RANGE / 2 * 1.3)
CANNON_X_SPACING = 12
CANNON_Z = 20
V0 = 94.0
ROT_MIN, ROT_MAX = -20.0, 20.0
ANG_MIN, ANG_MAX = 10.0, 85.0
TURN = 15.0                  # deg/s the arrows turn the barrel


def towerPos(doId):
    rng = RandomNumGen(doId)
    yMin, yMax = int(TOWER_Y_RANGE * 0.3), TOWER_Y_RANGE
    y = rng.randint(yMin, yMax)
    x = rng.randint(0, TOWER_X_RANGE)
    x = x - int(TOWER_X_RANGE / 2.0)
    x = float(x) * (float(y) / float(TOWER_Y_RANGE))
    y = y - int(TOWER_Y_RANGE / 2.0)
    return Point3(x, y, 0.0)


def launch(cannon, rot, ang):
    """Head position (0, 6, 0) under the barrel, the barrel pivot ~3 ft over the cannon base."""
    r, a = math.radians(rot), math.radians(ang)
    fwd = Vec3(-math.sin(r) * math.cos(a), math.cos(r) * math.cos(a), math.sin(a))
    start = Point3(cannon[0], cannon[1], cannon[2] + 3.0) + fwd * 6.0
    return start, fwd * V0


def flight(cannon, tower, rot, ang, t0=0.0):
    """(timeOfImpact or None, miss distance at water height)."""
    start, vel = launch(cannon, rot, ang)
    tr = Trajectory(t0, start, vel)
    disc = Point3(tower[0], tower[1], tower[2] + TOWER_HEIGHT)
    t = tr.checkCollisionWithDisc(disc, SAFE_RADIUS)
    # miss distance where it comes down through the water's height (for aiming)
    vz, z0, zd = vel[2], start[2], disc[2]
    disc2 = vz * vz - 2 * 32.0 * (zd - z0)
    if disc2 < 0:
        return None, 999.0
    tt = (vz + math.sqrt(disc2)) / 32.0
    px, py = start[0] + vel[0] * tt, start[1] + vel[1] * tt
    return (t if t > 0 else None), math.hypot(px - disc[0], py - disc[1])


def solve(cannon, tower):
    """The heading/angle that drops the toon in the water (the answer a kid is groping for)."""
    dx, dy = tower[0] - cannon[0], tower[1] - cannon[1]
    rot = max(ROT_MIN, min(ROT_MAX, math.degrees(math.atan2(-dx, dy))))
    best = (999.0, 45.0)
    a = ANG_MIN
    while a <= ANG_MAX:
        _, miss = flight(cannon, tower, rot, a)
        if miss < best[0]:
            best = (miss, a)
        a += 0.25
    return rot, best[1]


class Brain(BaseBrain):
    name = 'cannon'

    def onGameStart(self, t0):
        n = self.numPlayers
        self.cannon = Point3(self.index * CANNON_X_SPACING - (n - 1) * CANNON_X_SPACING / 2.0, CANNON_Y, CANNON_Z)
        self.tower = towerPos(self.doId)
        self.goodRot, self.goodAng = solve(self.cannon, self.tower)
        self.rot, self.ang = 0.0, 10.0
        self.shot = 0
        self.flying = False
        self.skill = random.uniform(0.6, 1.4)          # how fast this kid learns the aim
        self.at(random.uniform(1.5, 4.0), self.aim)

    def aim(self):
        """Pick where to point this shot, then turn the barrel there at the arrows' speed."""
        self.shot += 1
        err = 1.0 / (1.0 + 0.9 * self.skill * (self.shot - 1))
        self.targetRot = max(ROT_MIN, min(ROT_MAX, self.goodRot + random.gauss(0, 5.0 * err)))
        self.targetAng = max(ANG_MIN, min(ANG_MAX, self.goodAng + random.gauss(0, 6.0 * err)))
        self.aiming = True
        self.lastSent = 0.0

    def tick(self, t):
        if not getattr(self, 'aiming', False):
            return
        dt = 0.1
        moved = False
        for attr, target in (('rot', self.targetRot), ('ang', self.targetAng)):
            cur = getattr(self, attr)
            d = target - cur
            if abs(d) > 0.01:
                setattr(self, attr, cur + max(-TURN * dt, min(TURN * dt, d)))
                moved = True
        if moved and t - self.lastSent >= 0.5:
            self.lastSent = t
            self.send('setCannonPosition', [self.rot, self.ang])
        if not moved:
            self.aiming = False
            self.send('setCannonPosition', [self.rot, self.ang])
            self.at(random.uniform(0.3, 1.5), self.fire)

    def fire(self):
        self.send('setCannonPosition', [self.rot, self.ang])
        self.send('setCannonLit', [self.rot, self.ang])
        self.flying = True

    def onField(self, fieldName, args):
        if fieldName == 'setCannonWillFire' and args[0] == self.bot.avId:
            fireTime, rot, ang = args[1], args[2], args[3]
            land, miss = flight(self.cannon, self.tower, rot, ang, t0=fireTime)
            self.note.setdefault('misses', []).append(round(miss, 1))
            if land is not None:
                self.send('setToonWillLandInWater', [land])
                self.note['water'] = round(land, 1)
            else:
                # the flight (~3-6 s) + the 2 s landing, then back to the cannon to aim again
                self.at(random.uniform(6.0, 9.0), self.aim)
