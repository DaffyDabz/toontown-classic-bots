"""Trolley Tracks (DistributedTravelGame): the board game between trolley games while the trolley
holiday runs (2013: every Thursday). Each round the AI sends setTimerStartTime and waits
TravelGameGlobals.InputTimeout for every toon's setAvatarChoice(votes, direction) (direction 0 = up,
1 = down, votes 0..the toon's remaining votes); setServerChoices(votes, directions, ...) closes the
round. A kid scrolls the list for a few seconds, then spends some votes on a random way (the last
round, more of what is left). Never stalls: the AI's own timeout covers a missed vote."""
import random

from toontown.bots.minigames.base import Brain as BaseBrain
from toontown.minigame import TravelGameGlobals


class Brain(BaseBrain):
    name = 'travel'

    def __init__(self, game):
        BaseBrain.__init__(self, game)
        self.votes = TravelGameGlobals.DefaultStartingVotes
        self.rounds = 0
        self.note['votes'] = []
        self.setVotes(game.obj.get('setStartingVotes', None))

    def setVotes(self, args):
        try:
            self.votes = int(args[0][self.index])
        except (IndexError, TypeError, ValueError):
            pass

    def onField(self, fieldName, args):
        if fieldName == 'setStartingVotes':
            self.setVotes(args)
        elif fieldName == 'setTimerStartTime':
            self.rounds += 1
            wait = min(TravelGameGlobals.InputTimeout - 2.0, random.uniform(2.5, 9.0))
            self.at(max(1.0, wait), self.choose, self.rounds)
        elif fieldName == 'setServerChoices':
            try:
                self.votes = max(0, self.votes - max(0, int(args[0][self.index])))
            except (IndexError, TypeError, ValueError):
                pass

    def choose(self, rnd):
        if rnd != self.rounds or self.done:
            return
        have = max(0, self.votes)
        if have == 0 or random.random() < 0.15:
            spend = 0
        else:
            spend = random.randint(1, max(1, have // 2 + (have // 2 if rnd >= 3 else 0)))
        direction = random.randint(0, 1)
        self.send('setAvatarChoice', [spend, direction])
        self.note['votes'].append((spend, direction))
