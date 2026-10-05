"""Toon Slingshot (DistributedTargetGame): 3 rounds. Each round the toon is pulled back in the
slingshot during a 10 s countdown, flies down the field (setSmPosHpr at 5 Hz, anim 'swim', the
umbrella 'jumpAirborne' on a long glide), bounces, and lands: setScore(int x, int y) right away and
setPlayerDone 5 s later (the client's own delay). The AI waits for setPlayerDone from every toon each
round, so the bot sends exactly one per round, never before the round has started.

The targets are rebuilt from setTargetSeed exactly as the AI places them (random.seed(seed) +
setupTargets), so the landing the bot reports scores what it looks like it scores: a kid picks a
target, and lands in it about 60 % of the time (mostly the outer ring), otherwise just short, long or
wide of it."""
import math
import random

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import TargetGameGlobals as TG

POWER_MIN, POWER_MAX = 40.0, 120.0
MAX_Y = 900.0


def checkPlace(x, y, fill, placeList):
    for p in placeList:
        if math.sqrt((p[0] - x) ** 2 + (p[1] - y) ** 2) - (fill + p[2]) <= 0.0:
            return 0
    return 1


def rebuildTargets(seed, hood):
    """DistributedTargetGameAI.setupTargets with random.seed(seed): [(x, y, combinedIndex)]."""
    rng = random.Random()
    rng.seed(seed)
    fieldWidth = TG.ENVIRON_WIDTH * 3
    fieldLength = TG.ENVIRON_LENGTH * 3.7
    pattern = TG.difficultyPatterns[hood]
    counts, values, sizes, subParts = pattern[0], pattern[1], pattern[2], pattern[4]
    highest = max(values)
    placeValue = highest * 0.5
    placed, placeList = [], []
    for typeIndex in range(len(counts)):
        for _ in range(counts[typeIndex]):
            while True:
                x = rng.random() * (fieldWidth * 0.6) - fieldWidth * 0.6 * 0.5
                y = (rng.random() * 0.6 + (0.0 + 0.4 * (placeValue * 1.0 / (highest * 1.0)))) * fieldLength
                fill = sizes[typeIndex]
                if checkPlace(x, y, fill, placeList):
                    break
            placeList.append((x, y, fill))
            sub = subParts[typeIndex]
            while sub:
                placed.append((x, y, typeIndex + sub - 1))
                sub -= 1
    return placed, values, sizes


def scoreAt(placed, values, sizes, x, y):
    """What the AI's setScore gives for a landing at (x, y)."""
    top = 0
    for tx, ty, idx in placed:
        if math.hypot(tx - x, ty - y) < sizes[idx] and values[idx] > top:
            top = values[idx]
    return top


def flightY(power):
    t = 0.08 * power
    return 10.0 * power * (1.0 - math.exp(-0.1 * t))


def powerFor(y):
    lo, hi = POWER_MIN, POWER_MAX
    for _ in range(30):
        mid = (lo + hi) / 2.0
        if flightY(mid) < y:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


