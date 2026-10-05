"""Bake bot walk maps: python bake_walkmaps.py [zone ...]   (default: every zone in ttworld.allZones()).

Per zone: a 3 ft grid over the floor collision, standable layers, minus water and wall-blocked spots,
8-way edges proven by floor samples + capsule + thin-segment wall tests, pruned to the reachable
component(s). Output toontown/bots/walkmaps/<zone>.json.gz (read by walkmaps/WalkMap.py) and, for
playgrounds, a top-down PNG in <repo>/walkmaps_png (a preview, not used by the game).
Run under ppython from the repo root.
"""
import gzip
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ttworld  # noqa: E402  (chdirs to the repo root, loads prc)
from ttworld import World, FLOOR  # noqa: E402
from panda3d.core import CollisionPolygon, PNMImage, Filename  # noqa: E402

SPACING = 3.0
MAX_SLOPE = 1.2          # ft of rise per ft of run
STEP = 2.5               # a single riser a toon steps over (no wall on it; walls are tested separately)
SAMPLE = 0.5             # floor sample step along an edge (same as the checker)
SEG_HEIGHTS = (0.3, 0.5, 1.0, 1.5, 2.0, 3.0)
APPROACH = 15.0
OUT_DIR = os.path.join(ttworld.ROOT, 'toontown', 'bots', 'walkmaps')
PNG_DIR = os.path.join(ttworld.ROOT, 'walkmaps_png')
# dir bits: +x, +x+y, +y, -x+y, -x, -x-y, -y, +x-y
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
HALF = (0, 1, 2, 3)      # the other four are their opposites (d + 4) % 8


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ grid bounds
def floorBounds(w):
    lo = [1e9, 1e9]
    hi = [-1e9, -1e9]
    for np in w.world.findAllMatches('**/+CollisionNode'):
        n = np.node()
        if (n.getIntoCollideMask() & FLOOR).isZero():
            continue
        mat = np.getMat(w.world)
        for s in range(n.getNumSolids()):
            sol = n.getSolid(s)
            if not sol.isTangible():
                continue
            pts = []
            if isinstance(sol, CollisionPolygon):
                pts = [mat.xformPoint(sol.getPoint(i)) for i in range(sol.getNumPoints())]
            else:
                b = sol.getBounds()
                if b.isEmpty() or b.isInfinite():
                    continue
                c = mat.xformPoint(b.getCenter())
                r = b.getRadius()
                pts = [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
            for p in pts:
                lo[0] = min(lo[0], p[0]); lo[1] = min(lo[1], p[1])
                hi[0] = max(hi[0], p[0]); hi[1] = max(hi[1], p[1])
    return lo, hi


# ------------------------------------------------------------------ places
KIND_BY_NAME = (('gag_shop', 'gagshop'), ('clothes_shop', 'clothes'), ('pet_shop', 'petshop'), ('hq', 'hq'),
                ('_bank', 'bank'), ('library', 'library'), ('school_house', 'school'), ('toonhall', 'toonhall'),
                ('kart_shop', 'kartshop'))
PROP_KINDS = (('prop_trolley_station', 'trolley'), ('prop_toontown_central_fountain', 'fountain'),
              ('prop_daisys_fountain', 'fountain'), ('prop_gazebo', 'gazebo'), ('prop_party_gate', 'partygate'),
              ('prop_mickey_on_horse', 'statue'), ('prop_picnic_table', 'picnictable'))


def triggerCenter(w, np):
    return w.root.getRelativePoint(np, np.node().getBounds().getCenter())


def findPlaces(w):
    """[(name, kind, (x, y, z), extra)] in root space."""
    raw = []
    root = w.root
    for np in root.findAllMatches('**/*_DNARoot'):
        nm = np.getName()
        m = re.match(r'^(sz|tb|sb)(\d+):((?:toon_landmark|animated_building)_\S+)_DNARoot$', nm)
        if m:
            block = int(m.group(2))
            code = m.group(3)
            kind = None
            for key, k in KIND_BY_NAME:
                if key in code:
                    kind = k
                    break
            trigs = np.findAllMatches('**/door_trigger_*')
            if kind is None:
                if not trigs.getNumPaths() or w.zoneId == w.hoodId:
                    continue           # decoration landmark with no door
                kind = 'door'
            try:
                title = w.store.getTitleFromBlockNumber(block)
            except Exception:
                title = ''
            extra = {'block': block, 'title': title, 'prefix': m.group(1)}
            if w.zoneId != w.hoodId:
                extra['visZone'] = ttworld.visZoneOf(np)
                if kind == 'door':
                    extra['cogCapable'] = True     # a cog building that takes this block uses this door spot
            if trigs.getNumPaths():
                for ti, t in enumerate(trigs):
                    e = dict(extra)
                    if trigs.getNumPaths() > 1:
                        e['door'] = ti
                    raw.append((kind, kind, tuple(triggerCenter(w, t)), e))
            else:
                raw.append((kind, kind, tuple(np.getPos(root)), extra))
            continue
        m = re.match(r'^linktunnel_([a-z]+)_(\d+)_DNARoot$', nm)
        if m:
            pos = None
            trigR = 0.0
            for tn in ('tunnel_trigger', 'tunnel_sphere', 'tunnel_origin'):
                t = np.find('**/' + tn)
                if not t.isEmpty():
                    if t.node().isCollisionNode():
                        pos = triggerCenter(w, t)
                        trigR = t.node().getBounds().getRadius() * t.getNetTransform().getScale()[0]
                    else:
                        pos = t.getPos(root)
                    break
            if pos is None:
                pos = np.getPos(root)
            tz = int(m.group(2))
            raw.append(('tunnel', 'tunnel', tuple(pos), {'targetZone': tz, 'toZone': tz - tz % 100,
                                                         'triggerR': round(trigR, 2),
                                                         'visZone': ttworld.visZoneOf(np)}))
            continue
        if nm == 'fishing_spot_DNARoot':
            raw.append(('fishing', 'fishing', tuple(np.getPos(root)), {'pond': np.getParent().getName(),
                                                                        'visZone': ttworld.visZoneOf(np)}))
            continue
        for pre, kind in PROP_KINDS:
            if nm.startswith(pre):
                pos = np.getPos(root)
                if kind == 'trolley':
                    t = np.find('**/trolley_sphere')
                    if not t.isEmpty():
                        pos = triggerCenter(w, t)
                raw.append((kind, kind, tuple(pos), {}))
                break
    # GS racing / viewing pads: group pos is 0, use the centre of their starting blocks
    for np in root.findAllMatches('**'):
        nm = np.getName()
        m = re.match(r'^(racing_pad|viewing_pad)_(\d+)(?:_(\w+))?$', nm)
        if m:
            blocks = np.findAllMatches('**/starting_block_*')
            if not blocks.getNumPaths():
                continue
            ps = [b.getPos(root) for b in blocks]
            c = tuple(sum(p[i] for p in ps) / len(ps) for i in range(3))
            extra = {'pad': int(m.group(2)), 'blocks': len(ps)}
            if m.group(3):
                extra['track'] = m.group(3)
            raw.append((m.group(1), m.group(1).replace('_', ''), c, extra))
        elif re.match(r'^golf_kart_(\d+)_(\d+)$', nm):
            gp = np.getPos(root)
            if gp.length() < 1e-3 and np.getNumChildren():
                gp = np.getChild(0).getPos(root)     # GZHoodDataAI: the kart sits on its starting_block
            c, k = nm.split('_')[2:4]
            raw.append(('golfkart', 'golfkart', tuple(gp), {'course': int(c), 'kart': int(k)}))
        elif nm.startswith('leaderBoard_') and nm != 'leaderBoard_C':
            raw.append(('leaderboard', 'leaderboard', tuple(np.getPos(root)), {'track': nm.split('_', 1)[1]}))
    for p in getattr(w, 'gameTables', []):
        raw.append(('gametable', 'gametable', p, {}))
    # Cog HQ doors (DistributedCogHQDoor finds door_N/**/door_trigger_*) + the client's elevators and karts
    for di, dest in sorted(getattr(w, 'cogDoors', {}).items()):
        t = root.find('**/door_%d/**/door_trigger_*' % di)
        if t.isEmpty():
            t = root.find('**/door_trigger_%d' % di)
        if t.isEmpty():
            log('  %d: no trigger for cog HQ door_%d' % (w.zoneId, di))
            continue
        raw.append(('cogdoor', 'cogdoor', tuple(triggerCenter(w, t)), {'door': di, 'destZone': dest}))
    for name, kind, pos, extra in getattr(w, 'extraPlaces', []):
        raw.append((name, kind, pos, dict(extra, fixedName=name)))
    # unique names: kind, kind_1, ... (sorted by position for stable names)
    raw.sort(key=lambda r: (r[1], r[3].get('block', 0), round(r[2][0], 1), round(r[2][1], 1)))
    counts = {}
    for r in raw:
        counts[r[1]] = counts.get(r[1], 0) + 1
    seen = {}
    out = []
    for base, kind, pos, extra in raw:
        if 'fixedName' in extra:
            name = extra.pop('fixedName')
        elif kind == 'cogdoor':
            name = 'cogdoor_%d' % extra['door']
        elif kind == 'golfkart':
            name = 'golfkart_%d_%d' % (extra['course'], extra['kart'])
        elif 'block' in extra and kind == 'door':
            name = 'door_%d' % extra['block'] + ('_%d' % extra['door'] if 'door' in extra else '')
        elif counts[kind] > 1:
            n = seen.get(kind, 0)
            seen[kind] = n + 1
            name = '%s_%d' % (kind, n)
        else:
            name = kind
        if kind == 'tunnel':
            name = 'tunnel_%d' % extra['targetZone']
            if name in [o[0] for o in out]:
                name += '_b'
        out.append((name, kind, (float(pos[0]), float(pos[1]), float(pos[2])), extra))
    return out


# ------------------------------------------------------------------ bake
def bake(zoneId):
    t0 = time.time()
    w = World(zoneId)
    isPG = zoneId == w.hoodId
    lo, hi = floorBounds(w)
    x0 = math.floor(lo[0] / SPACING) * SPACING
    y0 = math.floor(lo[1] / SPACING) * SPACING
    nx = int((hi[0] - x0) / SPACING) + 1
    ny = int((hi[1] - y0) / SPACING) + 1
    log('%d: grid %dx%d (%.0f..%.0f, %.0f..%.0f)' % (zoneId, nx, ny, lo[0], hi[0], lo[1], hi[1]))
    xys = [(x0 + i * SPACING, y0 + j * SPACING) for j in range(ny) for i in range(nx)]
    hits = w.floorHits(xys)
    floorCells = set()
    cand = []      # (i, j, z, metaIdx)
    for idx, h in enumerate(hits):
        if not h:
            continue
        i, j = idx % nx, idx // nx
        floorCells.add((i, j))
        for z, k in w.standable(h):
            cand.append((i, j, z, k))
    pts = [(x0 + c[0] * SPACING, y0 + c[1] * SPACING, c[2]) for c in cand]
    water = w.isWater(pts)
    blocked = w.spheresBlocked(pts)
    nodes = [c for c, wa, bl in zip(cand, water, blocked) if not wa and bl is None]
    log('  candidates %d, water %d, blocked %d, nodes %d (%.0fs)' % (
        len(cand), sum(water), sum(1 for b in blocked if b), len(nodes), time.time() - t0))

    cell = {}
    for n, c in enumerate(nodes):
        cell.setdefault((c[0], c[1]), []).append(n)

    def partner(n, d):
        i, j, z = nodes[n][0], nodes[n][1], nodes[n][2]
        di, dj = DIRS[d]
        lst = cell.get((i + di, j + dj))
        if not lst:
            return None
        return min(lst, key=lambda m: abs(nodes[m][2] - z))

    pairs = []
    for n in range(len(nodes)):
        for d in HALF:
            m = partner(n, d)
            if m is None or partner(m, (d + 4) % 8) != n:
                continue
            a, b = nodes[n], nodes[m]
            if abs(b[2] - a[2]) > MAX_SLOPE * SPACING * math.hypot(*DIRS[d]) + 0.01:
                continue
            pairs.append((n, m, d))
    nMutual = len(pairs)
    # Stacked layers (a ramp over a lower floor): n's closest node next door is m, but m's closest node back
    # is the floor under the ramp, so the pair is not mutual and the 8-way mask cannot hold it. Test it like
    # any edge; if it passes it ships as an explicit link ('links' in the JSON, read by WalkMap).
    seenPair = {(n, m) for n, m, d in pairs}
    for n in range(len(nodes)):
        for d in range(8):
            m = partner(n, d)
            if m is None or partner(m, (d + 4) % 8) == n:
                continue
            a, b, dd = (n, m, d) if d < 4 else (m, n, d - 4)
            if (a, b) in seenPair:
                continue
            if abs(nodes[b][2] - nodes[a][2]) > MAX_SLOPE * SPACING * math.hypot(*DIRS[dd]) + 0.01:
                continue
            seenPair.add((a, b))
            pairs.append((a, b, dd))
    log('  mutual pairs %d, stacked-layer pairs %d' % (nMutual, len(pairs) - nMutual))

    def P(n):
        c = nodes[n]
        # z as shipped (z*10 rounded): edges are proven at the exact point WalkMap / the checker replay; the
        # 12000 tunnel ramp had an edge that cleared the ceiling at the raw z and clipped it 0.04 ft higher.
        return (x0 + c[0] * SPACING, y0 + c[1] * SPACING, round(c[2] * 10) / 10.0)

    # floor samples along each pair
    sampleXY, sampleOwner, sampleT = [], [], []
    for pi, (n, m, d) in enumerate(pairs):
        a, b = P(n), P(m)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / SAMPLE)))
        for s in range(1, k):
            t = s / k
            sampleXY.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            sampleOwner.append(pi)
            sampleT.append(t)
    sh = w.floorHits(sampleXY)
    chains = [[] for _ in pairs]
    for s, pi in enumerate(sampleOwner):
        n, m, d = pairs[pi]
        za, zb = nodes[n][2], nodes[m][2]
        zi = za + (zb - za) * sampleT[s]
        h = sh[s]
        if not h:
            chains[pi].append(None)
            continue
        z = min(h, key=lambda q: abs(q[0] - zi))[0]
        chains[pi].append((sampleXY[s][0], sampleXY[s][1], z))
    ok = [True] * len(pairs)
    why = [None] * len(pairs)
    wpts, wowner = [], []
    for pi, (n, m, d) in enumerate(pairs):
        a, b = P(n), P(m)
        ch = chains[pi]
        if any(c is None for c in ch):
            ok[pi] = False; why[pi] = 'nofloor'
            continue
        seq = [a] + ch + [b]
        for p, q in zip(seq, seq[1:]):
            run = math.hypot(q[0] - p[0], q[1] - p[1])
            if abs(q[2] - p[2]) > max(MAX_SLOPE * run + 0.05, STEP):
                ok[pi] = False; why[pi] = 'slope'
                break
        if ok[pi]:
            for c in ch:
                wpts.append(c); wowner.append(pi)
    for pi, wa in zip(wowner, w.isWater(wpts)):
        if wa:
            ok[pi] = False; why[pi] = 'water'
    live = [pi for pi in range(len(pairs)) if ok[pi]]
    segs = [(P(pairs[pi][0]), P(pairs[pi][1])) for pi in live]
    capb = w.capsulesBlocked(segs)
    segb = w.segmentsBlocked(segs, SEG_HEIGHTS)
    for pi, c, s in zip(live, capb, segb):
        if c is not None or s is not None:
            ok[pi] = False; why[pi] = 'wall:%s' % (c or s)
    log('  edges ok %d / %d (%.0fs)' % (sum(ok), len(pairs), time.time() - t0))

    mask = [0] * len(nodes)
    adj = [[] for _ in nodes]
    links = []
    for pi, (n, m, d) in enumerate(pairs):
        if ok[pi]:
            if pi < nMutual:
                mask[n] |= 1 << d
                mask[m] |= 1 << ((d + 4) % 8)
            else:
                links.append((n, m))
            adj[n].append(m); adj[m].append(n)

    # components
    comp = [-1] * len(nodes)
    sizes = []
    for s in range(len(nodes)):
        if comp[s] >= 0:
            continue
        c = len(sizes)
        comp[s] = c
        stack = [s]
        cnt = 0
        while stack:
            u = stack.pop(); cnt += 1
            for v in adj[u]:
                if comp[v] < 0:
                    comp[v] = c; stack.append(v)
        sizes.append(cnt)

    def nearest(pos, maxd=APPROACH, pool=None, maxdz=8.0):
        ci = int(round((pos[0] - x0) / SPACING)); cj = int(round((pos[1] - y0) / SPACING))
        r = int(math.ceil(maxd / SPACING)) + 1
        best, bd = None, maxd
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                for n in cell.get((i, j), ()):
                    if pool is not None and not pool(n):
                        continue
                    p = P(n)
                    if abs(p[2] - pos[2]) > maxdz:
                        continue
                    dd = math.hypot(p[0] - pos[0], p[1] - pos[1])
                    if dd <= bd:
                        best, bd = n, dd
        return best, bd

    places = findPlaces(w)
    drops = []
    if isPG:
        try:
            from toontown.distributed.HoodMgr import HoodMgr
            drops = [tuple(d[:4]) for d in HoodMgr.dropPoints.get(w.hoodId, [])]
        except Exception as e:
            log('  no drop points:', e)
    seedPts = [d[:3] for d in drops] if isPG and drops else         [p[2] for p in places if p[1] in ('tunnel', 'door', 'cogdoor')]
    seeds = set()
    for sp in seedPts:
        n, _ = nearest(sp)
        if n is not None and sizes[comp[n]] > 1:
            seeds.add(comp[n])
    if not seeds:
        seeds = {max(range(len(sizes)), key=lambda c: sizes[c])} if sizes else set()
        log('  no seed reached a component: keeping the largest')
    big = max((sizes[c] for c in seeds), default=0)
    keepC = {max(seeds, key=lambda c: sizes[c])} if seeds else set()   # one component: every pair has a path
    keep = [n for n in range(len(nodes)) if comp[n] in keepC]
    log('  components %d, seeded %d, kept %d (%d nodes)' % (len(sizes), len(seeds), len(keepC), len(keep)))

    if os.environ.get('TTBAKE_DBG'):
        from collections import Counter
        wc = Counter()
        ex = {}
        for pi, (n, m, d) in enumerate(pairs):
            if not ok[pi] and comp[n] != comp[m] and min(sizes[comp[n]], sizes[comp[m]]) > 100:
                wc[why[pi]] += 1
                ex.setdefault(why[pi], (P(n), P(m)))
        log('  DBG joins refused:', dict(wc))
        for k, v in ex.items():
            log('    ', k, v)
        debugPNG(zoneId, x0, y0, nx, ny, floorCells, nodes, comp, keepC, mask)
        with open(os.path.join(os.environ['TTBAKE_DBG'], '%d.nodes.json' % zoneId), 'w') as f:
            json.dump({'x0': x0, 'y0': y0, 'nodes': nodes, 'comp': comp, 'keep': sorted(keepC), 'mask': mask,
                       'pairs': [(n, m, d, ok[pi], why[pi]) for pi, (n, m, d) in enumerate(pairs)]}, f)

    remap = {n: k for k, n in enumerate(keep)}
    inKeep = lambda n: n in remap  # noqa: E731

    # street visgroup per node
    zoneList, zoneIdx = [], []
    if isPG:
        zoneList = [zoneId]
        zoneIdx = [0] * len(keep)
    else:
        vz = [w.meta[nodes[n][3]][1] for n in keep]
        # fill missing from the nearest tagged node (BFS over the kept graph)
        from collections import deque
        q = deque(k for k, z in enumerate(vz) if z is not None)
        while q:
            k = q.popleft()
            for v in adj[keep[k]]:
                kv = remap.get(v)
                if kv is not None and vz[kv] is None:
                    vz[kv] = vz[k]; q.append(kv)
        zoneList = sorted({z for z in vz if z is not None}) or [zoneId]
        zi = {z: i for i, z in enumerate(zoneList)}
        zoneIdx = [zi.get(z, 0) for z in vz]

    outNodes = []
    for k, n in enumerate(keep):
        i, j, z, _ = nodes[n]
        outNodes.append([i, j, int(round(z * 10)), mask[n], zoneIdx[k]])

    outPlaces, unreached = [], []
    for name, kind, pos, extra in places:
        # a tunnel trigger sphere (r 10-15) sits in the tunnel mouth, past the last floor: its edge is enough
        n, dist = nearest(pos, maxd=max(APPROACH, extra.get('triggerR', 0) + 3.0), pool=inKeep)
        rec = {'name': name, 'kind': kind, 'pos': [round(v, 2) for v in pos], 'extra': extra}
        if n is None:
            rec['node'] = -1
            unreached.append(name)
        else:
            rec['node'] = remap[n]
            rec['dist'] = round(dist, 2)
        outPlaces.append(rec)
    outDrops = []
    for d in drops:
        n, dist = nearest(d[:3], pool=inKeep)
        outDrops.append({'pos': [round(v, 2) for v in d], 'node': remap[n] if n is not None else -1,
                         'dist': round(dist, 2) if n is not None else None})

    stats = {'nodes': len(keep), 'area': len(keep) * SPACING * SPACING, 'edges': sum(bin(m).count('1') for m in
             (mask[n] for n in keep)) // 2, 'components': len(sizes), 'keptComponents': len(keepC),
             'places': len(outPlaces), 'unreached': unreached,
             'dropsUnreached': sum(1 for d in outDrops if d['node'] < 0), 'bakeSeconds': round(time.time() - t0, 1)}
    data = {'zone': zoneId, 'hood': w.hoodId, 'spacing': SPACING, 'x0': x0, 'y0': y0, 'nodes': outNodes,
            'zones': zoneList, 'places': outPlaces, 'drops': outDrops, 'stats': stats}
    outLinks = sorted([remap[n], remap[m]] for n, m in links if n in remap and m in remap)
    if outLinks:
        data['links'] = outLinks
        stats['links'] = len(outLinks)
    if w.hazards:
        data['hazards'] = w.hazards
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, '%d.json.gz' % zoneId)
    raw = json.dumps(data, separators=(',', ':')).encode()
    with gzip.GzipFile(path, 'wb', mtime=0) as f:
        f.write(raw)
    log('  wrote %s: %d nodes, %d places (%d unreached), %.0f KB, %.0fs' % (
        os.path.basename(path), len(keep), len(outPlaces), len(unreached), os.path.getsize(path) / 1024.0,
        time.time() - t0))
    if isPG:
        renderPNG(zoneId, x0, y0, nx, ny, floorCells, outNodes, outPlaces, outDrops)
    return stats


