"""Race Game (DistributedRaceGame): each turn every toon picks 1-4 (same number as someone = nobody
of them moves). The client sends setAvatarChoice once per setTimerStartTime; 20 s timer, 0 on
timeout. A kid clicks after watching the walk, 2-7 s in, and likes the big numbers."""
import random

from toontown.bots.minigames.base import Brain as BaseBrain


class Brain(BaseBrain):
    name = 'race'

    def onField(self, fieldName, args):
        if fieldName == 'setTimerStartTime':
            # the AI is in waitClientsChoices now: exactly one choice this turn
            self.turn = getattr(self, 'turn', 0) + 1
            turn = self.turn
            self.at(random.uniform(2.0, 7.0), self.choose, turn)

    def choose(self, turn):
        if turn != self.turn:
            return
        c = random.choice((1, 2, 2, 3, 3, 3, 4, 4, 4, 4))
        self.send('setAvatarChoice', [c])
        self.note['turns'] = self.note.get('turns', 0) + 1
