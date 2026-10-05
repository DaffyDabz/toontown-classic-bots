"""Toon Tag (DistributedTagGame): 60 s. The AI picks who is IT (setIt) and drops treasures
(DistributedTagTreasure DOs in the game zone, setPosition). A toon that is not IT runs into a
treasure (its 2 ft sphere) -> requestGrab() on the treasure (+2); IT runs into another toon (~2.4 ft)
-> tag(avId) and that toon is IT (the AI ignores tags for 2 s after one). The toon walks like in the
playground (20 ft/s, IT 26 ft/s), its position broadcast by the smooth node.

The bot is a kid playing it: not IT, it runs for the treasures near it and away from IT when IT
comes close (kids get caught too); IT, it chases the nearest toon and tags it when it touches.
It dawdles now and then, and does not go for every treasure."""
import math
import random

from toontown.bots.minigames.base import Brain as BaseBrain

RUN = 20.0
IT_RUN = 26.0
GRAB_R = 2.0
TAG_R = 2.3
EDGE = 43.0
STARTS = [(0, 10, 180), (10, 0, 90), (0, -10, 0), (-10, 0, -90)]


class Brain(BaseBrain):
    name = 'tag'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        x, y, h = STARTS[self.index % 4]
        self.placeAt(x, y, 0.0, h)
        self.bot.setAnim('neutral')
        self.it = self.fieldOf('setIt', (0,))[0]
        self.itSince = t0
        self.skill = random.uniform(0.7, 0.9)          # of full speed: kids zig-zag, overshoot
        self.greed = random.uniform(0.55, 0.85)        # how many treasures it goes for
        self.fear = random.uniform(12.0, 20.0)         # ft: IT this close -> run
        self.tried = set()                             # treasures asked for
        self.skip = set()                              # treasures this kid is not interested in
        self.goal = None
        self.dawdleUntil = t0 + random.uniform(0.3, 1.5)
        self.tagSent = 0.0
        self.wander = None
        self.note.update({'grabs': 0, 'tags': 0, 'itTimes': 0})

    def onField(self, fieldName, args):
        if fieldName == 'setIt':
            self.it = args[0]
            self.itSince = self.gameTime()
            self.goal = None
            if self.it == self.bot.avId:
                self.note['itTimes'] += 1

    def treasures(self):
        out = []
        for o in self.objectsOf('DistributedTagTreasure'):
            if o.get('setGrab') is not None or o.doId in self.tried:
                continue
            p = o.get('setPosition')
            if p is not None:
                out.append((o, p[0], p[1]))
        return out

    def others(self):
        out = []
        for a in self.avIds:
            if a == self.bot.avId:
                continue
            p = self.posOf(a)
            if p is not None:
                out.append((a, p[0], p[1]))
        return out

    def run(self, x, y, speed, dt=0.1):
        px, py = self.bot.pos[0], self.bot.pos[1]
        x = max(-EDGE, min(EDGE, x))
        y = max(-EDGE, min(EDGE, y))
        there = self.walkToward(x, y, speed, dt)
        self.bot.setAnim('neutral' if there else 'run')
        return there

    def tick(self, t):
        gt = self.gameTime()
        if t < self.dawdleUntil:
            self.bot.setAnim('neutral')
            return
        me = self.bot.pos
        if self.it == self.bot.avId:
            self.playIt(t, gt, me)
        else:
            self.playRunner(t, gt, me)

    def playIt(self, t, gt, me):
        if gt - self.itSince < random.uniform(1.0, 2.0):
            return                                     # just got tagged: a moment to turn around
        others = self.others()
        if not others:
            return
        # chase the nearest (sometimes a different one, as kids switch)
        if self.goal is None or random.random() < 0.01 or self.goal not in [a for a, _, _ in others]:
            others.sort(key=lambda e: math.hypot(e[1] - me[0], e[2] - me[1]) + random.uniform(0, 10))
            self.goal = others[0][0]
        p = self.posOf(self.goal)
        if p is None:
            self.goal = None
            return
        d = math.hypot(p[0] - me[0], p[1] - me[1])
        if d < TAG_R and t - self.tagSent > 1.0:
            self.send('tag', [self.goal])
            self.tagSent = t
            self.note['tags'] += 1
            return
        self.run(p[0], p[1], IT_RUN * self.skill)

    def playRunner(self, t, gt, me):
        itPos = self.posOf(self.it) if self.it else None
        if itPos is not None:
            dx, dy = me[0] - itPos[0], me[1] - itPos[1]
            d = math.hypot(dx, dy)
            if d < self.fear:
                # run away from IT, bending toward the middle when near a wall
                if d < 0.01:
                    dx, dy, d = 1.0, 0.0, 1.0
                ax, ay = dx / d, dy / d
                cx, cy = -me[0] / EDGE, -me[1] / EDGE
                self.run(me[0] + (ax + cx * 0.8) * 10.0, me[1] + (ay + cy * 0.8) * 10.0, RUN * self.skill)
                self.goal = None
                return
        # treasure: grab it when I touch it (the client's sphere), as the client does
        for o, x, y in self.treasures():
            if math.hypot(x - me[0], y - me[1]) < GRAB_R:
                self.bot.send('requestGrab', [], doId=o.doId, className='DistributedTagTreasure')
                self.tried.add(o.doId)
                self.note['grabs'] += 1
                if random.random() < 0.25:
                    self.dawdleUntil = t + random.uniform(0.5, 2.0)
                self.goal = None
                return
        ts = [(o, x, y) for o, x, y in self.treasures() if o.doId not in self.skip]
        if self.goal is None or self.goal not in [o.doId for o, _, _ in ts]:
            self.goal = None
            if ts:
                ts.sort(key=lambda e: math.hypot(e[1] - me[0], e[2] - me[1]) + random.uniform(0, 15))
                o = ts[0][0]
                if random.random() < self.greed:
                    self.goal = o.doId
                else:
                    self.skip.add(o.doId)
        if self.goal is not None:
            for o, x, y in ts:
                if o.doId == self.goal:
                    self.run(x, y, RUN * self.skill)
                    return
        # nothing to go for: jog about
        if self.wander is None or math.hypot(self.wander[0] - me[0], self.wander[1] - me[1]) < 2.0:
            self.wander = (random.uniform(-30, 30), random.uniform(-30, 30))
            if random.random() < 0.3:
                self.dawdleUntil = t + random.uniform(0.5, 2.5)
        self.run(self.wander[0], self.wander[1], RUN * 0.6)
