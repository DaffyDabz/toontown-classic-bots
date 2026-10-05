"""Matching / Pattern Game (DistributedPatternGame): Minnie dances a pattern (setPattern, the whole
cumulative list: 2, 4, 6, 8 moves), the toon copies it with the arrows (reportButtonPress per key,
cosmetic), then reportPlayerPattern(pattern, secondsTaken). Between rounds the client says
reportPlayerReady (the AI ABORTS the game if a toon is not ready within 15 s, so the bot always is).
A kid copies at 0.4-0.75 s a key and slips more on the long patterns."""
import random

from toontown.bots.minigames.base import Brain as BaseBrain

STEP = 0.75          # ~ one dance step of Minnie's demo (danceStepDuration), s
SLIP = {2: 0.03, 4: 0.08, 6: 0.16, 8: 0.26}


class Brain(BaseBrain):
    name = 'pattern'

    def onGameStart(self, t0):
        self.round = 0
        self.at(random.uniform(0.2, 0.8), self.ready)

    def ready(self):
        self.send('reportPlayerReady', [])

    def onField(self, fieldName, args):
        if fieldName == 'setPattern':
            self.round += 1
            pattern = list(args[0])
            demo = 0.5 + len(pattern) * STEP + 0.2
            self.at(demo + random.uniform(0.3, 0.9), self.play, self.round, pattern, 0, [], globalClock.getRealTime() + demo)
        elif fieldName == 'setPlayerPatterns':
            if self.round < 4:
                # the client plays everyone's answers back, then asks for the next one
                self.at(random.uniform(1.8, 2.6), self.ready)

    def play(self, rnd, pattern, i, pressed, t0):
        if rnd != self.round:
            return
        now = globalClock.getRealTime()
        want = pattern[i]
        key = want
        if random.random() < SLIP.get(len(pattern), 0.3) / max(1, len(pattern)) * 3:
            key = random.choice([k for k in range(4) if k != want])
        wrong = int(key != want)
        pressed = pressed + [key]
        self.send('reportButtonPress', [key, wrong])
        if wrong or len(pressed) == len(pattern):
            took = now - t0
            self.at(STEP, self.report, rnd, pressed, took)
            self.note.setdefault('rounds', []).append(int(not wrong))
            return
        self.at(random.uniform(0.4, 0.75), self.play, rnd, pattern, i + 1, pressed, t0)

    def report(self, rnd, pressed, took):
        if rnd == self.round:
            self.send('reportPlayerPattern', [pressed, min(took, 65.0)])
