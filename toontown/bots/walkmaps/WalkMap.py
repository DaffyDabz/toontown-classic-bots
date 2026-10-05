"""Baked walk graph for bot toons. Pure Python (3.9), no Panda3D.

Data: toontown/bots/walkmaps/<zone>.json.gz, written by toontown/bots/tools/bake_walkmaps.py.
Node = [i, j, z*10, dirMask, zoneIdx]; world pos = (x0 + i*spacing, y0 + j*spacing, z).
dirMask bits: +x, +x+y, +y, -x+y, -x, -x-y, -y, +x-y. The neighbour in direction d is the node in
cell (i+di, j+dj) whose z is closest to this node's z (the same rule the baker used). Every edge was
proven walkable for a toon (floor under every foot of it, slope <= 1.2, no wall, no water).
Optional data['links'] = [[a, b], ...]: extra proven edges between stacked layers (a ramp over a floor).

    wm = WalkMap.load(2000)
    path = wm.path(wm.randomPoint(), wm.place('trolley')['pos'])
"""
import gzip
import heapq
import json
import math
import os
import random

DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
_DIR = os.path.dirname(os.path.abspath(__file__))
_cache = {}
# smoothNodes() failed the replay check (2000, 2100, 1000: water/fall on merged runs), so paths ship raw.
SMOOTH_OK = False
# sweep fix: walk-map zone -> nodes below this z are under water (the DD harbour bed), cut from the graph
SUBMERGED = {1000: -5.0}


