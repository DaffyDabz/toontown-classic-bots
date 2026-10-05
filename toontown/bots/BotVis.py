"""What a real player on a street can see (TTBOTS POP).

A client on a street has interest in the visgroups listed in its own visgroup's DNA 'vis' list
(TownLoader / QuietZoneState), so the toons drawn on his screen are exactly the toons standing in
those zones. The director counts a watched street by that set: bots in the zones his client has
open, not bots anywhere on the whole street.

Pure text parse of the street .dna files (visgroup "2101" [ vis [ "2101" "2102" ... ] ]), once per
street, cached. No Panda3D, no DNA storage in the bot process.
"""
import glob
import os
import re

_VIS = {}              # visgroup zone -> frozenset of visible zones (itself included)
_LOADED = set()        # street branch ids parsed
_RX = re.compile(r'visgroup\s+"(\d+)"\s*\[\s*vis\s*\[([^\]]*)\]')


def _dnaFile(streetId):
    hits = glob.glob(os.path.join('resources', 'phase_*', 'dna', '*_%d.dna' % streetId))    # the AI's own path
    return hits[0] if hits else None


def loadStreet(streetId):
    if streetId in _LOADED:
        return
    _LOADED.add(streetId)
    path = _dnaFile(streetId)
    if path is None:
        return
    with open(path, 'r', errors='replace') as f:
        text = f.read()
    for zone, vis in _RX.findall(text):
        z = int(zone)
        _VIS[z] = frozenset([z] + [int(v) for v in re.findall(r'"(\d+)"', vis)])


def visibleFrom(zoneId):
    """Zones a client standing in zoneId has open (a playground: just itself)."""
    if zoneId is None:
        return frozenset()
    if zoneId % 100 == 0:
        return frozenset([zoneId])
    loadStreet(zoneId - zoneId % 100)
    return _VIS.get(zoneId, frozenset([zoneId]))
