"""A bot's VIEW of one zone, built the way Astron's client agent builds a real
client's interest, but inside the bot AI process.

How the bot process learns what is in a zone
--------------------------------------------
1. It subscribes (CONTROL_ADD_CHANNEL) to the zone's LOCATION CHANNEL,
   (parentId << 32) | zoneId. The State Server publishes on that channel every
   broadcast field update of every object in the zone (setSmPosHpr, setSC,
   setAnimState, fillSlotN ...), every ENTER_LOCATION of an object arriving,
   every CHANGING_LOCATION of one leaving and every DELETE_RAM.
2. For what was there BEFORE it subscribed, it sends
   STATESERVER_OBJECT_GET_ZONES_OBJECTS(context, parentId, [zoneId]) to the
   parent (the district). The parent answers GET_ZONES_COUNT_RESP(context, n)
   and each object in the zone answers with an "enter interest" message
   (context + the same body as ENTER_LOCATION_WITH_REQUIRED[_OTHER]).
3. Every entry is unpacked with the .dc (required fields, plus the ram "other"
   fields) into a ViewObject: doId, class name, the last value of every field,
   and a decoded position/heading for smooth nodes. Updates keep it current.

Nothing here is bot specific or zone specific: open a view on any
(parent, zone) and it holds every toon, trolley, fishing spot, door, NPC
in it. Views are SHARED: BotAIRepository.openView/closeView refcount them,
one per zone for the whole process. Listeners get messenger events:
    'botview-field', [view, obj, fieldName, args, senderChannel]  (not for positions:
                     read obj.pos; not for our own bots' broadcasts: dropped early)
    'botview-enter', [view, obj]      'botview-exit', [view, obj, deleted]
"""
from direct.directnotify import DirectNotifyGlobal
from direct.distributed.MsgTypes import *
from direct.distributed.PyDatagram import PyDatagram
from panda3d.core import Point3
from panda3d.direct import DCPacker

# component letters each smooth/node position field carries, in order
POS_FIELDS = {
    'setX': 'x', 'setY': 'y', 'setZ': 'z', 'setH': 'h', 'setP': 'p', 'setR': 'r',
    'setPos': 'xyz', 'setHpr': 'hpr', 'setPosHpr': 'xyzhpr', 'setXY': 'xy', 'setXZ': 'xz',
    'setXYH': 'xyh', 'setXYZH': 'xyzh',
    'setComponentX': 'x', 'setComponentY': 'y', 'setComponentZ': 'z', 'setComponentH': 'h',
    'setComponentP': 'p', 'setComponentR': 'r', 'setComponentL': 'l', 'setComponentT': 't',
    'setSmStop': 't', 'setSmH': 'ht', 'setSmZ': 'zt', 'setSmXY': 'xyt', 'setSmXZ': 'xzt',
    'setSmPos': 'xyzt', 'setSmHpr': 'hprt', 'setSmXYH': 'xyht', 'setSmXYZH': 'xyzht',
    'setSmPosHpr': 'xyzhprt', 'setSmPosHprL': 'lxyzhprt',
}


def flatten(v):
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            out.extend(flatten(x))
        return out
    return [v]


def locationChannel(parentId, zoneId):
    return (parentId << 32) | zoneId


class ViewObject:
    __slots__ = ('doId', 'dclass', 'parentId', 'zoneId', 'fields', 'pos', 'h')

    def __init__(self, doId, dclass, parentId, zoneId):
        self.doId = doId
        self.dclass = dclass
        self.parentId = parentId
        self.zoneId = zoneId
        self.fields = {}
        self.pos = Point3(0, 0, 0)
        self.h = 0.0

    @property
    def className(self):
        return self.dclass.getName()

    def get(self, fieldName, default=None):
        return self.fields.get(fieldName, default)

    def apply(self, fieldName, value):
        self.fields[fieldName] = value
        letters = POS_FIELDS.get(fieldName)
        if letters:
            vals = flatten(value)
            for c, v in zip(letters, vals):
                if c == 'x':
                    self.pos[0] = v
                elif c == 'y':
                    self.pos[1] = v
                elif c == 'z':
                    self.pos[2] = v
                elif c == 'h':
                    self.h = v


