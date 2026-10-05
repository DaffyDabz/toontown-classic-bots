"""How a bot comes and goes (TTBOTS P2, recon s4): the way a real toon does, whenever a real
player could see it.

  leave an area                          arrive in an area
  -------------                          -----------------
  silent   (no real player there)        silent   (no real player there)
  tunnel   walk into the tunnel mouth    tunnel   walk out of the matching tunnel mouth
  door     walk to a building door,      door     come out of a building door (requestExit,
           requestEnter like a client             every client plays the door's walk-out)
  teleport the TeleportOut hole (3.4 s)  teleport generated in TeleportIn with a fresh
                                                  timestamp: every client plays the hole-in

Between the two a bot hops through the Quiet Zone (zone 1), as a client's toon does, so nobody
ever sees it jump. Logging in = activate in the Quiet Zone (or silently in an empty zone);
logging out = leave the same way, then the toon is deleted from the State Server.
"""
import random

from direct.directnotify import DirectNotifyGlobal
from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED

QUIET = OTPGlobals.QuietZone
TELEPORT_OUT_TIME = 3.5          # Toon.getTeleportOutTrack is 3.4 s
TELEPORT_IN_TIME = 1.8           # Toon.getTeleportInTrack: hole + jump
DOOR_IN_TIME = 1.6               # DistributedDoor.avatarEnter walk-in on the clients
DOOR_OUT_TIME = 2.0
WALK_TIMEOUT = 90.0              # a leave walk that takes longer than this gives up and teleports


