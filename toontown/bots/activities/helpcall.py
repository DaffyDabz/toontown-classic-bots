"""HELP CALLS (the owner, 09-25: "if they're in one [a fight they can't handle], they should say help, and if
anybody says help, then the AI would come running, including other AI. So the AI can talk to each other, and it
should.").

  heard       a toon on a street says "Help!" (SpeedChat 1410), "We are in trouble." (1412), "I need help with the
              Cogs!" (2205) or types a line with the word help in it (setTalk) - a real player (setSC / setTalk
              broadcasts in a bot's view) or a bot (battlebrain calls call() directly: bots never hear each
              other's broadcasts, the views carry only the AI's and real players').
  answer      in a street battle with a free spot: the nearest free, fit bots on that street run over and join
              (StreetBattle mode 'help': toonRequestJoin), a Toon-Up carrier first when someone in it is hurt, up
              to the free spots; the first to go answers aloud ("Bring it on!" / "We can do this!" / "Hurry!").
              A real player out of a battle: the P9 ToonTask help ("I need help with the Cogs!": 1-2 bots come
              and help with his Cogs).
  rate        one answer per battle every CALL_GAP s (a second "Help!" before the runners arrive sends no more).

Numbers: STATS 'help_call_*' in bots-battle.txt, every call noted in bots-picks.log (HELPCALL lines).
"""
import math
import random
import re
import traceback

from direct.showbase.DirectObject import DirectObject

from toontown.bots import battlebrain as bb
from toontown.bots.activities import lifekit as kit

HELP_SC = (1410, 1412, 2205)
TYPED = re.compile(r'\bhelp', re.I)
REACH = 400.0            # ft: bots on the same street this close come running
CALL_GAP = 20.0          # s: one answer per battle
ANSWER_SC = (1406, 1416, 1400)           # "Bring it on!" / "We can do this!" / "Hurry!"


class HelpCalls(DirectObject):
    def __init__(self):
        DirectObject.__init__(self)
        self.director = None
        self.lastCall = {}                # battle doId -> time answered

    def ensure(self, director):
        if self.director is not None:
            return
        self.director = director
        bb.HUB.ensure(director)
        self.accept('botview-field', self.__field)

    # -- heard aloud (real players) ----------------------------------------------------------------------------------
    def __field(self, view, obj, fieldName, args, sender):
        try:
            if obj.className != 'DistributedToon' or obj.doId in self.director.bots:
                return
            if fieldName == 'setSC' and args and args[0] in HELP_SC:
                self.call(obj.doId, view.zoneId, 'said %s' % args[0], view=view, speaker=obj)
            elif fieldName == 'setTalk' and len(args) > 3 and TYPED.search(args[3] or ''):
                self.call(obj.doId, view.zoneId, 'typed help', view=view, speaker=obj)
        except Exception:
            self.director.error('help call heard', traceback.format_exc())

    # -- the call -------------------------------------------------------------------------------------------------
    def battleOf(self, avId, area):
        from toontown.bots.activities.battle import streetBattles
        for o in streetBattles(self.director, area):
            m = o.get('setMembers')
            if m and avId in m[6]:
                return o
        return None

    def call(self, avId, zoneId, why, view=None, speaker=None):
        """avId asked for help in zoneId: send the bots that can come. Returns how many were sent."""
        from toontown.bots.activities.battle import StreetBattle, canFight, fightGags
        d = self.director
        if d is None:
            return 0
        area = d.world.zoneToArea.get(zoneId)
        bb.STATS.count('help_call_heard')
        if area is None or area.kind != 'street':
            return 0
        o = self.battleOf(avId, area)
        if o is None:
            # a real player out of a battle: the P9 ToonTask help (bots come and help with his Cogs)
            if speaker is not None and view is not None and avId not in d.bots:
                from toontown.bots.activities import commands
                commands.COMMANDER.aloud(view, speaker, 2205)
                bb.STATS.count('help_call_task')
            return 0
        now = globalClock.getRealTime()
        if now - self.lastCall.get(o.doId, -1e9) < CALL_GAP:
            return 0
        m = o.get('setMembers')
        toons = list(m[6])
        coming = [b for b in d.bots.values() if b.activity is not None and b.activity.name == 'battle'
                  and getattr(b.activity, 'target', None) is not None and b.activity.target.doId == o.doId
                  and getattr(b.activity, 'phase', '') == 'tojoin']
        free = 4 - len(toons) - len(coming)
        p = o.get('setPosition')
        if free <= 0 or not p:
            return 0
        self.lastCall[o.doId] = now
        cands = []
        for b in d.bots.values():
            if b.avId in toons or b.area is not area or b.state != 'present' or b.travel is not None or b.node is None:
                continue
            if b.activity is not None and (not b.activity.interruptible or b.activity.name in ('battle', 'cogbuilding')):
                continue
            if not canFight(b, 0.5, min(5, fightGags(b))):      # a new toon comes with its cupcake and flower
                continue
            dist = math.hypot(b.pos[0] - p[0], b.pos[1] - p[1])
            if dist < REACH:
                cands.append((dist, b))
        hurt = False
        for t in toons:
            hp, mx = bb.toonHp(d.air, t) if t not in d.bots else bb.botHp(d.bots[t])
            hurt = hurt or (hp is not None and mx and hp < 0.6 * mx)
        # PROGRESSION: toons whose ToonTask wants Cogs come first (the Cog counts for them too, as players help)
        cands.sort(key=lambda c: ((bb.gagCount(c[1], (bb.HEAL,)) == 0) if hurt else 0,
                                  (getattr(c[1], 'goal', None) or {}).get('kind') != 'cogs', c[0]))
        sent = []
        for dist, b in cands[:free]:
            if b.startActivity(StreetBattle(b, mode='help', battle=o)):
                b.nextTick = min(b.nextTick, now)
                sent.append((b.avId, int(dist)))
        if sent:
            kit.say(d.bots[sent[0][0]], random.choice(ANSWER_SC), force=True, answer=False)
        bb.STATS.count('help_call_answered' if sent else 'help_call_nobody')
        bb.STATS.count('help_call_runners', len(sent))
        from toontown.bots import gagplan
        gagplan.log(d, 'HELPCALL %s %s (%s) in battle %s (%d toons): %s' % (
            'bot' if avId in d.bots else 'PLAYER', avId, why, o.doId, len(toons),
            ('sent %s' % sent) if sent else 'nobody free within %d ft' % REACH))
        return len(sent)


HELP = HelpCalls()


def _boot(task):
    import builtins
    sb = getattr(builtins, 'simbase', None)
    d = getattr(getattr(sb, 'air', None), 'botDirector', None)
    if d is None:
        return task.again
    HELP.ensure(d)
    return task.done


taskMgr.doMethodLater(1.0, _boot, 'bots-helpcall-boot')