class BotZoneView:
    notify = DirectNotifyGlobal.directNotify.newCategory('BotZoneView')

    def __init__(self, air, parentId, zoneId):
        self.air = air
        self.parentId = parentId
        self.zoneId = zoneId
        self.channel = locationChannel(parentId, zoneId)
        self.objects = {}
        self.context = None
        self.expected = None      # GET_ZONES_COUNT_RESP count
        self.users = 0
        self.stats = {}           # field updates seen, by name (debug)

    def open(self):
        self.air.registerForChannel(self.channel)
        self.requery()

    def requery(self):
        """(Re)send GET_ZONES_OBJECTS with a fresh context; answers to an older context still count
        (P10b: the district query is repeated until it answers)."""
        self.context = self.air.getContext()
        self.air.viewContexts[self.context] = self
        dg = PyDatagram()
        dg.addServerHeader(self.parentId, self.air.ourChannel, STATESERVER_OBJECT_GET_ZONES_OBJECTS)
        dg.addUint32(self.context)
        dg.addUint32(self.parentId)
        dg.addUint16(1)
        dg.addUint32(self.zoneId)
        self.air.send(dg)

    def close(self):
        self.air.unregisterForChannel(self.channel)
        self.air.viewContexts.pop(self.context, None)
        for doId in self.objects:
            if self.air.doView.get(doId) is self:
                del self.air.doView[doId]
        self.objects.clear()

    # ---- queries ----------------------------------------------------------
    def ofClass(self, className):
        return [o for o in self.objects.values() if o.className == className]

    def first(self, className):
        objs = self.ofClass(className)
        return objs[0] if objs else None

    def summary(self):
        counts = {}
        for o in self.objects.values():
            counts[o.className] = counts.get(o.className, 0) + 1
        return counts

    # ---- datagram input (called by BotAIRepository) ------------------------
    def enter(self, doId, parentId, zoneId, dclass, fields):
        obj = self.objects.get(doId)
        fresh = obj is None
        if fresh:
            obj = ViewObject(doId, dclass, parentId, zoneId)
            self.objects[doId] = obj
            self.air.doView[doId] = self
        for name, value in fields:
            obj.apply(name, value)
        if fresh:
            messenger.send('botview-enter', [self, obj])

    def field(self, doId, fieldName, value, sender):
        obj = self.objects.get(doId)
        key = fieldName if obj is not None else 'unknown:' + fieldName
        self.stats[key] = self.stats.get(key, 0) + 1
        if obj is None:
            return
        obj.apply(fieldName, value)
        if fieldName not in POS_FIELDS:      # positions are read from obj.pos, never announced
            messenger.send('botview-field', [self, obj, fieldName, value, sender])
        elif POS_FIELDS[fieldName][-1] == 't':
            hook = getattr(self.air, 'stampHook', None)     # P10: a peer's clock, seen in its timestamps
            if hook is not None:
                hook(self, obj, flatten(value)[-1])

    def exit(self, doId, deleted=False):
        obj = self.objects.pop(doId, None)
        if obj is not None:
            if self.air.doView.get(doId) is self:
                del self.air.doView[doId]
            messenger.send('botview-exit', [self, obj, deleted])


def sentRequired(f, owner):
    """Astron's append_required_data(client_only=True[, also_owner]): an entry carries
    only the required fields a client may see (broadcast/clrecv, plus ownrecv for the
    owner). Reading every required field misaligned a toon right after setName (P0)."""
    return f.isBroadcast() or f.isClrecv() or (owner and f.isOwnrecv())


def unpackEntry(air, di, other, owner=False):
    """Unpack the ENTER_LOCATION/OWNER body: doId, parent, zone, dclass, required [, other]."""
    doId = di.getUint32()
    parentId = di.getUint32()
    zoneId = di.getUint32()
    classId = di.getUint16()
    dclass = air.dclassesByNumber.get(classId)
    if dclass is None:
        return doId, parentId, zoneId, None, []
    packer = DCPacker()
    packer.setUnpackData(di.getRemainingBytes())
    fields = []
    for i in range(dclass.getNumInheritedFields()):
        f = dclass.getInheritedField(i)
        if f.asMolecularField() is None and f.isRequired() and sentRequired(f, owner):
            packer.beginUnpack(f)
            v = packer.unpackObject()
            if not packer.endUnpack():
                return doId, parentId, zoneId, dclass, fields
            fields.append((f.getName(), v))
    if other:
        for _ in range(packer.rawUnpackUint16()):
            f = dclass.getFieldByIndex(packer.rawUnpackUint16())
            if f is None:
                break
            packer.beginUnpack(f)
            v = packer.unpackObject()
            if not packer.endUnpack():
                break
            fields.append((f.getName(), v))
    return doId, parentId, zoneId, dclass, fields


def unpackField(dclass, di):
    fieldId = di.getUint16()
    f = dclass.getFieldByIndex(fieldId)
    if f is None:
        return None, None
    packer = DCPacker()
    packer.setUnpackData(di.getRemainingBytes())
    packer.beginUnpack(f)
    v = packer.unpackObject()
    packer.endUnpack()
    return f.getName(), v
