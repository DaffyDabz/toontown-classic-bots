"""'Follow me!' (SpeedChat 1006) from a real player: the nearest free bot follows him in his
footsteps (the P0/P1 behaviour of Lucky, now any bot) until he says "Wait here." (1010),
"Stay." (21002) or "Bye!" (200), or leaves the zone; then it walks its own footsteps back
onto the walk map. P9 (commands) builds on this.
"""
from panda3d.core import Point3

from toontown.bots.activities import Activity, register
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED, gaitFor

SC_OK = 3
SC_FOLLOW_ME = 1006
SC_STOP = (1010, 21002, 200)
FOLLOW_GAP = 6.0
HEAR_RANGE = 60.0


@register
class Follow(Activity):
    name = 'follow'
    weight = 0            # never picked at random: only started by trigger()
    interruptible = False
    fast = True           # tick every broadcast period, even standing (to react to the leader)

    @classmethod
    def trigger(cls, bot, speaker, msgId):
        # P9: "Follow me!" is a player command now (activities/commands.py: follows everywhere, not just
        # this zone); this in-zone follower stays for reference and is only started when commands is missing
        try:
            from toontown.bots.activities import commands  # noqa: F401
            return None
        except Exception:
            pass
        if msgId == SC_FOLLOW_ME and (speaker.pos - bot.pos).length() <= HEAR_RANGE:
            return cls(bot, speaker.doId)
        return None

    def __init__(self, bot, leaderId=None):
        Activity.__init__(self, bot)
        self.leader = leaderId
        self.trail = []
        self.walked = []
        self.back = False

    def leaderObj(self):
        v = self.bot.view
        return v.objects.get(self.leader) if v is not None else None

    def start(self):
        leader = self.leaderObj()
        if leader is None:
            return False
        bot = self.bot
        bot.path = []
        self.trail = [Point3(leader.pos)]
        self.walked = [Point3(bot.pos)]
        bot.node = None
        bot.faceTo(leader.pos)
        bot.setAnim('neutral')
        bot.say(SC_OK)
        self.notify('follows %s' % self.leader)
        return True

    def notify(self, text):
        self.bot.notify.info('[TTBOTS] %s %s' % (self.bot.avId, text))

    def onHeard(self, speaker, msgId):
        if msgId in SC_STOP and speaker.doId == self.leader and not self.back:
            self.bot.say(SC_OK)
            self.__goBack()
            return True
        return msgId == SC_FOLLOW_ME       # already following someone

    def __goBack(self):
        self.back = True
        bot = self.bot
        bot.path = [(p[0], p[1], p[2], None) for p in reversed(self.walked)]
        bot.speed = RUN_SPEED
        bot.setAnim('run')

    def step(self, now):
        bot = self.bot
        if self.back:
            if bot.path:
                return True
            wm = bot.area.wm
            bot.node = wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
            bot.setAnim('neutral')
            return False
        leader = self.leaderObj()
        if leader is None:
            self.notify('lost its leader, walking back')
            self.__goBack()
            return True
        pos = Point3(leader.pos)
        if (pos - self.trail[-1]).length() > 1.0:
            self.trail.append(pos)
        self.__prune(leader)
        gap = (leader.pos - bot.pos).length()
        speed, gait = gaitFor(gap - FOLLOW_GAP, bot.anim)     # owner 09-25: run to keep up, no sliding feet
        if not speed or (bot.anim == 'neutral' and gap <= FOLLOW_GAP + 1.5):
            bot.path = []
            bot.setAnim('neutral')
            bot.faceTo(leader.pos)
            while len(self.trail) > 1 and (self.trail[0] - bot.pos).length() < FOLLOW_GAP:
                self.trail.pop(0)
            return True
        pts = list(self.trail)
        if (pts[-1] - leader.pos).length() < FOLLOW_GAP:
            pts = pts[:-1] or [pts[-1]]
        bot.path = [(p[0], p[1], p[2], None) for p in pts]
        bot.speed = speed
        bot.setAnim(gait)
        last = self.walked[-1]
        if (bot.pos - last).length() > 2.0:
            self.walked.append(Point3(bot.pos))
        return True

    def __prune(self, leader):
        bot = self.bot
        for i in range(len(self.trail) - 1, 0, -1):
            if (self.trail[i] - bot.pos).length() < FOLLOW_GAP:
                del self.trail[:i]
                break
        gap = (leader.pos - bot.pos).length()
        if len(self.trail) > 1 and gap < FOLLOW_GAP * 3:
            walk, prev = 0.0, bot.pos
            for p in self.trail:
                walk += (p - prev).length()
                prev = p
            if walk > gap * 2 + FOLLOW_GAP:
                self.trail = [Point3(leader.pos)]

    def stop(self, why):
        bot = self.bot
        if bot.node is None and bot.area is not None:
            bot.node = bot.area.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
