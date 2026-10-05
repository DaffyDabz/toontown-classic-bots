"""TTBOTS P8b: bots in the Cog HQs (factories, mints, DA offices, CGC, the four boss battles).

  suits.py    believable cog-suit progress for the Brrrgh / Dreamland bots, written to the DB once
  common.py   shared plumbing: facility table, barrier acks (setBarrierReady), elevator rides, runs
  level.py    the level runner: factory cells, mint / DA office / CGC rooms, floor elevators, CGC games
  bosses.py   VP, CFO, CJ, CEO brains on top of the shared battle brain
The activities that drive them: toontown/bots/activities/coghq.py (facilities, courtyards) and
toontown/bots/activities/boss.py (boss elevators and battles).
"""