class Brain(BaseBrain):
    name = 'target'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        seed = self.fieldOf('setTargetSeed', (0,))[0]
        self.placed, self.values, self.sizes = rebuildTargets(seed, self.hood)
        self.aim = random.uniform(0.32, 0.48)         # this kid's chance to land in the target it picks
        self.round = 1
        self.flight = None
        self.best = 0
        self.note['scores'] = []
        self.startRound()

    def startX(self):
        n = self.numPlayers
        return 14.0 * self.index - 14.0 * (n - 1) / 2.0 + 10.0

    def startRound(self):
        self.x0 = self.startX()
        self.placeAt(self.x0, 0.0, 1.0, 0.0)
        self.bot.setAnim('neutral')
        self.flight = None
        self.landed = False
        rnd = self.round
        # the countdown runs 10 s; the kid taps to power up and lets go (or the launch is forced ~11 s)
        launch = random.uniform(4.0, 10.8)
        self.at(launch - 1.0, self.pull, rnd)
        self.at(launch, self.launch, rnd)

    def onField(self, fieldName, args):
        if fieldName == 'setRoundDone':
            self.round += 1
            self.at(random.uniform(0.2, 0.5), self.startRound)

    # ---- the round -----------------------------------------------------------------------------
    def pickLanding(self):
        """Where this kid's shot lands: at a target it picked ~aim of the time, else near it."""
        centres = {}
        for x, y, idx in self.placed:
            if y < MAX_Y - 10:
                centres[(x, y)] = min(idx, centres.get((x, y), 99))
        # kids aim at the big targets, the near ones more often
        want = 0 if random.random() < 0.7 else 1
        pool = [(x, y, i) for (x, y), i in centres.items() if i == want] or [(x, y, i) for (x, y), i in centres.items()]
        tx, ty, idx = random.choice(sorted(pool, key=lambda c: c[1] + random.uniform(0, 400))[:4])
        r = self.sizes[idx]
        if random.random() < self.aim:
            # in: mostly the outer ring, now and then the inner one
            d = r * (random.uniform(0.55, 0.92) if random.random() < 0.8 else random.uniform(0.0, 0.45))
        else:
            d = r * random.uniform(1.15, 2.5)
        a = random.uniform(0, 2 * math.pi)
        lx = max(-TG.MAX_FIELD_SPAN + 1, min(TG.MAX_FIELD_SPAN - 1, tx + math.cos(a) * d))
        ly = max(40.0, min(MAX_Y - 1, ty + math.sin(a) * d))
        return lx, ly

    def pull(self, rnd):
        if rnd != self.round or self.flight is not None:
            return
        self.pullBack = random.uniform(5.0, 12.0)
        self.moveTo(self.x0 + random.uniform(-3, 3), -self.pullBack, 1.0, 0.0)

    def launch(self, rnd):
        if rnd != self.round or self.flight is not None:
            return
        lx, ly = self.pickLanding()
        p = powerFor(min(ly, flightY(POWER_MAX)))
        dur = 0.08 * p
        glide = 0.0
        if ly > flightY(POWER_MAX) - 5:
            glide = 2.0 + (ly - flightY(POWER_MAX)) / 40.0      # the umbrella carries it further
        apex = min(90.0, (0.5 * p) ** 2 / (2 * 12.5))
        x0 = self.bot.pos[0]
        y0 = self.bot.pos[1]
        self.flight = {'t0': self.gameTime(), 'dur': dur, 'glide': glide, 'apex': apex, 'x0': x0, 'y0': y0,
                       'lx': lx, 'ly': ly, 'bounce': random.randint(1, 3), 'umbrella': False}
        self.bot.setAnim('swim')

    def tick(self, t):
        f = self.flight
        if f is None or self.landed:
            return
        s = self.gameTime() - f['t0']
        total = f['dur'] + f['glide']
        bounceT = 0.5 * f['bounce']
        if s < total:
            u = s / total
            # forward speed decays (drag): most of the distance is covered early
            k = 1.6
            fy = (1.0 - math.exp(-k * u)) / (1.0 - math.exp(-k))
            y = f['y0'] + (f['ly'] - 8.0 * f['bounce'] - f['y0']) * fy
            x = f['x0'] + (f['lx'] - f['x0']) * fy
            if f['glide'] and s > f['dur'] * 0.5:
                # umbrella: slow sink from half the height
                if not f['umbrella']:
                    f['umbrella'] = True
                    self.bot.setAnim('jumpAirborne')
                v = (s - f['dur'] * 0.5) / (total - f['dur'] * 0.5)
                z = f['apex'] * (1.0 - v) + 0.1
            else:
                zu = s / f['dur']
                z = 1.0 + 4.0 * f['apex'] * zu * (1.0 - zu)
            self.moveTo(x, y, max(0.1, z), 0.0)
        elif s < total + bounceT:
            # small bounces on the way to the resting spot
            b = (s - total) / bounceT
            y = f['ly'] - 8.0 * f['bounce'] * (1.0 - b)
            z = 0.1 + 3.0 * abs(math.sin(b * math.pi * f['bounce'])) * (1.0 - b)
            if f['umbrella']:
                f['umbrella'] = False
                self.bot.setAnim('swim')
            self.moveTo(f['lx'], y, z, 0.0)
        else:
            self.land()

    def land(self):
        f = self.flight
        self.landed = True
        rnd = self.round
        self.moveTo(f['lx'], f['ly'], 0.1, 0.0)
        self.bot.setAnim('neutral')
        x, y = int(f['lx']), int(f['ly'])
        self.send('setScore', [x, y])
        got = scoreAt(self.placed, self.values, self.sizes, x, y)
        self.best = max(self.best, got)
        self.note['scores'].append(got)
        self.at(1.0, self.bot.setAnim, 'victory' if got else 'Sad')
        self.at(5.0, self.done_, rnd)

    def done_(self, rnd):
        if rnd == self.round:
            self.send('setPlayerDone', [])
            self.bot.setAnim('neutral')
