"""Matching Game (DistributedPairingGame): a team memory game on a board of face-down cards. The
toon walks (OrthoDrive, 8 directions, 11 ft/s) onto a card and presses: openCardRequest(card,
bonusGlowCard). openCardResult(card, avId, matchingCard, points, cardsToTurnDown) tells everyone what
turned up, what matched and what went back down. With 2+ players each toon may hold ONE card up and
a match is made with a card another toon holds up (or, alone, a two-card turn). The AI cancels the
game unless every toon sends reportDone when its timer runs out (or the board is cleared).

The deck is rebuilt from setDeckSeed exactly as the client deals it (card i at x = (i % 8) * 4,
y = (i // 8) * yInc), so the bot walks to real cards. It remembers what it saw about half the time
(a kid; a little more alone), turns cards slower in a crowd, goes for a match when a card it remembers pairs with one somebody holds up, otherwise
turns an unknown card; now and then it holds its card up and signals the others (setSignaling).
bonusGlowCard is computed exactly as the client does (the glowing card at this moment)."""
import math
import random

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import PairingGameGlobals
from toontown.minigame.PlayingCard import PlayingCardBase

SPEED = 11.0
CARDS_PER_ROW = 8


def rankOf(v):
    return PlayingCardBase(v).rank


def traversal(numCards, yRows):
    """DistributedPairingGame.calcBonusTraversal."""
    out = []
    half = CARDS_PER_ROW // 2 + (CARDS_PER_ROW % 2)
    for i in range(half):
        for j in range(2):
            col = i + j * half
            for row in range(yRows):
                idx = row * CARDS_PER_ROW + col
                if 0 <= idx < numCards and col < CARDS_PER_ROW:
                    out.append(idx)
    return out


