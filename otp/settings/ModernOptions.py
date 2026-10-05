from panda3d.core import *
from direct.showbase.DirectObject import DirectObject
from direct.directnotify import DirectNotifyGlobal
from otp.otpbase import OTPGlobals
notify = DirectNotifyGlobal.directNotify.newCategory('ModernOptions')
DefaultKeybinds = {'forward': 'w',
 'reverse': 's',
 'turnLeft': 'a',
 'turnRight': 'd',
 'jump': 'space',
 'run': 'shift',
 'slow': 'v',
 'chat': 't'}
KeybindActions = (('forward', 'Forward'),
 ('reverse', 'Back'),
 ('turnLeft', 'Turn Left'),
 ('turnRight', 'Turn Right'),
 ('jump', 'Jump'),
 ('run', 'Run (hold)'),
 ('slow', 'Slow Walk (hold)'),
 ('chat', 'Open Chat'))
KeybindsChangedEvent = 'keybindsChanged'
ChatTypingStartEvent = 'chatTypingStart'
ChatTypingStopEvent = 'chatTypingStop'
MouseLookHold = 'hold'
MouseLookToggle = 'toggle'
FovMin = 50
FovMax = 100
UiScaleMin = 0.75
UiScaleMax = 1.1
SensitivityMin = 0.2
SensitivityMax = 3.0
Defaults = {'cam-sens-h': 1.0,
 'cam-sens-v': 1.0,
 'fov': OTPGlobals.DefaultCameraFov,
 'mouselook-mode': MouseLookHold,
 'vsync': True,
 'antialias': False,
 'fps-meter': False,
 'ui-scale': 1.0,
 'music-volume': 1.0,
 'sfx-volume': 1.0}
KeyNames = {'space': 'Space',
 'shift': 'Shift',
 'control': 'Ctrl',
 'alt': 'Alt',
 'enter': 'Enter',
 'tab': 'Tab',
 'backspace': 'Backspace',
 'arrow_up': 'Up',
 'arrow_down': 'Down',
 'arrow_left': 'Left',
 'arrow_right': 'Right',
 'caps_lock': 'Caps Lock',
 'lshift': 'Shift',
 'rshift': 'Shift',
 'lcontrol': 'Ctrl',
 'rcontrol': 'Ctrl',
 'lalt': 'Alt',
 'ralt': 'Alt'}
ModifierAliases = {'lshift': 'shift',
 'rshift': 'shift',
 'lcontrol': 'control',
 'rcontrol': 'control',
 'lalt': 'alt',
 'ralt': 'alt'}
RejectedKeys = ('escape', 'mouse1', 'mouse2', 'mouse3', 'mouse4', 'mouse5', 'wheel_up', 'wheel_down', 'wheel_left', 'wheel_right', 'control', 'lcontrol', 'rcontrol', 'arrow_up', 'arrow_down', 'arrow_left', 'arrow_right')

_settings = None

def registerSettings(settings):
    global _settings
    _settings = settings


def getSettings():
    # ToonBase registers its Settings before builtins.base exists.
    if _settings is not None:
        return _settings
    try:
        return base.settings
    except:
        return None


def getSetting(key):
    settings = getSettings()
    default = Defaults.get(key)
    if settings is None:
        return default
    if key == 'music-volume' or key == 'sfx-volume':
        return getVolume(key)
    return settings.getSetting(key, default)


def setSetting(key, value, write = True):
    settings = getSettings()
    if settings is None:
        return
    settings.updateSetting(key, value)
    if key == 'music-volume':
        settings.updateSetting('music', value > 0)
    elif key == 'sfx-volume':
        settings.updateSetting('sfx', value > 0)
    if write:
        settings.writeSettings()


def getVolume(key):
    settings = getSettings()
    if settings is None:
        return 1.0
    value = settings.getSetting(key, None)
    oldKey = 'music'
    if key == 'sfx-volume':
        oldKey = 'sfx'
    if value is None:
        if settings.getSetting(oldKey, True):
            value = 1.0
        else:
            value = 0.0
    elif not settings.getSetting(oldKey, True):
        value = 0.0
    try:
        value = float(value)
    except:
        value = 1.0
    return max(0.0, min(1.0, value))


def getKeybinds():
    binds = dict(DefaultKeybinds)
    settings = getSettings()
    if settings is not None:
        saved = settings.getSetting('keybinds', None)
        if isinstance(saved, dict):
            for action, key in saved.items():
                if action in binds and key:
                    binds[action] = key

    return binds


def getKeybind(action):
    return getKeybinds().get(action)


def setKeybind(action, key):
    key = ModifierAliases.get(key, key)
    binds = getKeybinds()
    oldKey = binds.get(action)
    for other, otherKey in binds.items():
        if other != action and otherKey == key:
            binds[other] = oldKey

    binds[action] = key
    setSetting('keybinds', binds)
    messenger.send(KeybindsChangedEvent)
    return binds


def resetKeybinds():
    setSetting('keybinds', dict(DefaultKeybinds))
    messenger.send(KeybindsChangedEvent)


def isKeyAllowed(key):
    if not key or key in RejectedKeys:
        return False
    if key.startswith('mouse') or key.startswith('wheel') or key.startswith('joystick') or key.startswith('gamepad'):
        return False
    if '-' in key:
        return False
    return True


def getKeyLabel(key):
    if key in KeyNames:
        return KeyNames[key]
    if len(key) == 1:
        return key.upper()
    return key.replace('_', ' ').title()


def clamp(value, low, high):
    return max(low, min(high, value))


def getFov():
    try:
        fov = float(getSetting('fov'))
    except:
        fov = Defaults['fov']
    return clamp(fov, FovMin, FovMax)


