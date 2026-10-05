"""The map the director plans on (TTBOTS P2): AREAS, their location zones, walk maps,
tunnels between them and the door places in them. Pure data + the P3 walk maps.

An AREA is what the director counts and targets:
  - a playground (2000 TTC, ..., 6000 Acorn Acres, 8000 Speedway, 17000 Golf), one location zone;
  - a street BRANCH (2100 Silly Street, ...): one walk map, many location zones (the street's
    visgroups 2101..2156). A toon on a street sits in the visgroup under its feet, exactly as a
    client's toon does, so BotToon changes zone as it walks from one visgroup into the next.
Cog HQs (10000+) are left out on purpose: bots go there with the battle brain (P8).
"""
import random

from toontown.bots.walkmaps.WalkMap import WalkMap

TTC, DD, DG, MML, BR, DDL = 2000, 1000, 5000, 4000, 3000, 9000
OZ, GS, GZ = 6000, 8000, 17000
HOODS = (TTC, DD, DG, MML, BR, DDL)            # level order: a fresh toon in TTC ... maxed in Dreamland
SHARED = (OZ, GS, GZ)                          # anyone goes there
STREETS = {TTC: (2100, 2200, 2300), DD: (1100, 1200, 1300), DG: (5100, 5200, 5300),
           MML: (4100, 4200, 4300), BR: (3100, 3200, 3300), DDL: (9100, 9200)}
NAMES = {2000: 'Toontown Central', 1000: 'Donald\'s Dock', 5000: 'Daisy Gardens', 4000: 'Minnie\'s Melodyland',
         3000: 'The Brrrgh', 9000: 'Donald\'s Dreamland', 6000: 'Acorn Acres', 8000: 'Goofy Speedway',
         17000: 'Chip \'n Dale\'s MiniGolf',
         2100: 'Silly St', 2200: 'Loopy Ln', 2300: 'Punchline Pl', 1100: 'Barnacle Blvd', 1200: 'Seaweed St',
         1300: 'Lighthouse Ln', 5100: 'Elm St', 5200: 'Maple St', 5300: 'Oak St', 4100: 'Alto Ave',
         4200: 'Baritone Blvd', 4300: 'Tenor Terr', 3100: 'Walrus Way', 3200: 'Sleet St', 3300: 'Polar Pl',
         9100: 'Lullaby Ln', 9200: 'Pajama Pl',
         10000: 'Bossbot HQ', 10100: 'Bossbot Lobby', 11000: 'Sellbot HQ', 11100: 'Sellbot Lobby',
         11200: 'Factory Exterior', 12000: 'Cashbot HQ', 12100: 'Cashbot Lobby', 13000: 'Lawbot HQ',
         13100: 'Lawbot Lobby', 13200: 'DA Office Lobby'}
# P8b: Cog HQ courtyards and lobbies (kind 'coghq', hood = the HQ courtyard); who may go there is
# decided by ELIGIBLE['coghq'] (toontown/bots/coghq/suits.py via activities/coghq.py)
COGHQ = (10000, 10100, 11000, 11100, 11200, 12000, 12100, 13000, 13100, 13200)
ELIGIBLE = {}           # area kind -> fn(bot, area) -> bool (the director asks it before the hood rule)
AREA_TARGETS = {}       # area id -> (min, max) bots, over the kind's default (P8b: 8 at each boss elevator)