class WalkMap:
    @classmethod
    def load(cls, zoneId):
        zoneId = int(zoneId)
        wm = _cache.get(zoneId)
        if wm is None:
            with gzip.open(os.path.join(_DIR, '%d.json.gz' % zoneId), 'rb') as f:
                wm = cls(json.loads(f.read().decode('utf-8')))
            _cache[zoneId] = wm
        return wm

    @staticmethod
    def available():
        return sorted(int(n.split('.')[0]) for n in os.listdir(_DIR) if n.endswith('.json.gz'))

    def __init__(self, data):
        self.zoneId = data['zone']
        self.hoodId = data['hood']
        self.spacing = float(data['spacing'])
        self.x0 = float(data['x0'])
        self.y0 = float(data['y0'])
        self.zones = list(data['zones'])
        self.stats = data.get('stats', {})
        self.drops = data.get('drops', [])
        self.hazards = data.get('hazards', [])     # e.g. Cashbot HQ train lanes: {'kind', 'y', 'halfWidth', 'zBelow'}
        self._places = data.get('places', [])
        self._byName = {p['name']: p for p in self._places}
        nodes = data['nodes']
        self.nodeCount = len(nodes)
        self._i = [n[0] for n in nodes]
        self._j = [n[1] for n in nodes]
        self._z = [n[2] / 10.0 for n in nodes]
        self._mask = [n[3] for n in nodes]
        self._zi = [n[4] for n in nodes]
        self._cell = {}
        for k, n in enumerate(nodes):
            self._cell.setdefault((n[0], n[1]), []).append(k)
        self._nbr = [self._neighbours(k) for k in range(self.nodeCount)]
        for a, b in data.get('links', ()):      # stacked-layer edges the 8-way mask cannot express (a ramp
            if b not in self._nbr[a]:           # over a lower floor); proven by the baker like any edge
                self._nbr[a].append(b)
            if a not in self._nbr[b]:
                self._nbr[b].append(a)
        self._imin, self._imax = min(self._i, default=0), max(self._i, default=0)
        self._jmin, self._jmax = min(self._j, default=0), max(self._j, default=0)
        # sweep fix: the Donald's Dock bake took the harbour bed (z -12.3 .. -5, under the water) for ground,
        # joined to the docks; bots strolled on the sea floor. Those nodes are cut out of the graph here
        # (no edges, never nearest, never random), so every bot stays on the docks like a real toon.
        floor = SUBMERGED.get(self.zoneId)
        self._dropped = set(k for k in range(self.nodeCount) if floor is not None and self._z[k] < floor)
        if self._dropped:
            for k in self._dropped:
                self._nbr[k] = []
            for k in range(self.nodeCount):
                if self._nbr[k]:
                    self._nbr[k] = [m for m in self._nbr[k] if m not in self._dropped]
            for key in list(self._cell):
                self._cell[key] = [k for k in self._cell[key] if k not in self._dropped]
                if not self._cell[key]:
                    del self._cell[key]

    # ------------------------------------------------------------ nodes
    def pos(self, k):
        return (self.x0 + self._i[k] * self.spacing, self.y0 + self._j[k] * self.spacing, self._z[k])

    def zoneOf(self, k):
        return self.zones[self._zi[k]]

    def neighbours(self, k):
        return list(self._nbr[k])

    def _partner(self, k, d):
        di, dj = DIRS[d]
        lst = self._cell.get((self._i[k] + di, self._j[k] + dj))
        if not lst:
            return None
        z = self._z[k]
        return min(lst, key=lambda m: abs(self._z[m] - z))

    def _neighbours(self, k):
        out = []
        m = self._mask[k]
        for d in range(8):
            if m & (1 << d):
                p = self._partner(k, d)
                if p is not None:
                    out.append(p)
        return out

    def _edge(self, a, b):
        return b in self._nbr[a]

    def randomNode(self, rng=None):
        while True:
            k = (rng or random).randrange(self.nodeCount)
            if k not in self._dropped:
                return k

    def randomPoint(self, rng=None):
        return self.pos(self.randomNode(rng))

    def nearestNode(self, x, y, z=None, maxDist=None):
        """Nearest node by horizontal distance; with z, nodes more than 4 ft off vertically cost extra."""
        sp = self.spacing
        ci = int(round((x - self.x0) / sp))
        cj = int(round((y - self.y0) / sp))
        best, bd = None, float('inf')
        # rings needed to cover the whole grid from (ci, cj)
        span = max(abs(ci - self._imin), abs(ci - self._imax), abs(cj - self._jmin), abs(cj - self._jmax))
        rmax = min(span, int(maxDist / sp) + 1) if maxDist is not None else span
        r = 0
        while r <= rmax:
            for i in range(ci - r, ci + r + 1):
                edge = i in (ci - r, ci + r)
                for j in (range(cj - r, cj + r + 1) if edge else (cj - r, cj + r)):
                    for k in self._cell.get((i, j), ()):
                        px, py, pz = self.pos(k)
                        d = math.hypot(px - x, py - y)
                        if z is not None:
                            dz = abs(pz - z)
                            if dz > 4.0:
                                d += (dz - 4.0) * 4.0
                        if d < bd:
                            best, bd = k, d
            # every cell in ring r+1 is at least (r + 0.5) * spacing away horizontally
            if best is not None and bd <= (r + 0.5) * sp:
                break
            r += 1
        if best is None or (maxDist is not None and bd > maxDist):
            return None
        return best

    def zoneAt(self, x, y):
        """Street visgroup zone under (x, y) (the playground zone for a playground)."""
        k = self.nearestNode(x, y)
        return self.zones[self._zi[k]] if k is not None else None

    # ------------------------------------------------------------ places
    def places(self, kind=None):
        return [p for p in self._places if kind is None or p['kind'] == kind]

    def place(self, name):
        return self._byName.get(name)

    def placePos(self, name):
        """Walkable approach point of a place (its node), or None when unreached."""
        p = self._byName.get(name)
        if p is None or p.get('node', -1) < 0:
            return None
        return self.pos(p['node'])

    # ------------------------------------------------------------ paths
    def pathNodes(self, a, b):
        """A* (octile, 3D length) between node indices; [] when unreachable."""
        if a == b:
            return [a]
        pa = self.pos
        bx, by, bz = pa(b)
        sp = self.spacing
        c2 = math.sqrt(2.0) - 2.0

        def h(k):
            dx = abs(self._i[k] - self._i[b])
            dy = abs(self._j[k] - self._j[b])
            return sp * (dx + dy + c2 * min(dx, dy))

        g = {a: 0.0}
        came = {}
        openq = [(h(a), 0.0, a)]
        closed = set()
        while openq:
            f, gc, u = heapq.heappop(openq)
            if u in closed:
                continue
            if u == b:
                out = [u]
                while u in came:
                    u = came[u]
                    out.append(u)
                out.reverse()
                return out
            closed.add(u)
            ux, uy, uz = pa(u)
            for v in self._nbr[u]:
                if v in closed:
                    continue
                vx, vy, vz = pa(v)
                ng = gc + math.sqrt((vx - ux) ** 2 + (vy - uy) ** 2 + (vz - uz) ** 2)
                if ng < g.get(v, float('inf')):
                    g[v] = ng
                    came[v] = u
                    heapq.heappush(openq, (ng + h(v), ng, v))
        return []

    def path(self, a_xyz, b_xyz, smooth=False):
        """World points from the node nearest a to the node nearest b; [] when unreachable."""
        a = self.nearestNode(*_xyz(a_xyz))
        b = self.nearestNode(*_xyz(b_xyz))
        if a is None or b is None:
            return []
        ns = self.pathNodes(a, b)
        if smooth and SMOOTH_OK and len(ns) > 2:
            ns = self.smoothNodes(ns)
        return [self.pos(k) for k in ns]

    def smoothNodes(self, ns):
        """Drop intermediate nodes inside straight runs (same 8-way step repeated). The smoothed line is
        then exactly the proven edges it replaces. Any-angle shortcuts were tried and rejected: the replay
        check found gaps and pond edges between proven edges."""
        if len(ns) < 3:
            return list(ns)
        out = [ns[0]]
        run = [ns[0]]           # nodes of the current straight run, starting at out[-1]
        step = None
        for u, v in zip(ns, ns[1:]):
            s = (self._i[v] - self._i[u], self._j[v] - self._j[u])
            if step is not None and (s != step or not self._flat(run + [v])):
                out.append(u)
                run = [u]
            step = s
            run.append(v)
        out.append(ns[-1])
        return out

    def _flat(self, run, tol=0.3):
        """Every node of the run lies within tol ft of the straight z line between its ends (so a merged
        segment never cuts under an arched bridge or through a stair)."""
        za, zb, n = self._z[run[0]], self._z[run[-1]], len(run) - 1
        return all(abs(self._z[k] - (za + (zb - za) * q / n)) <= tol for q, k in enumerate(run))


def _xyz(p):
    return (p[0], p[1], p[2] if len(p) > 2 else None)


def load(zoneId):
    return WalkMap.load(zoneId)