def applyFov(fov = None, live = True):
    if fov is None:
        fov = getFov()
    OTPGlobals.DefaultCameraFov = fov
    try:
        from toontown.toonbase import ToontownGlobals
        ToontownGlobals.DefaultCameraFov = fov
    except:
        pass

    if not live:
        return
    av = getattr(base, 'localAvatar', None)
    if av is not None and hasattr(av, 'setCameraFov'):
        av.setCameraFov(fov)
    elif getattr(base, 'camLens', None):
        base.camLens.setFov(fov)


def getCameraSensitivity():
    h = clamp(float(getSetting('cam-sens-h')), SensitivityMin, SensitivityMax)
    v = clamp(float(getSetting('cam-sens-v')), SensitivityMin, SensitivityMax)
    return (h, v)


def getMouseLookMode():
    mode = getSetting('mouselook-mode')
    if mode not in (MouseLookHold, MouseLookToggle):
        mode = MouseLookHold
    return mode


def applyAudio():
    musicVolume = getVolume('music-volume')
    sfxVolume = getVolume('sfx-volume')
    if musicVolume > 0:
        if not base.musicActive:
            base.enableMusic(1)
    elif base.musicActive:
        base.enableMusic(0)
    if base.musicManager:
        base.musicManager.setVolume(musicVolume)
    if sfxVolume > 0:
        if not base.sfxActive:
            base.enableSoundEffects(1)
    elif base.sfxActive:
        base.enableSoundEffects(0)
    for manager in base.sfxManagerList or []:
        if manager:
            manager.setVolume(sfxVolume)


def getPrcData():
    lines = []
    if getSetting('vsync'):
        lines.append('sync-video #t')
    else:
        lines.append('sync-video #f')
    if getSetting('antialias'):
        lines.append('framebuffer-multisample #t')
        lines.append('multisamples 4')
    return '\n'.join(lines)


def getStartupWindow(desktopSize):
    settings = getSettings()
    res = settings.getSetting('resolution', None)
    borderless = settings.getSetting('borderless', None)
    if not settings.getSetting('windowed-mode', True):
        borderless = True
    if res is None:
        res = (800, 600)
        if borderless is None:
            borderless = True
    if borderless and desktopSize:
        res = desktopSize
    return (tuple(res), bool(borderless))


def applyWindow(width, height, borderless):
    if not base.win:
        return False
    pipe = base.pipe
    if borderless and pipe and pipe.getDisplayWidth() > 0:
        width = pipe.getDisplayWidth()
        height = pipe.getDisplayHeight()
    props = WindowProperties()
    props.setFullscreen(False)
    props.setSize(width, height)
    props.setUndecorated(bool(borderless))
    if borderless:
        props.setOrigin(0, 0)
    elif pipe and pipe.getDisplayWidth() > 0:
        props.setOrigin(max(0, (pipe.getDisplayWidth() - width) // 2), max(0, (pipe.getDisplayHeight() - height) // 2))
    if base.win.getProperties().getFullscreen():
        if not base.openMainWindow(props=props, keepCamera=True):
            return False
        base.graphicsEngine.openWindows()
        base.disableShowbaseMouse()
        try:
            from panda3d.otp import NametagGlobals
            NametagGlobals.setCamera(base.cam)
            NametagGlobals.setMouseWatcher(base.mouseWatcherNode)
        except:
            pass

    else:
        base.win.requestProperties(props)
    settings = getSettings()
    settings.updateSetting('resolution', (width, height))
    settings.updateSetting('windowed-mode', True)
    settings.updateSetting('borderless', bool(borderless))
    settings.writeSettings()
    return True


class UiScaler(DirectObject):

    def __init__(self):
        DirectObject.__init__(self)
        self.scale = 1.0
        self.accept('window-event', self.__windowEvent)
        self.accept('aspectRatioChanged', self.apply)

    def __windowEvent(self, win):
        if win == base.win:
            self.apply()

    def apply(self, scale = None):
        if scale is None:
            try:
                scale = float(getSetting('ui-scale'))
            except:
                scale = 1.0
        scale = clamp(scale, UiScaleMin, UiScaleMax)
        self.scale = scale
        ratio = base.getAspectRatio()
        if ratio < 1:
            base.aspect2d.setScale(scale, ratio, ratio * scale)
        else:
            base.aspect2d.setScale(scale / ratio, 1.0, scale)
        top = 1.0
        bottom = -1.0
        left = -ratio
        right = ratio
        if ratio < 1:
            top = 1.0 / ratio
            bottom = -1.0 / ratio
            left = -1.0
            right = 1.0
        anchors = (('a2dTopCenter', 0, top),
         ('a2dBottomCenter', 0, bottom),
         ('a2dLeftCenter', left, 0),
         ('a2dRightCenter', right, 0),
         ('a2dTopLeft', left, top),
         ('a2dTopRight', right, top),
         ('a2dBottomLeft', left, bottom),
         ('a2dBottomRight', right, bottom))
        for name, x, z in anchors:
            for suffix in ('', 'Ns'):
                node = getattr(base, name + suffix, None)
                if node is not None and not node.isEmpty():
                    node.setPos(x / scale, 0, z / scale)


def applyFpsMeter():
    base.setFrameRateMeter(bool(getSetting('fps-meter')))


def applyAntialias():
    if getSetting('antialias'):
        render.setAntialias(AntialiasAttrib.MMultisample)


def applyAll():
    applyFov(live=False)
    applyAudio()
    applyFpsMeter()
    applyAntialias()
    if not hasattr(base, 'uiScaler'):
        base.uiScaler = UiScaler()
    base.uiScaler.apply()