class Brain(BaseBrain):
    name = 'pairing'
    preStart = False
    moves = True

    def onGameStart(self, t0):
        seed = self.fieldOf('setDeckSeed', (0,))[0]
        n = self.numPlayers
        self.cards = list(PairingGameGlobals.createDeck(seed, n).cards)
        self.num = len(self.cards)
        self.rows = (self.num + CARDS_PER_ROW - 1) // CARDS_PER_ROW
        self.yInc = 4.0 if self.num > 4 * CARDS_PER_ROW else 5.0
        self.bonus = traversal(self.num, self.rows)
        self.maxOpen = 2 if n == 1 else 1
        self.memory = random.uniform(0.4, 0.6) + (0.15 if n == 1 else 0.0)  # chance to remember a card it saw
        # s a kid stands and looks before the next card (in a crowd kids wait on each other more)
        self.pace = random.uniform(3.0, 5.5) * (0.7, 1.0, 1.6, 2.2)[min(n, 4) - 1]
        self.faceUp = {}                              # avId -> [cards up]
        self.gone = set()                             # matched
        self.known = {}                               # card -> rank this kid remembers
        self.goal = None
        self.waitUntil = 0.0
        self.pending = None                           # a card I asked for, not answered yet
        self.lastSignal = 0.0
        starts = [((0, 0), -45), ((28, 16), 135), ((28, 0), 45), ((0, 16), -135)]
        (x, y), h = ((0, 0), 0) if n == 1 else starts[self.index % 4]
        self.placeAt(x, y, 0.0, h)
        self.bot.setAnim('neutral')
        self.length = PairingGameGlobals.calcGameDuration(self.difficulty)
        self.at(self.length - self.gameTime() + random.uniform(0.1, 0.6), self.reportDone, 'timer')
        self.waitUntil = t0 + random.uniform(1.0, 3.0)
        self.reported = False
        self.note.update({'flips': 0, 'matches': 0})

    def cardPos(self, i):
        return (i % CARDS_PER_ROW) * 4.0, (i // CARDS_PER_ROW) * self.yInc

    def reportDone(self, why):
        if not self.reported:
            self.reported = True
            self.send('reportDone', [])
            self.note['done'] = why

    def up(self):
        s = set()
        for l in self.faceUp.values():
            s.update(l)
        return s

    def onField(self, fieldName, args):
        if fieldName != 'openCardResult':
            return
        card, avId, match, points, down = args
        for c in down:
            for l in self.faceUp.values():
                if c in l:
                    l.remove(c)
        if avId == self.bot.avId:
            self.pending = None
            self.note['flips'] += 1
        # everyone sees the card; this kid remembers it most of the time (always its own, for a bit)
        if avId == self.bot.avId or random.random() < self.memory:
            self.known[card] = rankOf(self.cards[card])
        if match >= 0:
            self.gone.update((card, match))
            for l in self.faceUp.values():
                for c in (card, match):
                    if c in l:
                        l.remove(c)
            if avId == self.bot.avId:
                self.note['matches'] += 1
            if len(self.gone) >= self.num:
                self.at(random.uniform(0.3, 1.2), self.reportDone, 'cleared')
        else:
            self.faceUp.setdefault(avId, []).append(card)
        if random.random() > self.memory * 1.15:
            # forget something old now and then
            if self.known:
                self.known.pop(random.choice(list(self.known.keys())), None)

    def choose(self):
        up = self.up()
        mine = self.faceUp.get(self.bot.avId, [])
        down = [i for i in range(self.num) if i not in self.gone and i not in up]
        if not down:
            return None
        byRank = {}
        for c, r in self.known.items():
            if c in down:
                byRank.setdefault(r, []).append(c)
        # 1. a card somebody holds up (another toon's, or my own when alone) that I know the pair of
        others = [c for a, l in self.faceUp.items() for c in l if (a != self.bot.avId or self.maxOpen == 2)]
        for c in others:
            r = rankOf(self.cards[c])
            if byRank.get(r) and random.random() < 0.6:
                return min(byRank[r], key=self.dist)
        # 2. alone: two known of a rank, face down
        if self.maxOpen == 2 and not mine:
            pairs = [l for l in byRank.values() if len(l) >= 2]
            if pairs:
                return min(pairs[0], key=self.dist)
        # 3. my card is up and nobody has its pair up: wait by it a little, waving (signal)
        # 4. an unknown card, the nearer ones more often
        unknown = [i for i in down if i not in self.known] or down
        unknown.sort(key=lambda i: self.dist(i) + random.uniform(0, 14))
        return unknown[0]

    def dist(self, i):
        x, y = self.cardPos(i)
        return math.hypot(x - self.bot.pos[0], y - self.bot.pos[1])

    def orthoStep(self, x, y, dt):
        """OrthoDrive: 8 directions; diagonal until one axis lines up, then straight."""
        px, py = self.bot.pos[0], self.bot.pos[1]
        dx, dy = x - px, y - py
        if abs(dx) < 0.05 and abs(dy) < 0.05:
            return True
        sx = 0 if abs(dx) < 0.05 else (1 if dx > 0 else -1)
        sy = 0 if abs(dy) < 0.05 else (1 if dy > 0 else -1)
        norm = math.hypot(sx, sy)
        step = SPEED * dt
        nx = px + max(-abs(dx), min(abs(dx), sx / norm * step))
        ny = py + max(-abs(dy), min(abs(dy), sy / norm * step))
        h = math.degrees(math.atan2(-sx, sy))
        self.moveTo(nx, ny, 0.0, h)
        return False

    def tick(self, t):
        if self.reported or t < self.waitUntil or self.pending is not None:
            if self.pending is not None and t > self.pendingAt + 3.0:
                self.pending = None          # no answer (someone else turned it): look again
            return
        if self.goal is None or self.goal in self.gone or self.goal in self.up():
            self.goal = self.choose()
            if self.goal is None:
                return
            mine = self.faceUp.get(self.bot.avId, [])
            if mine and self.maxOpen == 1 and self.goal not in self.known and random.random() < 0.25 \
                    and t - self.lastSignal > 6.0:
                # holding a card up: wave the others over for a while before turning another
                self.lastSignal = t
                for k in range(random.randint(1, 3)):
                    self.at(k * 1.67, self.send, 'setSignaling', [self.bot.avId])
                self.waitUntil = t + random.uniform(3.0, 5.0)
                self.goal = None
                return
        x, y = self.cardPos(self.goal)
        if self.orthoStep(x, y, 0.1):
            self.bot.setAnim('neutral')
            gt = self.gameTime()
            glow = self.bonus[int(gt / 0.5) % len(self.bonus)] if self.bonus else 0
            self.send('openCardRequest', [self.goal, glow])
            self.pending = self.goal
            self.pendingAt = t
            self.goal = None
            if self.maxOpen == 2 and len(self.faceUp.get(self.bot.avId, [])) % 2 == 0:
                self.waitUntil = t + random.uniform(0.8, 1.6)     # alone: the second card of the turn
            else:
                self.waitUntil = t + self.pace * random.uniform(0.7, 1.4)
        else:
            self.bot.setAnim('run')
