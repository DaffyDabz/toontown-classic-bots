"""PROGRESSION W2: bot-to-bot friendship for a FriendQuest ("make a friend"), the client's own messages.

  makeFriend(bot, other)   the requester does what the client's FriendInviter does: FriendManager.friendQuery(otherId)
                           (doId 4501, FriendManagerAI on the main AI). The other bot's client side is P1's
                           (BotToon.on_inviteeFriendQuery: inviteeFriendConsidering(1), then inviteeFriendResponse(1)
                           2-5 s later); the AI then makes them friends (extendFriendsList + setFriendsList to both
                           owners, TTFriendsManager.friendsMade). Both say a SpeedChat line: the asker 507 "Please be my
                           friend!", the other 1 "Yes" as it clicks Yes, then a 'Great!' / 'Cool!' now and then.
  Returns True when the request went out (the result arrives later: friendResponse to the asker, setFriendsList).
"""
import random

from direct.directnotify import DirectNotifyGlobal

from otp.otpbase import OTPGlobals

notify = DirectNotifyGlobal.directNotify.newCategory('BotSocial')
ASK = 507                  # 'Please be my friend!'
YES = 1                    # 'Yes'
AFTER = (309, 303, 100)    # 'Great!', 'Cool!', 'Hi!'
PENDING = {}               # asker avId -> (other avId, time)
STATS = {'asked': 0, 'refused_check': 0}


def canFriend(bot, other):
    if bot is other or other is None:
        return False
    if bot.state != 'present' or getattr(other, 'state', None) != 'present':
        return False
    if other.zoneId != bot.zoneId:
        return False
    if other.avId in bot.friendIds() or bot.avId in other.friendIds():
        return False
    if len(bot.friendIds()) >= OTPGlobals.MaxFriends or len(other.friendIds()) >= OTPGlobals.MaxFriends:
        return False
    p = PENDING.get(bot.avId)
    if p is not None and globalClock.getRealTime() - p[1] < 95.0:     # FriendManagerAI keeps one invite per toon (90 s)
        return False
    return True


def _say(bot, msgId):
    try:
        from toontown.bots.activities import lifekit as kit
        kit.say(bot, msgId, force=True, answer=False)
    except Exception:
        bot.say(msgId)


def makeFriend(bot, other, say=True):
    if not canFriend(bot, other):
        STATS['refused_check'] += 1
        return False
    bot.faceTo(other.pos)
    if say:
        _say(bot, ASK)
    bot.friendMgr('friendQuery', [other.avId])
    PENDING[bot.avId] = (other.avId, globalClock.getRealTime())
    STATS['asked'] += 1
    notify.info('[TTBOTS-W2] friendQuery: %s asks %s (friends %d / %d)' % (
        bot.avId, other.avId, len(bot.friendIds()), len(other.friendIds())))

    def answer(task, bot=bot, other=other):
        # the invitee reads the panel and clicks Yes (BotToon answers 2-5 s after the query): say it as it does
        if other.state == 'present' and other.zoneId == bot.zoneId:
            _say(other, YES)
        return task.done

    def after(task, bot=bot, other=other):
        PENDING.pop(bot.avId, None)
        if bot.state == 'present' and other.avId in bot.friendIds() and random.random() < 0.6:
            _say(bot, random.choice(AFTER))
        return task.done

    taskMgr.doMethodLater(random.uniform(1.8, 2.6), answer, 'w2-friend-yes-%d' % bot.avId)
    taskMgr.doMethodLater(random.uniform(7.0, 9.0), after, 'w2-friend-after-%d' % bot.avId)
    return True
