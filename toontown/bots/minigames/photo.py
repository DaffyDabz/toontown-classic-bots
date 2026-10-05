"""Toon Snapshot (DistributedPhotoGame): the toon stands at a tripod (nothing about it is
networked) and photographs the wandering subjects. For a photo that matches one of the 5
assignments and beats its own best, the client sends newClientPhotoScore(subjectIndex, pose,
stars 1-5). filmOut() when the film is used up. The AI runs a per-hood timer (120 s TTC .. 85 s)
and waits on nobody.

The assignments are the AI's own (PhotoGameBase.generateAssignmentTemplates with random.seed(doId),
rebuilt here with a private Random so the bot process's random stays unseeded). A kid snaps every
few seconds, gets a scorable picture every 8-15 s, improves as it goes, and a third of kids run
out of film near the end."""
import random

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import PhotoGameGlobals


def assignments(doId, hood, n=PhotoGameGlobals.ONSCREENASSIGNMENTS):
    data = PhotoGameGlobals.AREA_DATA[hood]
    rng = random.Random(doId)
    out = []
    num = len(data['PATHS'])
    if num == 0:
        return out, data
    while len(out) < n:
        s = rng.choice(list(range(num)))
        pose = (None, None)
        while pose[0] is None:
            k = data['PATHANIMREL'][s]
            pose = rng.choice(data['ANIMATIONS'][k] + data['MOVEMODES'][k])
        t = (s, pose[0])
        if t not in out:
            out.append(t)
    return out, data


class Brain(BaseBrain):
    name = 'photo'

    def onGameStart(self, t0):
        self.assign, data = assignments(self.doId, self.hood)
        self.length = data['TIME']
        self.film = data['FILMCOUNT'] - 1          # the AI counts every message
        self.best = {}
        self.sent = 0
        self.skill = random.uniform(-0.7, 0.4)
        self.focus = random.sample(range(len(self.assign)), min(len(self.assign), random.randint(1, 3)))
        if self.assign:
            self.at(random.uniform(6.0, 12.0), self.snap)
        if random.random() < 0.33:
            self.at(self.length * random.uniform(0.75, 0.95), self.out)

    def snap(self):
        gt = self.gameTime()
        if gt > self.length - 1.5 or self.sent >= self.film:
            return
        i = random.choice(self.focus)
        s, pose = self.assign[i]
        stars = max(1.0, min(5.0, round(random.gauss(1.5 + self.skill + gt / self.length * 1.0, 0.8))))
        if stars > self.best.get(i, 0):
            self.best[i] = stars
            self.send('newClientPhotoScore', [s, pose, float(stars)])
            self.sent += 1
            self.note['best'] = dict(self.best)
        self.at(random.uniform(8.0, 15.0), self.snap)

    def out(self):
        self.send('filmOut', [])
        self.film = 0
        self.note['filmOut'] = round(self.gameTime(), 1)
