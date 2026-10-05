"""P10: battle broadcast delivery, measured per bot per round (the shared-view check).

Every battle broadcast the bot process receives (DistributedBattle*, via the zone views) is counted for
each bot whose battle brain is registered on that battle, in the battle's current round (a round starts
at each setState WaitForInput). When the battle leaves the view, one line goes to the log and one JSON
line to <run>/bots-delivery.jsonl:
    rounds, and per bot the events it got in each round it was a member of the battle at that round's start;
    'deaf' = (bot, round) pairs with no event at all (a bot that stopped hearing its battle).
Costs one dict update per battle broadcast.
"""
import json
import os

from direct.directnotify import DirectNotifyGlobal
from direct.showbase.DirectObject import DirectObject

from toontown.bots import battlebrain as bb


class BattleDelivery(DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('BattleDelivery')

    def __init__(self, runDir):
        DirectObject.__init__(self)
        self.path = os.path.join(runDir, 'bots-delivery.jsonl')
        self.recs = {}
        self.totals = {'battles': 0, 'rounds': 0, 'botRounds': 0, 'deaf': 0}
        self.accept('botview-field', self.__field)
        self.accept('botview-exit', self.__exit)

    def __field(self, view, obj, fieldName, args, sender):
        if not bb.isBattle(obj):
            return
        r = self.recs.get(obj.doId)
        if r is None:
            r = self.recs[obj.doId] = {'cls': obj.className, 'zone': view.zoneId, 'round': 0, 'members': {},
                                       'ev': {}, 'players': set()}
        if fieldName == 'setState' and args and args[0] == 'WaitForInput':
            r['round'] += 1
            m = obj.get('setMembers')
            toons = list(m[6]) if m else []
            d = bb.HUB.director
            r['members'][r['round']] = [t for t in toons if d is not None and t in d.bots]
            r['players'].update(t for t in toons if d is not None and t not in d.bots)
        for av in bb.HUB.brains.get(obj.doId, {}):
            e = r['ev'].setdefault(av, {})
            e[r['round']] = e.get(r['round'], 0) + 1

    def __exit(self, view, obj, deleted=False):
        r = self.recs.pop(obj.doId, None)
        if r is None or r['round'] < 1:
            return
        per, deaf, botRounds = {}, [], 0
        for rnd, bots in r['members'].items():
            for av in bots:
                n = r['ev'].get(av, {}).get(rnd, 0)
                per.setdefault(av, []).append(n)
                botRounds += 1
                if n == 0:
                    deaf.append((av, rnd))
        t = self.totals
        t['battles'] += 1
        t['rounds'] += r['round']
        t['botRounds'] += botRounds
        t['deaf'] += len(deaf)
        rec = {'battle': obj.doId, 'cls': r['cls'], 'zone': r['zone'], 'rounds': r['round'],
               'players': sorted(r['players']), 'perBotPerRound': {str(k): v for k, v in per.items()},
               'deaf': deaf}
        self.notify.info('[TTBOTS] battle-delivery %s %s zone %s: %d rounds, %d bots, deaf %d, events %s' % (
            obj.doId, r['cls'], r['zone'], r['round'], len(per), len(deaf),
            ' '.join('%s:%s' % (k, v) for k, v in per.items())))
        try:
            with open(self.path, 'a') as f:
                f.write(json.dumps(rec) + '\n')
        except OSError:
            pass