class BotTravel:
    notify = DirectNotifyGlobal.directNotify.newCategory('BotTravel')
    name = 'travel'

    def __init__(self, director, bot, dest, logout=False, why='', via=None):
        self.via = via                    # 'tunnel': an activity walked the bot to the tunnel into dest (P4/P7)
        self.director = director
        self.bot = bot
        self.src = bot.area
        self.dest = dest                  # Area, or None when logging out
        self.logout = logout
        self.why = why
        self.phase = None
        self.until = 0.0
        self.leaveBy = None
        self.arriveBy = None
        self.door = None                  # (place, doId) while going through a door
        self.tunnelPlace = None
        self.doorReply = None

    def onDirect(self, fieldName, args):
        if fieldName == 'setOtherZoneIdAndDoId':
            self.doorReply = 'ok'
        elif fieldName == 'rejectEnter':
            self.doorReply = 'reject'

    # ---- plan -----------------------------------------------------------------------
    def begin(self):
        """Called by the director. Picks how to leave, starts it."""
        bot, d = self.bot, self.director
        if bot.state == 'offline':
            return self.__login()
        bot.endActivity('travel')
        watched = d.watched(self.src)
        if self.via == 'tunnel' and self.dest is not None and self.dest.id in self.src.tunnels:
            # through the tunnel it stands at, out of the matching mouth on the other side
            self.tunnelPlace, self.tunnelTo = self.src.tunnels[self.dest.id], self.dest
            self.arriveBy = 'tunnel'
            self.leaveBy = 'tunnel' if watched else 'silent'
        elif self.via == 'teleport' and watched:
            self.leaveBy = 'teleport'         # P8: a toon that went sad teleports to its playground
        elif not watched:
            self.leaveBy = 'silent'
        else:
            self.leaveBy = self.__pickLeave()
        getattr(self, '_leave_' + self.leaveBy)()
        return True

    def __pickLeave(self):
        d, src = self.director, self.src
        options = []
        # a tunnel into the destination, or into an empty neighbour we can go on from unseen
        for nbId, place in src.tunnels.items():
            nb = d.world.areas[nbId]
            if self.dest is not None and nb is self.dest:
                options += [('tunnel', place, nb)] * 4
            elif not d.watched(nb) and d.canWalkTo(self.bot, place):
                options.append(('tunnel', place, nb))
        options = [o for o in options if d.canWalkTo(self.bot, o[1])]
        choice = random.random()
        if options and choice < 0.55:
            _, place, nb = random.choice(options)
            self.tunnelPlace, self.tunnelTo = place, nb
            return 'tunnel'
        if choice < 0.75:
            door = d.pickDoor(src, near=self.bot.pos)
            if door is not None:
                self.door = door
                return 'door'
        return 'teleport'

    # ---- leaving --------------------------------------------------------------------
    def _leave_silent(self):
        self.director.count('out', 'silent')
        self.__arrive()

    def _leave_teleport(self):
        bot = self.bot
        bot.path = []
        bot.setAnim('TeleportOut', force=True)
        self.phase, self.until = 'tpout', self.__now() + TELEPORT_OUT_TIME

    def _leave_tunnel(self):
        p = self.tunnelPlace
        x, y, z = p['pos']
        if not self.bot.walkToPos(x, y, z, RUN_SPEED, 'run'):
            self.leaveBy = 'teleport'
            return self._leave_teleport()
        self.phase, self.until = 'walkout', self.__now() + WALK_TIMEOUT

    def _leave_door(self):
        place, doId = self.door
        if not self.bot.walkTo(self.src.placeNode(place), RUN_SPEED, 'run'):
            self.leaveBy = 'teleport'
            return self._leave_teleport()
        self.phase, self.until = 'walkdoor', self.__now() + WALK_TIMEOUT

    # ---- the steps ------------------------------------------------------------------------
    @staticmethod
    def __now():
        return globalClock.getRealTime()

    def step(self, now):
        bot = self.bot
        ph = self.phase
        if ph == 'walkout':
            if not bot.path:
                self.director.count('out', 'tunnel')
                if self.dest is not None and self.tunnelTo is self.dest:
                    self.arriveBy = 'tunnel'
                self.__arrive()
            elif now > self.until:
                bot.path = []
                self.leaveBy = 'teleport'
                self._leave_teleport()
        elif ph == 'walkdoor':
            if not bot.path:
                place, doId = self.door
                bot.faceTo(place['pos'])
                bot.setAnim('neutral')
                bot.send('requestEnter', [], doId=doId, className='DistributedDoor')
                self.phase, self.until = 'doorwait', now + 3.0
            elif now > self.until:
                bot.path = []
                self._leave_teleport()
        elif ph == 'doorwait':
            if self.doorReply == 'ok':
                x, y, z = self.door[0]['pos']
                bot.path = [(x, y, z, None)]          # a step into the doorway while it opens
                bot.speed = WALK_SPEED
                bot.setAnim('walk')
                self.phase, self.until = 'doorin', now + DOOR_IN_TIME
            elif self.doorReply == 'reject' or now > self.until:
                self._leave_teleport()
        elif ph == 'doorin':
            if now >= self.until:
                self.director.count('out', 'door')
                self.__arrive()
        elif ph == 'tpout':
            if now >= self.until:
                self.director.count('out', 'teleport')
                self.__arrive()
        elif ph == 'tpin':
            if now >= self.until:
                bot.setAnim('neutral')
                return self.__done()
        elif ph == 'walkin':
            if not bot.path:
                return self.__done()
            if now > self.until:
                bot.path = []
                return self.__done()
        elif ph == 'doorout':
            if now >= self.until:
                # the clients' door walk-out track leaves the toon in its walk cycle: reset it
                bot.setAnim('neutral', force=True)
                return self.__done()
        elif ph == 'activating':
            if now > self.until:        # the toon never showed up: try again later
                load = getattr(self.director, 'load', None)
                if load is not None:    # P10b: delete a stale live toon, back off, one warning per streak
                    load.loginFailed(bot, now)
                else:
                    self.director.error('activate', '%s never appeared in zone %s' % (bot.avId, bot.zoneId))
                bot.gone('activation timeout')
                return False
        elif ph == 'done':
            return False
        return True

    def __done(self):
        self.phase = 'done'
        self.bot.arrivedAt = self.__now()
        self.director.arrived(self.bot)
        return False

    # ---- arriving -------------------------------------------------------------------
    def __arrive(self):
        bot, d = self.bot, self.director
        bot.path = []
        if self.dest is None:          # logging out
            d.count('out', 'logout')
            bot.logout(self.why)
            self.phase = 'done'
            return
        watched = d.watched(self.dest)
        if self.arriveBy is None:
            if not watched:
                self.arriveBy = 'silent'
            else:
                self.arriveBy = 'door' if random.random() < 0.25 else 'teleport'
        getattr(self, '_arrive_' + self.arriveBy)()

    def _arrive_silent(self):
        a, bot = self.dest, self.bot
        node = self.director.spawnNode(a, bot)
        bot.area = a
        bot.node = node
        pos = a.wm.pos(node)
        bot.relocate(a.zoneOfNode(node), pos=pos, h=random.uniform(-180, 180), anim='neutral', area=a)
        self.director.count('in', 'silent')
        self.__done()

    def _arrive_teleport(self):
        a, bot = self.dest, self.bot
        node = self.director.spawnNode(a, bot)
        bot.relocate(QUIET)
        bot.area = a
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        bot.h = random.uniform(-180, 180)
        bot.broadcastNow()
        bot.setAnim('TeleportIn', force=True)
        bot.relocate(a.zoneOfNode(node), area=a)
        self.director.count('in', 'teleport')
        self.phase, self.until = 'tpin', self.__now() + TELEPORT_IN_TIME

    def _arrive_tunnel(self):
        a, bot = self.dest, self.bot
        place = a.tunnels.get(self.src.id)
        if place is None:
            return self._arrive_teleport()
        node = a.placeNode(place)
        x, y, z = place['pos']
        bot.relocate(QUIET)
        bot.area = a
        bot.node = node
        bot.pos = Point3(x, y, z)
        bot.faceTo(a.wm.pos(node))
        bot.broadcastNow()
        bot.setAnim('run', force=True)
        bot.relocate(self.director.world.tunnelZone(a, place), area=a)
        # out of the mouth onto the map, then a few steps on
        bot.path = [a.wm.pos(node) + (node,)]
        bot.speed = RUN_SPEED
        on = a.nodeNear(*a.wm.pos(node)[:2], radius=25.0)
        if on is not None:
            nodes = a.wm.pathNodes(node, on)
            bot.path += [a.wm.pos(k) + (k,) for k in nodes[1:]]
        bot.moving = True
        self.director.count('in', 'tunnel')
        self.phase, self.until = 'walkin', self.__now() + 30.0

    def _arrive_door(self):
        a, bot, d = self.dest, self.bot, self.director
        door = d.pickDoor(a)
        if door is None:
            return self._arrive_teleport()
        place, doId = door
        node = a.placeNode(place)
        bot.relocate(QUIET)
        bot.area = a
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        x, y, z = place['pos']
        bot.faceTo((2 * bot.pos[0] - x, 2 * bot.pos[1] - y))     # back to the door
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(d.world.doorZone(a, place), area=a)
        bot.send('requestExit', [], doId=doId, className='DistributedDoor')
        d.count('in', 'door')
        self.phase, self.until = 'doorout', self.__now() + DOOR_OUT_TIME

    # ---- logging in -----------------------------------------------------------------
    def __login(self):
        bot, d, a = self.bot, self.director, self.dest
        if not d.watched(a):
            node = d.spawnNode(a, bot)
            zone = a.zoneOfNode(node)

            def seen():
                bot.area = a
                bot.node = node
                bot.pos = Point3(*a.wm.pos(node))
                bot.h = random.uniform(-180, 180)
                d.setArea(bot, a)
                bot.broadcastNow()
                bot.setAnim('neutral', force=True)
                d.count('in', 'login-silent')
                self.__done()
            bot.activate(zone, seen)
        else:
            def seen():
                self.arriveBy = 'door' if random.random() < 0.2 else 'teleport'
                d.count('in', 'login')
                getattr(self, '_arrive_' + self.arriveBy)()
            bot.activate(QUIET, seen)
        self.phase, self.until = 'activating', self.__now() + 30.0
        return True
