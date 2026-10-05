"""Replay-check baked walk maps against the real collision: python check_walkmaps.py [zone ...] [--paths N]

Loads each map through walkmaps/WalkMap.py (the code the bots run), plans N seeded random A* paths
(raw and smooth) and replays every segment against ttworld.World:
  crossing = a wall hit by a thin segment at 0.3/1/2/3 ft, either direction
  fall     = no floor under a 0.5 ft sample, or a drop > 2.5 ft between samples
  water    = a sample on a floor under the zone's water
PASS = 0 crossings / water / falls on raw paths. Exit code 1 on FAIL.
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ttworld  # noqa: E402
from ttworld import World  # noqa: E402
sys.path.insert(0, os.path.join(ttworld.ROOT, 'toontown', 'bots', 'walkmaps'))
from WalkMap import WalkMap  # noqa: E402

HEIGHTS = (0.3, 1.0, 2.0, 3.0)
STEP = 0.5
DROP = 2.5              # = the baker STEP: a toon steps any riser with no wall (floor ray starts 4000 ft up)


def replay(w, segs):
    """segs: list of ((x,y,z),(x,y,z)) -> (crossings, water, falls) as lists of bad segment indices."""
    walls = w.segmentsBlocked(segs, HEIGHTS)
    xy, owner, zi = [], [], []
    for s, (a, b) in enumerate(segs):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / STEP)))
        for q in range(0, k + 1):
            t = q / k
            xy.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            owner.append(s)
            zi.append(a[2] + (b[2] - a[2]) * t)
    hits = w.floorHits(xy)
    fall = set()
    prev = {}
    pts, powner = [], []
    for n, (s, h) in enumerate(zip(owner, hits)):
        if not h:
            fall.add(s)
            continue
        z = min(h, key=lambda q: abs(q[0] - zi[n]))[0]
        if s in prev and prev[s] - z > DROP:
            fall.add(s)
        prev[s] = z
        pts.append((xy[n][0], xy[n][1], z)); powner.append(s)
    water = {s for s, wa in zip(powner, w.isWater(pts)) if wa}
    cross = {s for s, hit in enumerate(walls) if hit is not None}
    return cross, water, fall, walls


def check(zoneId, nPaths):
    t0 = time.time()
    wm = WalkMap.load(zoneId)
    w = World(zoneId)
    rng = random.Random(zoneId * 7919 + 1)
    raw, smooth = set(), set()
    rawPaths, smPaths, failedPlans = [], [], 0
    for _ in range(nPaths):
        a = wm.randomPoint(rng)
        b = wm.randomPoint(rng)
        p = wm.path(a, b)
        if not p:
            failedPlans += 1
            continue
        sp = wm.path(a, b, smooth=True)
        rawPaths.append(p); smPaths.append(sp)
        for u, v in zip(p, p[1:]):
            raw.add((u, v))
        for u, v in zip(sp, sp[1:]):
            smooth.add((u, v))
    tPlan = time.time() - t0
    rawSegs = sorted(raw)
    smSegs = sorted(smooth - raw)
    c, wa, f, walls = replay(w, rawSegs)
    sc, swa, sf, swalls = replay(w, smSegs) if smSegs else (set(), set(), set(), [])
    samples = []
    for s in sorted(c | wa | f)[:5]:
        samples.append('raw %s->%s %s%s%s' % (_r(rawSegs[s][0]), _r(rawSegs[s][1]), 'wall:%s ' % walls[s] if s in c else '',
                                             'water ' if s in wa else '', 'fall' if s in f else ''))
    for s in sorted(sc | swa | sf)[:3]:
        samples.append('smooth %s->%s %s%s%s' % (_r(smSegs[s][0]), _r(smSegs[s][1]),
                                                'wall:%s ' % swalls[s] if s in sc else '',
                                                'water ' if s in swa else '', 'fall' if s in sf else ''))
    places = wm.places()
    unreached = [p['name'] for p in places if p.get('node', -1) < 0]
    avgRaw = sum(len(p) for p in rawPaths) / max(1, len(rawPaths))
    avgSm = sum(len(p) for p in smPaths) / max(1, len(smPaths))
    return {'zone': zoneId, 'nodes': wm.nodeCount, 'area': wm.nodeCount * wm.spacing ** 2,
            'places': len(places) - len(unreached), 'unreached': unreached, 'paths': len(rawPaths),
            'noPath': failedPlans, 'rawSegs': len(rawSegs), 'cross': len(c), 'water': len(wa), 'falls': len(f),
            'smSegs': len(smSegs), 'sCross': len(sc), 'sWater': len(swa), 'sFalls': len(sf),
            'avgRaw': avgRaw, 'avgSmooth': avgSm, 'samples': samples, 'secs': time.time() - t0, 'plan': tPlan}


def _r(p):
    return '(%.1f,%.1f,%.1f)' % p


def main(argv):
    nPaths = 2000
    zones = []
    it = iter(argv)
    for a in it:
        if a == '--paths':
            nPaths = int(next(it))
        else:
            zones.append(int(a))
    zones = zones or [z for z in ttworld.allZones() if z in WalkMap.available()]
    res = []
    for z in zones:
        r = check(z, nPaths)
        res.append(r)
        print('%5d nodes %6d paths %4d noPath %d raw segs %6d X/W/F %d/%d/%d  smooth segs %6d X/W/F %d/%d/%d  (%.0fs)'
              % (z, r['nodes'], r['paths'], r['noPath'], r['rawSegs'], r['cross'], r['water'], r['falls'],
                 r['smSegs'], r['sCross'], r['sWater'], r['sFalls'], r['secs']), flush=True)
        for s in r['samples']:
            print('      ' + s, flush=True)
    print()
    print('zone   nodes    area(sqft) places unreached  raw X/W/F  smooth X/W/F  avg pts raw/smooth')
    ok = smOk = True
    for r in res:
        print('%5d %7d %12.0f %6d %9d %4d/%d/%d %8d/%d/%d %9.1f/%.1f %s' % (
            r['zone'], r['nodes'], r['area'], r['places'], len(r['unreached']), r['cross'], r['water'], r['falls'],
            r['sCross'], r['sWater'], r['sFalls'], r['avgRaw'], r['avgSmooth'],
            ('unreached=' + ','.join(r['unreached'])) if r['unreached'] else ''))
        ok = ok and r['cross'] == r['water'] == r['falls'] == 0 and r['noPath'] == 0
        smOk = smOk and r['sCross'] == r['sWater'] == r['sFalls'] == 0
    print('RAW %s   SMOOTH %s' % ('PASS' if ok else 'FAIL', 'PASS' if smOk else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