class Area:
    def __init__(self, areaId, kind, hood):
        self.id = areaId
        self.kind = kind                 # 'playground' | 'street'
        self.hood = hood                 # home hood (a SHARED playground is its own hood)
        self.name = NAMES.get(areaId, str(areaId))
        self.zones = []                  # location zones a toon can stand in
        self.min = self.max = self.target = 0
        self.bots = set()                # BotToons counted here (present, arriving)
        self.players = set()             # real players (client toons) in any of self.zones
        self.tunnels = {}                # neighbour area id -> tunnel place (in THIS area's map)
        self.doors = []                  # building door places (block, doorIndex, zone)
        self._wm = None
        self._comp = None
        self._main = None

    def __repr__(self):
        return '<Area %s %s>' % (self.id, self.name)

    @property
    def wm(self):
        if self._wm is None:
            self._wm = WalkMap.load(self.id)
            self.__components()
        return self._wm

    # ---- walk map helpers ------------------------------------------------------
    def __components(self):
        """Label connected components once; spawn and stroll only on the biggest one (the ground),
        never on a lone roof or a ledge the baker proved but no toon walks to."""
        wm = self._wm
        comp = [-1] * wm.nodeCount
        sizes = []
        for s in range(wm.nodeCount):
            if comp[s] >= 0:
                continue
            c = len(sizes)
            stack, n = [s], 0
            comp[s] = c
            while stack:
                u = stack.pop()
                n += 1
                for v in wm._nbr[u]:
                    if comp[v] < 0:
                        comp[v] = c
                        stack.append(v)
            sizes.append(n)
        self._comp = comp
        big = max(range(len(sizes)), key=lambda c: sizes[c]) if sizes else 0
        self._main = [k for k in range(wm.nodeCount) if comp[k] == big]
        self.mainComp = big

    def onGround(self, node):
        self.wm
        return node is not None and self._comp[node] == self.mainComp

    def randomNode(self, rng=random):
        self.wm
        return rng.choice(self._main)

    def nodeNear(self, x, y, radius, rng=random, tries=8):
        """A ground node about radius ft from (x, y)."""
        wm = self.wm
        import math
        for _ in range(tries):
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(radius * 0.4, radius)
            k = wm.nearestNode(x + r * math.cos(a), y + r * math.sin(a), maxDist=6.0)
            if k is not None and self.onGround(k):
                return k
        return None

    def zoneOfNode(self, node):
        return self.wm.zoneOf(node) if self.kind == 'street' else self.id

    def placeNode(self, place):
        k = place.get('node', -1)
        return k if k is not None and k >= 0 else None


class World:
    def __init__(self):
        self.areas = {}
        self.zoneToArea = {}
        for hood in HOODS:
            self.__add(hood, 'playground', hood)
            for st in STREETS[hood]:
                self.__add(st, 'street', hood)
        for pg in SHARED:
            self.__add(pg, 'playground', pg)
        for hq in COGHQ:
            self.__add(hq, 'coghq', hq - hq % 1000)
        for a in self.areas.values():
            self.__links(a)

    def __add(self, areaId, kind, hood):
        a = Area(areaId, kind, hood)
        a.zones = list(a.wm.zones) if kind == 'street' else [areaId]
        self.areas[areaId] = a
        for z in a.zones:
            self.zoneToArea[z] = a

    def __links(self, a):
        for p in a.wm.places('tunnel'):
            to = p['extra'].get('toZone')
            if to in self.areas and a.placeNode(p) is not None:
                a.tunnels[to] = p
        for p in a.wm.places():
            if p['kind'] in ('door', 'hq', 'gagshop', 'clothes', 'petshop', 'bank', 'library', 'school',
                             'toonhall', 'kartshop') and 'block' in p.get('extra', {}) \
                    and a.placeNode(p) is not None:
                a.doors.append(p)

    def area(self, areaOrZone):
        return self.areas.get(areaOrZone) or self.zoneToArea.get(areaOrZone)

    def neighbours(self, a):
        return [self.areas[i] for i in a.tunnels]

    def tunnelZone(self, a, place):
        """Location zone of a tunnel mouth in area a (street: its visgroup)."""
        if a.kind == 'street':
            return place['extra'].get('visZone') or a.zoneOfNode(a.placeNode(place))
        return a.id

    def doorZone(self, a, place):
        if a.kind == 'street':
            return place['extra'].get('visZone') or a.zoneOfNode(a.placeNode(place))
        return a.id
