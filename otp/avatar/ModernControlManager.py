from direct.controls import ControlManager
from direct.showbase.InputStateGlobal import inputState
from direct.showbase.DirectObject import DirectObject
from direct.directnotify import DirectNotifyGlobal
from otp.otpbase import OTPGlobals
from otp.settings import ModernOptions
KeybindSource = 'Keybinds'
ModifierPatterns = ('%s', 'control-%s', 'shift-%s', 'alt-%s', 'shift-control-%s', 'control-alt-%s', 'shift-alt-%s')
MoveActions = ('forward', 'reverse', 'turnLeft', 'turnRight', 'jump')
SpeedActions = ('run', 'slow')
KeybindForwardEvent = 'keybindForward'

class ModernControlManager(ControlManager.ControlManager):
    notify = DirectNotifyGlobal.directNotify.newCategory('ModernControlManager')
    RunMultiplier = 1.5
    SlowMultiplier = 0.4

    def __init__(self, enable = True, passMessagesThrough = False):
        self.keyListener = DirectObject()
        self.eventListener = DirectObject()
        self.bindingsActive = False
        self.typingSuspended = False
        self.heldActions = set()
        self.baseSpeeds = None
        ControlManager.ControlManager.__init__(self, enable, passMessagesThrough)
        self.eventListener.accept(ModernOptions.ChatTypingStartEvent, self.suspendBindings)
        self.eventListener.accept(ModernOptions.ChatTypingStopEvent, self.resumeBindings)
        self.eventListener.accept(ModernOptions.KeybindsChangedEvent, self.reloadBindings)

    def delete(self):
        self.eventListener.ignoreAll()
        self.__unbindKeys()
        ControlManager.ControlManager.delete(self)

    def enable(self):
        if self.isEnabled:
            return
        ControlManager.ControlManager.enable(self)
        self.__bindKeys()

    def disable(self):
        self.__unbindKeys()
        ControlManager.ControlManager.disable(self)

    def setSpeeds(self, forwardSpeed, jumpForce, reverseSpeed, rotateSpeed, strafeLeft = 0, strafeRight = 0):
        self.baseSpeeds = (forwardSpeed, jumpForce, reverseSpeed, rotateSpeed)
        self.__applySpeeds()

    def suspendBindings(self):
        self.typingSuspended = True
        self.__unbindKeys()

    def resumeBindings(self):
        if not self.typingSuspended:
            return
        self.typingSuspended = False
        self.__bindKeys()

    def reloadBindings(self):
        self.__unbindKeys()
        self.__bindKeys()

    def __bindKeys(self):
        if self.bindingsActive or not self.isEnabled or self.typingSuspended:
            return
        self.bindingsActive = True
        binds = ModernOptions.getKeybinds()
        for action in MoveActions + SpeedActions:
            key = binds.get(action)
            if not key:
                continue
            for pattern in ModifierPatterns:
                self.keyListener.accept(pattern % key, self.__keyDown, [action])

            self.keyListener.accept(key + '-up', self.__keyUp, [action])

    def __unbindKeys(self):
        self.keyListener.ignoreAll()
        self.bindingsActive = False
        for action in list(self.heldActions):
            self.__keyUp(action)

    def __keyDown(self, action):
        if self.typingSuspended or action in self.heldActions:
            return
        self.heldActions.add(action)
        if action in MoveActions:
            inputState.set(action, True, inputSource=KeybindSource)
            if action == 'forward':
                messenger.send(KeybindForwardEvent)
        else:
            self.__applySpeeds()

    def __keyUp(self, action):
        if action not in self.heldActions:
            return
        self.heldActions.discard(action)
        if action in MoveActions:
            inputState.set(action, False, inputSource=KeybindSource)
            if action == 'forward':
                messenger.send(KeybindForwardEvent + '-up')
        else:
            self.__applySpeeds()

    def getSpeedMultiplier(self):
        if 'slow' in self.heldActions:
            return self.SlowMultiplier
        if 'run' in self.heldActions:
            return self.RunMultiplier
        return 1.0

    def __applySpeeds(self):
        speeds = self.baseSpeeds
        if speeds is None:
            speeds = (OTPGlobals.ToonForwardSpeed, OTPGlobals.ToonJumpForce, OTPGlobals.ToonReverseSpeed, OTPGlobals.ToonRotateSpeed)
            if self.getSpeedMultiplier() == 1.0:
                return
        forwardSpeed, jumpForce, reverseSpeed, rotateSpeed = speeds
        multiplier = self.getSpeedMultiplier()
        ControlManager.ControlManager.setSpeeds(self, forwardSpeed * multiplier, jumpForce, reverseSpeed * multiplier, rotateSpeed)
