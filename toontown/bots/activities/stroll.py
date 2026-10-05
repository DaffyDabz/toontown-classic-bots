"""Default activity (TTBOTS P2): stroll the walk map and stand about, like a toon killing time.
Half the walks head for a landmark of the playground (fountain, gazebo, trolley, shops, pond...),
the rest are short hops around where the toon stands. P4 makes this richer (chat, emotes, shops).
"""
import random

from toontown.bots.activities import Activity, register
from toontown.bots.BotToon import tripSpeed

LANDMARKS = ('fountain', 'gazebo', 'trolley', 'gagshop', 'hq', 'fishing', 'clothes', 'petshop', 'statue',
             'toonhall', 'bank', 'library', 'school', 'partygate', 'gametable', 'picnictable', 'golfkart',
             'racingpad', 'viewingpad', 'leaderboard', 'kartshop', 'door')


@register
class Stroll(Activity):
    name = 'stroll'
    weight = 1.0

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None

    def start(self):
        self.rounds = random.randint(3, 8)
        self.idleUntil = 0.0
        return self.__walk()

    def __goal(self):
        bot, a = self.bot, self.bot.area
        if random.random() < 0.4:
            places = [p for p in a.wm.places() if p['kind'] in LANDMARKS and a.placeNode(p) is not None]
            if places:
                p = random.choice(places)
                k = a.nodeNear(p['pos'][0], p['pos'][1], 18.0)
                if k is not None and bot.node is not None and a._comp[k] == a._comp[bot.node]:
                    return k
        return a.nodeNear(bot.pos[0], bot.pos[1], random.uniform(25.0, 80.0))

    def __walk(self):
        bot = self.bot
        if bot.node is None:
            bot.node = bot.area.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
        for _ in range(4):
            k = self.__goal()
            if k is not None and k != bot.node and bot.walkTo(k, *tripSpeed()):
                return True
        return True     # nowhere to go right now: stand, try again after the pause

    def step(self, now):
        bot = self.bot
        if bot.path:
            return True
        if not self.idleUntil:
            r = random.random()
            self.idleUntil = now + (random.uniform(8.0, 18.0) if r < 0.1 else random.uniform(1.0, 4.5))
            bot.setAnim('neutral')
            return True
        if now < self.idleUntil:
            return True
        self.idleUntil = 0.0
        self.rounds -= 1
        if self.rounds <= 0:
            return False
        self.__walk()
        return True

    def stop(self, why):
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()
