"""Bake stand spots for building interiors: floor under each spot, clear of walls, reachable in a
straight line from the entry point (in front of the door). Output JSON for toontown/bots/activities."""
import os, sys, json, math
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
os.chdir(ROOT); sys.path.insert(0, ROOT)
from panda3d.core import *
loadPrcFile('etc/Configrc.prc')
loadPrcFileData('p', 'window-type none\naudio-library-name null\nnotify-level-loader error\n')
from direct.showbase.ShowBase import ShowBase
base = ShowBase()
from panda3d.toontown import DNAStorage, loadDNAFile
st = DNAStorage()
for f in ('phase_3/dna/storage.dna', 'phase_3.5/dna/storage_interior.dna'):
    loadDNAFile(st, f)
WALL, FLOOR = BitMask32(1), BitMask32(2)

def probe(name, np, doorName='door_origin'):
    np.reparentTo(render)
    np.flattenLight()
    trav = CollisionTraverser()
    def ray(x, y, top):
        q = CollisionHandlerQueue(); cn = CollisionNode('r'); cn.addSolid(CollisionRay(x, y, top, 0, 0, -1))
        cn.setFromCollideMask(FLOOR); cn.setIntoCollideMask(BitMask32.allOff())
        p = render.attachNewNode(cn); trav.addCollider(p, q); trav.traverse(render); trav.removeCollider(p); p.removeNode()
        if not q.getNumEntries(): return None
        q.sortEntries(); return q.getEntry(0).getSurfacePoint(render)[2]
    def blocked(a, b):
        for lift in (0.7, 1.5, 3.0):
            q = CollisionHandlerQueue(); cn = CollisionNode('s')
            cn.addSolid(CollisionCapsule(a[0], a[1], a[2] + lift, b[0], b[1], b[2] + lift, 1.2))
            cn.setFromCollideMask(WALL); cn.setIntoCollideMask(BitMask32.allOff())
            p = render.attachNewNode(cn); trav.addCollider(p, q); trav.traverse(render); trav.removeCollider(p); p.removeNode()
            if q.getNumEntries(): return True
        return False
    lo, hi = np.getTightBounds()
    top = hi[2] - 0.5
    doors = []
    for n in sorted(np.findAllMatches('**/%s*' % doorName), key=lambda n: n.getName()):
        p = n.getPos(render); h = n.getH(render)
        # the toon steps in 4 ft in front of the door (door faces out: heading h, inward = -forward)
        fx, fy = -math.sin(math.radians(h + 180)), math.cos(math.radians(h + 180))
        e = (p[0] + 4 * fx, p[1] + 4 * fy)
        z = ray(e[0], e[1], p[2] + 3)
        doors.append({'name': n.getName(), 'pos': [round(p[0], 2), round(p[1], 2), round(p[2], 2)], 'h': round(h, 1),
                      'entry': [round(e[0], 2), round(e[1], 2), round(z if z is not None else p[2], 2)]})
    npcs = []
    for n in sorted(np.findAllMatches('**/npc_origin_*'), key=lambda n: n.getName()):
        p = n.getPos(render); h = n.getH(render)
        fx, fy = -math.sin(math.radians(h)), math.cos(math.radians(h))
        s = (p[0] + 4.5 * fx, p[1] + 4.5 * fy)
        z = ray(s[0], s[1], p[2] + 3)
        npcs.append({'index': int(n.getName().split('_')[-1]), 'pos': [round(p[0], 2), round(p[1], 2), round(p[2], 2)],
                     'talk': [round(s[0], 2), round(s[1], 2), round(z if z is not None else p[2], 2)]})
    spots = []
    x = lo[0] + 2
    while x < hi[0] - 2:
        y = lo[1] + 2
        while y < hi[1] - 2:
            z = ray(x, y, top)
            if z is not None:
                spots.append((round(x, 2), round(y, 2), round(z, 2)))
            y += 3.0
        x += 3.0
    out = []
    for d in doors:
        ok = [s for s in spots if abs(s[2] - d['entry'][2]) < 0.6 and not blocked(d['entry'], s)]
        d['spots'] = ok
    for c in npcs:
        c['ok'] = [i for i, d in enumerate(doors) if not blocked(d['entry'], c['talk'])]
        c['via'] = {}
        for i, d in enumerate(doors):
            if i in c['ok']:
                continue
            cands = sorted(spots, key=lambda s: (s[0] - c['talk'][0]) ** 2 + (s[1] - c['talk'][1]) ** 2)
            for s in cands[:400]:
                if not blocked(s, c['talk']) and not blocked(d['entry'], s):
                    c['via'][i] = s
                    break
    np.removeNode()
    print(name, 'doors', [(d['name'], len(d['spots'])) for d in doors], 'npcs', [(c['index'], c['ok'], sorted(c['via'])) for c in npcs], file=sys.stderr)
    return {'doors': doors, 'npcs': npcs}

res = {}
codes = [st.getCatalogCode('TI_room', i) for i in range(st.getNumCatalogCodes('TI_room'))]
res['TI_codes'] = codes
for c in codes:
    res[c] = probe(c, st.findNode(c).copyTo(NodePath('x')))
for key, m in (('gagshop', 'phase_4/models/modules/gagShop_interior'), ('hq', 'phase_3.5/models/modules/HQ_interior'),
               ('petshop', 'phase_4/models/modules/PetShopInterior'), ('kartshop', 'phase_6/models/karting/KartShop_Interior'),
               ('toonhall', 'phase_3.5/models/modules/tt_m_ara_int_toonhall')):
    res[key] = probe(key, loader.loadModel(m))
out = sys.argv[1]
with open(out, 'w', newline='\n') as f:
    json.dump(res, f, separators=(',', ':'))
print('wrote', out, os.path.getsize(out), file=sys.stderr)
