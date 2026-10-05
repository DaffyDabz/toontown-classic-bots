"""Headless Toontown world loader + batched collision queries (bake/check tools only).

Loads a playground or street exactly the way the client does (SafeZoneLoader /
TownLoader: global storage, hood storage, sz/town storage, then the zone DNA),
keeps only the collision solids the local toon collides with (WallBitmask 1,
FloorBitmask 2) and answers batched ray / sphere / capsule / segment queries.

Run under ppython from the repo root. Never imported by the bot process.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from panda3d.core import (loadPrcFile, loadPrcFileData, NodePath, PandaNode, BitMask32,
                          CollisionTraverser, CollisionHandlerQueue, CollisionNode,
                          CollisionRay, CollisionSphere, CollisionCapsule, CollisionSegment,
                          GeomNode, Point3)
loadPrcFile('etc/Configrc.prc')
loadPrcFileData('ttbots-bake', 'window-type none\naudio-library-name null\n'
                'audio-sfx-active #f\naudio-music-active #f\nnotify-level-loader error\n')
from panda3d.core import CSDefault  # noqa: E402
from panda3d.toontown import DNAStorage, loadDNAFile  # noqa: E402

WALL = BitMask32(1)
FLOOR = BitMask32(2)
VISIBLE = GeomNode.getDefaultCollideMask()
TOON_R = 1.4
TOON_C = 1.5          # wall sphere centre above the floor (GravityWalker: z=radius, +0.1 lift)

# hoodId: (hood storage, sz storage, sz dna, town storage, street dna prefix, streets)
HOODS = {
    2000: ('phase_4/dna/storage_TT.dna', 'phase_4/dna/storage_TT_sz.dna', 'phase_4/dna/toontown_central_sz.dna',
           'phase_5/dna/storage_TT_town.dna', 'phase_5/dna/toontown_central_', (2100, 2200, 2300), 'TTC'),
    1000: ('phase_6/dna/storage_DD.dna', 'phase_6/dna/storage_DD_sz.dna', 'phase_6/dna/donalds_dock_sz.dna',
           'phase_6/dna/storage_DD_town.dna', 'phase_6/dna/donalds_dock_', (1100, 1200, 1300), 'DD'),
    5000: ('phase_8/dna/storage_DG.dna', 'phase_8/dna/storage_DG_sz.dna', 'phase_8/dna/daisys_garden_sz.dna',
           'phase_8/dna/storage_DG_town.dna', 'phase_8/dna/daisys_garden_', (5100, 5200, 5300), 'DG'),
    4000: ('phase_6/dna/storage_MM.dna', 'phase_6/dna/storage_MM_sz.dna', 'phase_6/dna/minnies_melody_land_sz.dna',
           'phase_6/dna/storage_MM_town.dna', 'phase_6/dna/minnies_melody_land_', (4100, 4200, 4300), 'MML'),
    3000: ('phase_8/dna/storage_BR.dna', 'phase_8/dna/storage_BR_sz.dna', 'phase_8/dna/the_burrrgh_sz.dna',
           'phase_8/dna/storage_BR_town.dna', 'phase_8/dna/the_burrrgh_', (3100, 3200, 3300), 'TB'),
    9000: ('phase_8/dna/storage_DL.dna', 'phase_8/dna/storage_DL_sz.dna', 'phase_8/dna/donalds_dreamland_sz.dna',
           'phase_8/dna/storage_DL_town.dna', 'phase_8/dna/donalds_dreamland_', (9100, 9200), 'DDL'),
    8000: ('phase_6/dna/storage_GS.dna', 'phase_6/dna/storage_GS_sz.dna', 'phase_6/dna/goofy_speedway_sz.dna',
           None, None, (), 'GS'),
    6000: ('phase_6/dna/storage_OZ.dna', 'phase_6/dna/storage_OZ_sz.dna', 'phase_6/dna/outdoor_zone_sz.dna',
           None, None, (), 'OZ'),
    17000: ('phase_6/dna/storage_GZ.dna', 'phase_6/dna/storage_GZ_sz.dna', 'phase_6/dna/golf_zone_sz.dna',
            None, None, (), 'GZ'),
}

# Cog HQ zones are one model each (CogHQLoader.loadPlaceGeom), not DNA. Per zone: the model, the loader's
# tunnel renames, cog HQ door index -> destination zone (the *HQDataAI makeDoor/extDoor calls), and the
# elevators / karts the client adds from distributed objects (their collision is part of the world):
#   'origins': (elevator model, {elevator_origin_N index: place name})   placed at the model's locator
#   'locator': (elevator model, place name, h)                            lobby boss elevator at elevator_locator
#   'fixed':   [(elevator model, place name, pos, h, scale)]              DistributedFactoryElevatorExt.setEntranceId
#   'karts':   [(place name, pos, h)]                                     BossbotHQDataAI cog kart posList/hprList
MODEL_ZONES = {
    10000: {'model': 'phase_12/models/bossbotHQ/CogGolfHub', 'rename': [('LinkTunnel1', 'linktunnel_gz_17000_DNARoot')],
            'doors': {0: 10100},
            'karts': [('kart_front_three', (154.762, 37.169, 0), 110.815), ('kart_middle_six', (141.403, -81.887, 0), 61.231),
                      ('kart_back_nine', (-48.44, 15.308, 0), -105.481)]},
    10100: {'model': 'phase_12/models/bossbotHQ/CogGolfCourtyard', 'doors': {0: 10000},
            'locator': ('phase_12/models/bossbotHQ/BB_Elevator', 'elevator_ceo', 0)},
    11000: {'model': 'phase_9/models/cogHQ/SellbotHQExterior',
            'rename': [('Tunnel1', 'linktunnel_dg_5316_DNARoot'), ('Tunnel2', 'linktunnel_sellhq_11200_DNARoot')],
            'doors': {0: 11100, 1: 11100, 2: 11100, 3: 11100}},
    11100: {'model': 'phase_9/models/cogHQ/SellbotHQLobby', 'doors': {0: 11000},
            'locator': ('phase_9/models/cogHQ/cogHQ_elevator', 'elevator_vp', 180)},
    11200: {'model': 'phase_9/models/cogHQ/SellbotFactoryExterior',
            'rename': [('tunnel_group2', 'linktunnel_sellhq_11000_DNARoot')],
            'fixed': [('phase_4/models/modules/elevator', 'elevator_factory_front', (62.74, -85.31, 0.0), 2.0, 1.05),
                      ('phase_4/models/modules/elevator', 'elevator_factory_side', (-162.25, 26.43, 0.0), 269.0, 1.05)]},
    12000: {'model': 'phase_10/models/cogHQ/CashBotShippingStation',
            'rename': [('LinkTunnel1', 'linktunnel_dl_9252_DNARoot')], 'doors': {0: 12100},
            'origins': ('phase_10/models/cogHQ/mintElevator',
                        {1: 'elevator_coin_mint', 2: 'elevator_dollar_mint', 0: 'elevator_bullion_mint'}),
            # CashbotHQExterior.TrainTracks: trains run along y at z -67 and squish (10 laff) a toon they hit
            'hazards': [{'kind': 'train', 'y': y, 'halfWidth': 12.0, 'zBelow': -60.0}
                        for y in (-54.45, -133.45, -212.45, -291.45)]},
    12100: {'model': 'phase_10/models/cogHQ/VaultLobby', 'doors': {0: 12000},
            'locator': ('phase_10/models/cogHQ/CFOElevator', 'elevator_cfo', 0)},
    13000: {'model': 'phase_11/models/lawbotHQ/LawbotPlaza', 'rename': [('TunnelEntrance1', 'linktunnel_br_3326_DNARoot')],
            'doors': {0: 13200, 1: 13100}},
    13100: {'model': 'phase_11/models/lawbotHQ/LB_CH_Lobby', 'doors': {0: 13000},
            'locator': ('phase_11/models/lawbotHQ/LB_Elevator', 'elevator_cj', 0)},
    13200: {'model': 'phase_11/models/lawbotHQ/LB_DA_Lobby', 'doors': {0: 13000},
            'origins': ('phase_10/models/cogHQ/mintElevator',
                        {0: 'elevator_office_a', 1: 'elevator_office_b', 2: 'elevator_office_c', 3: 'elevator_office_d'})},
}
COG_KART_R = 5.0

# Water: a floor below the zone's water line with a visible surface above it (pond / sea / stream).
# Levels from FishingTargetGlobals (ponds), DDPlayground (-2.33 swim line), OZPlayground (-0.53).
WATER_LEVEL = {2000: -1.4, 2100: -1.838, 2200: -1.862, 2300: -1.886, 1000: -1.885, 1100: -2.482, 1200: -2.482,
               1300: -2.482, 5000: -1.825, 5100: -2.048, 5200: -2.048, 5300: -1.877, 4000: -2.65, 4100: -2.714,
               4200: -2.714, 4300: -2.714, 3000: -0.8, 3100: -2.4, 3200: -2.4, 3300: -2.4, 9000: -2.5,
               9100: -2.478, 9200: -2.478, 6000: -0.53, 8000: None}
# Measured top of the visible water mesh (*pd_water, MMsz_water, water_surface, water1). Wins over the table
# above wherever it exists: the table's last FishingTargetGlobals column is not the surface (MML sz gave
# -14.65 - -12 = -2.65 while its water is at -14.55, which flagged the whole lower plaza as under water).
WATER_LEVEL.update({2100: -1.5, 2200: -1.5, 2300: -1.5, 1100: -2.0, 1200: -2.0, 1300: -2.0, 5000: -0.92,
                    5100: -1.5, 5200: -1.5, 5300: -1.5, 4000: -14.55, 4100: -0.83, 4200: -0.83, 4300: -0.83,
                    3100: -1.92, 3200: -2.0, 3300: -2.0, 9000: -17.02, 9100: -2.0, 9200: -2.0, 6000: -0.59})

# Client-side distributed objects that add walls the DNA does not carry.
EXTRA_WALLS = {5000: [((1.39, 92.91, 2.0), 4.5 * 2.5)]}   # DistributedDGFlower bigFlowerCollide r 4.5 on a 2.5x model
# DNA groups whose client object (not the DNA) brings the collision: name prefix -> wall radius
DNA_OBSTACLES = (('picnic_table', 6.0), ('golf_kart', 5.0), ('starting_block', 3.5), ('leaderBoard', 3.0))


GAME_TABLE_R = 5.0


def dnaTextPositions(dnaFile, prefix):
    """First pos inside each `node "<prefix>N"` group of a text DNA file (props that make no node)."""
    import re
    path = os.path.join(ROOT, 'resources', dnaFile)
    if not os.path.exists(path):
        return []
    txt = open(path).read()
    pat = r'node "%s\d+" \[[^\]]*?\[[^\]]*\][^\]]*?pos \[ *([-\d.e]+) +([-\d.e]+) +([-\d.e]+) *\]' % re.escape(prefix)
    return [tuple(float(v) for v in m.groups()) for m in re.finditer(pat, txt)]


def zoneFiles(zoneId):
    if zoneId in MODEL_ZONES:
        return [MODEL_ZONES[zoneId]['model']]
    hoodId = zoneId - zoneId % 1000
    h = HOODS[hoodId]
    if zoneId == hoodId:
        return ['phase_4/dna/storage.dna', h[0], h[1], h[2]]
    return ['phase_4/dna/storage.dna', h[0], 'phase_5/dna/storage_town.dna', h[3], h[4] + str(zoneId) + '.dna']


def allZones():
    out = []
    for hoodId, h in HOODS.items():
        out.append(hoodId)
        out.extend(h[5])
    out.extend(sorted(MODEL_ZONES))
    return out


def _loadModel(path):
    from panda3d.core import Loader, Filename
    n = Loader.getGlobalPtr().loadSync(Filename(path))
    if n is None:
        raise IOError('cannot load model %s' % path)
    return NodePath(n)


def visZoneOf(np):
    """First 4-5 digit numeric ancestor name = the street visgroup zone."""
    while not np.isEmpty():
        n = np.getName()
        if n.isdigit() and len(n) >= 4:
            return int(n)
        np = np.getParent()
    return None


class World:
    def __init__(self, zoneId):
        self.zoneId = zoneId
        self.hoodId = zoneId - zoneId % 1000
        self.store = DNAStorage()
        self.render = NodePath('render')
        self.extraPlaces = []      # (name, kind, (x, y, z), extra) from client-side objects (elevators, karts)
        self.hazards = []
        self.isModelZone = zoneId in MODEL_ZONES
        if self.isModelZone:
            self._loadModelZone(MODEL_ZONES[zoneId])
        else:
            files = zoneFiles(zoneId)
            for f in files[:-1]:
                loadDNAFile(self.store, f, CSDefault, 0)
            node = loadDNAFile(self.store, files[-1], CSDefault, 0)
            self.root = self.render.attachNewNode(node)
        self.waterLevel = WATER_LEVEL.get(zoneId)
        self._buildCollisionWorld()

    def _loadModelZone(self, spec):
        """Cog HQ: the loader's model + its renames + the elevators / karts the client places in it."""
        self.root = _loadModel(spec['model'])
        self.root.reparentTo(self.render)
        for old, new in spec.get('rename', ()):
            np = self.root.find('**/' + old)
            if np.isEmpty():
                raise LookupError('%d: no %s in %s' % (self.zoneId, old, spec['model']))
            np.setName(new)
            s = np.find('**/tunnel_sphere')
            if not s.isEmpty() and np.find('**/tunnel_trigger').isEmpty():
                s.setName('tunnel_trigger')
        self.cogDoors = dict(spec.get('doors', {}))

        def addElevator(model, name, xform, extra):
            e = _loadModel(model)
            e.reparentTo(self.root)
            xform(e)
            e.setName('elevatorModel_' + name)
            p = e.getPos(self.root)
            self.extraPlaces.append((name, 'elevator', (p[0], p[1], p[2]), dict(extra, h=round(e.getH(self.root), 2))))

        if 'origins' in spec:
            model, names = spec['origins']
            for idx, name in sorted(names.items()):
                loc = self.root.find('**/elevator_origin_%d' % idx)
                if loc.isEmpty():
                    raise LookupError('%d: no elevator_origin_%d' % (self.zoneId, idx))
                addElevator(model, name, lambda e, loc=loc: e.setPosHpr(loc, 0, 0, 0, 0, 0, 0), {'origin': idx})
        if 'locator' in spec:
            model, name, h = spec['locator']
            loc = self.root.find('**/elevator_locator')
            if loc.isEmpty():
                raise LookupError('%d: no elevator_locator' % self.zoneId)

            def atLocator(e, loc=loc, h=h):
                e.reparentTo(loc)
                e.setH(h)
                e.wrtReparentTo(self.root)
            addElevator(model, name, atLocator, {'boss': True})
        for model, name, pos, h, scale in spec.get('fixed', ()):
            addElevator(model, name, lambda e, pos=pos, h=h, scale=scale: (e.setPosHpr(pos[0], pos[1], pos[2], h, 0, 0),
                                                                           e.setScale(scale)), {})
        self.cogKarts = []
        for i, (name, pos, h) in enumerate(spec.get('karts', ())):
            self.cogKarts.append((pos, COG_KART_R))
            self.extraPlaces.append((name, 'cogkart', tuple(float(v) for v in pos), {'course': i, 'h': h}))
        self.hazards = list(spec.get('hazards', ()))

    def _buildCollisionWorld(self):
        """Copy every wall/floor CollisionNode into a flat world bucketed in 64 ft cells."""
        self.world = NodePath('collworld')
        self.meta = []            # index -> (orig name, visgroup zone)
        cells = {}
        big = self.world.attachNewNode('big')
        for cnp in self.root.findAllMatches('**/+CollisionNode'):
            n = cnp.node()
            m = n.getIntoCollideMask()
            if (m & (WALL | FLOOR)).isZero():
                continue
            cp = n.makeCopy()
            k = len(self.meta)
            cp.setName('k%d' % k)
            self.meta.append((cnp.getName(), visZoneOf(cnp)))
            np = NodePath(cp)
            np.setTransform(cnp.getTransform(self.root))
            b = cp.getBounds().makeCopy()
            if b.isEmpty():
                continue
            b.xform(np.getTransform().getMat())
            if b.isEmpty():
                continue
            c = b.getCenter()
            try:
                r = b.getRadius()
            except Exception:
                r = 999
            if r > 90:
                np.reparentTo(big)
            else:
                key = (int(c[0] // 64), int(c[1] // 64))
                if key not in cells:
                    cells[key] = self.world.attachNewNode('cell')
                np.reparentTo(cells[key])
        for pos, r in EXTRA_WALLS.get(self.zoneId, []) + getattr(self, 'cogKarts', []):
            cn = CollisionNode('k%d' % len(self.meta))
            self.meta.append(('extra_wall', None))
            cn.addSolid(CollisionSphere(Point3(*pos), r))
            cn.setIntoCollideMask(WALL)
            self.world.attachNewNode(cn)
        seen = set()
        for np in self.root.findAllMatches('**'):
            nm = np.getName()
            for pre, r in DNA_OBSTACLES:
                if nm.startswith(pre):
                    p = np.getPos(self.root)
                    if pre == 'golf_kart' and p.length() < 1e-3 and np.getNumChildren():
                        p = np.getChild(0).getPos(self.root)     # GZHoodDataAI: the kart sits on its starting_block
                    key = (round(p[0]), round(p[1]))
                    if p.length() < 1e-3 or key in seen:
                        break      # empty group container (pos 0) or a duplicate child
                    seen.add(key)
                    cn = CollisionNode('k%d' % len(self.meta))
                    self.meta.append(('obstacle_' + nm, visZoneOf(np)))
                    cn.addSolid(CollisionSphere(p, r))
                    cn.setIntoCollideMask(WALL)
                    self.world.attachNewNode(cn)
                    break
        # OZ checkers tables: their prop code is not in storage, so the DNA makes no node; take the text pos.
        self.gameTables = [] if self.isModelZone else dnaTextPositions(zoneFiles(self.zoneId)[-1], 'game_table_')
        for p in self.gameTables:
            cn = CollisionNode('k%d' % len(self.meta))
            self.meta.append(('obstacle_game_table', None))
            cn.addSolid(CollisionSphere(Point3(*p), GAME_TABLE_R))
            cn.setIntoCollideMask(WALL)
            self.world.attachNewNode(cn)

    # ---------------------------------------------------------------- batching
    def _run(self, solids, fromMask, scene=None, chunk=1024):
        scene = scene if scene is not None else self.world
        out = [[] for _ in solids]
        for base in range(0, len(solids), chunk):
            trav = CollisionTraverser('bake')
            q = CollisionHandlerQueue()
            nps = []
            holder = scene.attachNewNode('probes')
            for i, s in enumerate(solids[base:base + chunk]):
                cn = CollisionNode('p%d' % (base + i))
                cn.addSolid(s)
                cn.setFromCollideMask(fromMask)
                cn.setIntoCollideMask(BitMask32.allOff())
                np = holder.attachNewNode(cn)
                trav.addCollider(np, q)
                nps.append(np)
            trav.traverse(scene)
            for e in q.getEntries():
                idx = int(e.getFromNodePath().getName()[1:])
                out[idx].append(e)
            holder.removeNode()
        return out

    def floorHits(self, xys, top=200.0, jitter=0.05):
        """All tangible floor surfaces under each (x, y): list of (z, meta index).
        A ray that lands exactly on a seam between two floor polygons can miss both; an empty result is
        retried at +-jitter ft (a crack that thin is not a hole a toon falls through)."""
        res = self._floorHits(xys, top)
        if jitter:
            for dx, dy in ((jitter, jitter), (-jitter, -jitter), (jitter, -jitter), (-jitter, jitter)):
                miss = [i for i, h in enumerate(res) if not h]
                if not miss:
                    break
                again = self._floorHits([(xys[i][0] + dx, xys[i][1] + dy) for i in miss], top)
                for i, h in zip(miss, again):
                    res[i] = h
        return res

    def _floorHits(self, xys, top):
        rays = [CollisionRay(x, y, top, 0, 0, -1) for x, y in xys]
        res = []
        for ents in self._run(rays, FLOOR):
            hits = []
            for e in ents:
                if not e.getInto().isTangible():
                    continue
                if (e.getIntoNode().getIntoCollideMask() & FLOOR).isZero():
                    continue
                k = int(e.getIntoNode().getName()[1:])
                hits.append((e.getSurfacePoint(self.world)[2], k))
            hits.sort(reverse=True)
            res.append(hits)
        return res

    def standable(self, hits, clear=3.5):
        """Keep floors with head room: a floor within `clear` ft below a higher one is merged away."""
        out = []
        for z, k in hits:
            if out and out[-1][0] - z < clear:
                continue
            out.append((z, k))
        return out

    def _wallHit(self, ents):
        for e in ents:
            if not e.getInto().isTangible():
                continue
            m = e.getIntoNode().getIntoCollideMask()
            if (m & WALL).isZero():
                continue
            # A wall+floor surface facing up (the Cashbot vault lobby stair ramp, mask 7) is walked up: the
            # floor ray lifts the toon and the pusher only nudges the sphere off the slope.
            if not (m & FLOOR).isZero() and e.hasSurfaceNormal() and e.getSurfaceNormal(self.world)[2] > 0.5:
                continue
            return self.meta[int(e.getIntoNode().getName()[1:])][0]
        return None

    def spheresBlocked(self, pts, r=TOON_R, lift=TOON_C):
        sph = [CollisionSphere(x, y, z + lift, r) for x, y, z in pts]
        return [self._wallHit(e) for e in self._run(sph, WALL)]

    def capsulesBlocked(self, segs, r=TOON_R, lift=TOON_C):
        caps = [CollisionCapsule(a[0], a[1], a[2] + lift, b[0], b[1], b[2] + lift, r) for a, b in segs]
        return [self._wallHit(e) for e in self._run(caps, WALL)]

    def segmentsBlocked(self, segs, heights=(0.5, 1.5, 3.0)):
        """Thin wall test, both directions (collision polygons are one-sided)."""
        solids, owner = [], []
        for i, (a, b) in enumerate(segs):
            for h in heights:
                pa = Point3(a[0], a[1], a[2] + h)
                pb = Point3(b[0], b[1], b[2] + h)
                if (pa - pb).length() < 1e-3:
                    continue
                solids.append(CollisionSegment(pa, pb)); owner.append(i)
                solids.append(CollisionSegment(pb, pa)); owner.append(i)
        out = [None] * len(segs)
        for i, ents in zip(owner, self._run(solids, WALL)):
            if out[i] is None:
                out[i] = self._wallHit(ents)
        return out

    def isWater(self, pts):
        """(x, y, floorZ) -> True when the floor is under this zone's water with a visible surface above it."""
        wl = self.waterLevel
        res = [False] * len(pts)
        if wl is None:
            return res
        idx = [i for i, p in enumerate(pts) if p[2] < wl - 0.3]
        if not idx:
            return res
        rays = [CollisionRay(pts[i][0], pts[i][1], wl + 1.5, 0, 0, -1) for i in idx]
        for i, ents in zip(idx, self._run(rays, VISIBLE, scene=self.render, chunk=256)):
            fz = pts[i][2]
            for e in ents:
                z = e.getSurfacePoint(self.render)[2]
                if fz + 0.2 < z <= wl + 1.5:
                    res[i] = True
                    break
        return res