def debugPNG(zoneId, x0, y0, nx, ny, floorCells, nodes, comp, keepC, mask):
    """TTBAKE_DBG=<dir>: every node, kept components green, dropped ones orange, 2 px per cell."""
    s = 2
    img = PNMImage(nx * s + 1, ny * s + 1)
    img.fill(0.05, 0.05, 0.08)
    for i, j in floorCells:
        img.setXel(i * s, (ny - 1 - j) * s, 0.35, 0.35, 0.37)
    for n, c in enumerate(nodes):
        col = (0.3, 0.9, 0.3) if comp[n] in keepC else (1.0, 0.55, 0.1)
        if comp[n] in keepC and len(keepC) > 1 and comp[n] != min(keepC):
            col = (0.3, 0.6, 1.0)
        x, y = c[0] * s, (ny - 1 - c[1]) * s
        img.setXel(x, y, *col)
        for d in range(8):
            if mask[n] & (1 << d):
                img.setXel(x + DIRS[d][0], y - DIRS[d][1], *col)
    d = os.environ['TTBAKE_DBG']
    os.makedirs(d, exist_ok=True)
    img.write(Filename.fromOsSpecific(os.path.join(d, '%d.png' % zoneId)))


def renderPNG(zoneId, x0, y0, nx, ny, floorCells, outNodes, outPlaces, outDrops):
    s = 3   # px per grid cell -> 1 px per ft
    W, H = nx * s + 1, ny * s + 1
    img = PNMImage(W, H)
    img.fill(0.08, 0.08, 0.1)

    def px(i, j):
        return int(i * s), int((ny - 1 - j) * s)

    def dot(x, y, r, c):
        for yy in range(y - r, y + r + 1):
            for xx in range(x - r, x + r + 1):
                if 0 <= xx < W and 0 <= yy < H:
                    img.setXel(xx, yy, *c)

    for i, j in floorCells:
        x, y = px(i, j)
        dot(x, y, 1, (0.35, 0.35, 0.37))
    pos = {}
    for n in outNodes:
        pos.setdefault((n[0], n[1]), []).append(n)
    for n in outNodes:
        x, y = px(n[0], n[1])
        for d in range(8):
            if n[3] & (1 << d):
                di, dj = DIRS[d]
                for t in range(1, s):
                    xx, yy = x + di * t, y - dj * t
                    if 0 <= xx < W and 0 <= yy < H:
                        img.setXel(xx, yy, 0.2, 0.65, 0.3)
        full = n[3] == 255
        img.setXel(x, y, *((0.6, 1.0, 0.6) if full else (1.0, 0.85, 0.2)))

    def world2px(p):
        return int(round((p[0] - x0) / SPACING * s)), int(round((ny - 1 - (p[1] - y0) / SPACING) * s))
    for d in outDrops:
        x, y = world2px(d['pos'])
        dot(x, y, 2, (0.2, 0.5, 1.0))
    for p in outPlaces:
        x, y = world2px(p['pos'])
        dot(x, y, 3, (1.0, 0.15, 0.15) if p['node'] >= 0 else (1.0, 0.0, 1.0))
    os.makedirs(PNG_DIR, exist_ok=True)
    if not img.write(Filename.fromOsSpecific(os.path.join(PNG_DIR, '%d.png' % zoneId))):
        log('  PNG write failed')


def main(argv):
    zones = [int(a) for a in argv] or ttworld.allZones()
    t = time.time()
    res = {}
    for z in zones:
        res[z] = bake(z)
    log('ALL DONE %d zones in %.0fs' % (len(zones), time.time() - t))
    for z, s in res.items():
        log('%5d nodes %6d area %8.0f places %3d unreached %s' % (z, s['nodes'], s['area'], s['places'],
                                                                  ','.join(s['unreached']) or '-'))


if __name__ == '__main__':
    main(sys.argv[1:])
