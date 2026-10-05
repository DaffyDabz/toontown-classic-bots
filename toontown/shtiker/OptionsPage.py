from panda3d.core import *
from . import ShtikerPage
from toontown.toontowngui import TTDialog
from direct.gui.DirectGui import *
from toontown.toonbase import TTLocalizer
from . import DisplaySettingsDialog
from direct.task import Task
from otp.speedchat import SpeedChat
from otp.speedchat import SCColorScheme
from otp.speedchat import SCStaticTextTerminal
from direct.directnotify import DirectNotifyGlobal
from enum import IntEnum
from otp.settings import ModernOptions
speedChatStyles = ((2000,
  (200 / 255.0, 60 / 255.0, 229 / 255.0),
  (200 / 255.0, 135 / 255.0, 255 / 255.0),
  (220 / 255.0, 195 / 255.0, 229 / 255.0)),
 (2001,
  (0 / 255.0, 0 / 255.0, 255 / 255.0),
  (140 / 255.0, 150 / 255.0, 235 / 255.0),
  (201 / 255.0, 215 / 255.0, 255 / 255.0)),
 (2002,
  (90 / 255.0, 175 / 255.0, 225 / 255.0),
  (120 / 255.0, 215 / 255.0, 255 / 255.0),
  (208 / 255.0, 230 / 255.0, 250 / 255.0)),
 (2003,
  (130 / 255.0, 235 / 255.0, 235 / 255.0),
  (120 / 255.0, 225 / 255.0, 225 / 255.0),
  (234 / 255.0, 255 / 255.0, 255 / 255.0)),
 (2004,
  (0 / 255.0, 200 / 255.0, 70 / 255.0),
  (0 / 255.0, 200 / 255.0, 80 / 255.0),
  (204 / 255.0, 255 / 255.0, 204 / 255.0)),
 (2005,
  (235 / 255.0, 230 / 255.0, 0 / 255.0),
  (255 / 255.0, 250 / 255.0, 100 / 255.0),
  (255 / 255.0, 250 / 255.0, 204 / 255.0)),
 (2006,
  (255 / 255.0, 153 / 255.0, 0 / 255.0),
  (229 / 255.0, 147 / 255.0, 0 / 255.0),
  (255 / 255.0, 234 / 255.0, 204 / 255.0)),
 (2007,
  (255 / 255.0, 0 / 255.0, 50 / 255.0),
  (229 / 255.0, 0 / 255.0, 50 / 255.0),
  (255 / 255.0, 204 / 255.0, 204 / 255.0)),
 (2008,
  (255 / 255.0, 153 / 255.0, 193 / 255.0),
  (240 / 255.0, 157 / 255.0, 192 / 255.0),
  (255 / 255.0, 215 / 255.0, 238 / 255.0)),
 (2009,
  (170 / 255.0, 120 / 255.0, 20 / 255.0),
  (165 / 255.0, 120 / 255.0, 50 / 255.0),
  (210 / 255.0, 200 / 255.0, 180 / 255.0)))
PageMode = IntEnum('PageMode', ('Options', 'Audio', 'Video', 'Controls', 'Camera', 'Codes'), start=0)
TabNames = {PageMode.Options: 'General',
 PageMode.Audio: 'Audio',
 PageMode.Video: 'Video',
 PageMode.Controls: 'Controls',
 PageMode.Camera: 'Camera',
 PageMode.Codes: 'Codes'}
PageTitles = {PageMode.Options: TTLocalizer.OptionsPageTitle,
 PageMode.Audio: 'Audio',
 PageMode.Video: 'Video',
 PageMode.Controls: 'Controls',
 PageMode.Camera: 'Camera',
 PageMode.Codes: TTLocalizer.CdrPageTitle}
TabSpacing = 0.28
TabTextScale = 0.05
titleHeight = 0.61
textStartHeight = 0.45
textRowHeight = 0.145
leftMargin = -0.72
buttonbase_xcoord = 0.35
buttonbase_ycoord = 0.45
button_image_scale = (0.7, 1, 1)
button_textpos = (0, -0.02)
options_text_scale = 0.052
slider_xcoord = 0.28
value_xcoord = 0.76

def makeToggleButton(parent, pos, command, text = '', scale = 1.0, extraArgs = []):
    guiButton = loader.loadModel('phase_3/models/gui/quit_button')
    button = DirectButton(parent=parent, relief=None, image=(guiButton.find('**/QuitBtn_UP'),
     guiButton.find('**/QuitBtn_DN'),
     guiButton.find('**/QuitBtn_RLVR'),
     guiButton.find('**/QuitBtn_UP')), image3_color=Vec4(0.5, 0.5, 0.5, 0.5), image_scale=button_image_scale, text=text, text3_fg=(0.5, 0.5, 0.5, 0.75), text_scale=options_text_scale, text_pos=button_textpos, pos=pos, scale=scale, command=command, extraArgs=extraArgs)
    guiButton.removeNode()
    return button


def makeArrowButton(parent, pos, command, flip = False):
    gui = loader.loadModel('phase_3.5/models/gui/friendslist_gui')
    scale = (1.0, 1.0, 1.0)
    if flip:
        scale = (-1.0, 1.0, 1.0)
    button = DirectButton(parent=parent, relief=None, image=(gui.find('**/Horiz_Arrow_UP'),
     gui.find('**/Horiz_Arrow_DN'),
     gui.find('**/Horiz_Arrow_Rllvr'),
     gui.find('**/Horiz_Arrow_UP')), image3_color=Vec4(1, 1, 1, 0.5), scale=scale, pos=pos, command=command)
    gui.removeNode()
    return button


def makeLabel(parent, row, text = '', wordwrap = 17, rowHeight = textRowHeight):
    return DirectLabel(parent=parent, relief=None, text=text, text_align=TextNode.ALeft, text_scale=options_text_scale, text_wordwrap=wordwrap, pos=(leftMargin, 0, textStartHeight - row * rowHeight))


def makeSlider(parent, z, valueRange, value, command):
    guiButton = loader.loadModel('phase_3/models/gui/quit_button')
    slider = DirectSlider(parent=parent, relief=DGG.SUNKEN, frameColor=(0.55, 0.43, 0.3, 1), borderWidth=(0.02, 0.02), frameSize=(-1, 1, -0.07, 0.07), range=valueRange, value=value, pageSize=(valueRange[1] - valueRange[0]) / 20.0, scale=0.22, pos=(slider_xcoord, 0, z + 0.015), thumb_relief=None, thumb_image=(guiButton.find('**/QuitBtn_UP'), guiButton.find('**/QuitBtn_DN'), guiButton.find('**/QuitBtn_RLVR')), thumb_image_scale=(0.45, 1, 2.6), command=command)
    guiButton.removeNode()
    return slider


def makeValueLabel(parent, z):
    return DirectLabel(parent=parent, relief=None, text='', text_align=TextNode.ACenter, text_scale=options_text_scale, pos=(value_xcoord, 0, z))


class OptionsPage(ShtikerPage.ShtikerPage):
    notify = DirectNotifyGlobal.directNotify.newCategory('OptionsPage')

    def __init__(self):
        ShtikerPage.ShtikerPage.__init__(self)

    def load(self):
        ShtikerPage.ShtikerPage.load(self)
        self.optionsTabPage = OptionsTabPage(self)
        self.optionsTabPage.hide()
        self.audioTabPage = AudioTabPage(self)
        self.audioTabPage.hide()
        self.videoTabPage = VideoTabPage(self)
        self.videoTabPage.hide()
        self.controlsTabPage = ControlsTabPage(self)
        self.controlsTabPage.hide()
        self.cameraTabPage = CameraTabPage(self)
        self.cameraTabPage.hide()
        self.codesTabPage = CodesTabPage(self)
        self.codesTabPage.hide()
        self.tabPages = {PageMode.Options: self.optionsTabPage,
         PageMode.Audio: self.audioTabPage,
         PageMode.Video: self.videoTabPage,
         PageMode.Controls: self.controlsTabPage,
         PageMode.Camera: self.cameraTabPage,
         PageMode.Codes: self.codesTabPage}
        self.title = DirectLabel(parent=self, relief=None, text=TTLocalizer.OptionsPageTitle, text_scale=0.12, pos=(0, 0, titleHeight))
        normalColor = (1, 1, 1, 1)
        clickColor = (0.8, 0.8, 0, 1)
        rolloverColor = (0.15, 0.82, 1.0, 1)
        diabledColor = (1.0, 0.98, 0.15, 1)
        gui = loader.loadModel('phase_3.5/models/gui/fishingBook')
        tabGeoms = [gui.find('**/tabs/polySurface1'), gui.find('**/tabs/polySurface2'), gui.find('**/tabs/polySurface3')]
        self.tabs = {}
        for index, mode in enumerate(PageMode):
            x = (index - (len(PageMode) - 1) / 2.0) * TabSpacing
            image = self.__makeTabImage(tabGeoms[index % len(tabGeoms)])
            self.tabs[mode] = DirectButton(parent=self, relief=None, text=TabNames[mode], text_scale=TabTextScale, text_align=TextNode.ACenter, text_pos=(0, -0.015), image=image, image_color=normalColor, image1_color=clickColor, image2_color=rolloverColor, image3_color=diabledColor, text_fg=Vec4(0.2, 0.1, 0, 1), command=self.setMode, extraArgs=[mode], pos=(x, 0, 0.77))
            image.removeNode()

        self.optionsTab = self.tabs[PageMode.Options]
        self.codesTab = self.tabs[PageMode.Codes]
        gui.removeNode()
        return

    def __makeTabImage(self, geom):
        node = NodePath('optionsTabImage')
        tab = geom.copyTo(node)
        tab.setHpr(0, 0, -90)
        tab.setScale(0.033, 0.033, 0.025)
        bounds = tab.getTightBounds(node)
        if bounds:
            center = (bounds[0] + bounds[1]) / 2.0
            tab.setPos(tab.getPos() - center)
        return node

    def enter(self):
        self.setMode(PageMode.Options, updateAnyways=1)
        ShtikerPage.ShtikerPage.enter(self)

    def exit(self):
        for page in self.tabPages.values():
            page.exit()

        ShtikerPage.ShtikerPage.exit(self)

    def unload(self):
        for page in self.tabPages.values():
            if page is not self.codesTabPage:
                page.unload()

        del self.title
        ShtikerPage.ShtikerPage.unload(self)

    def setMode(self, mode, updateAnyways = 0):
        messenger.send('wakeup')
        if not updateAnyways:
            if self.mode == mode:
                return
        if mode not in self.tabPages:
            raise Exception('OptionsPage::setMode - Invalid Mode %s' % mode)
        self.mode = mode
        self.title['text'] = PageTitles[mode]
        for otherMode, page in self.tabPages.items():
            if otherMode != mode:
                self.tabs[otherMode]['state'] = DGG.NORMAL
                page.exit()

        self.tabs[mode]['state'] = DGG.DISABLED
        self.tabPages[mode].enter()


class OptionsTabPage(DirectFrame):
    notify = DirectNotifyGlobal.directNotify.newCategory('OptionsTabPage')
    DisplaySettingsTaskName = 'save-display-settings'
    DisplaySettingsDelay = 60
    ChangeDisplaySettings = ConfigVariableBool('change-display-settings', 1).value
    ChangeDisplayAPI = ConfigVariableBool('change-display-api', 0).value

    def __init__(self, parent = aspect2d):
        self._parent = parent
        self.currentSizeIndex = None
        DirectFrame.__init__(self, parent=self._parent, relief=None, pos=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
        self.load()
        return

    def destroy(self):
        self._parent = None
        DirectFrame.destroy(self)
        return

    def load(self):
        self.displaySettingsChanged = 0
        self.settingsChanged = 0
        guiButton = loader.loadModel('phase_3/models/gui/quit_button')
        self.speed_chat_scale = 0.055
        self.Friends_Label = makeLabel(self, 0, wordwrap=16)
        self.Whispers_Label = makeLabel(self, 1, wordwrap=16)
        self.SpeedChatStyle_Label = makeLabel(self, 2, text=TTLocalizer.OptionsPageSpeedChatStyleLabel, wordwrap=10)
        self.Friends_toggleButton = makeToggleButton(self, (buttonbase_xcoord, 0.0, buttonbase_ycoord), self.__doToggleAcceptFriends)
        self.Whispers_toggleButton = makeToggleButton(self, (buttonbase_xcoord, 0.0, buttonbase_ycoord - textRowHeight), self.__doToggleAcceptWhispers)
        self.speedChatStyleLeftArrow = makeArrowButton(self, (0.25, 0, buttonbase_ycoord - textRowHeight * 2), self.__doSpeedChatStyleLeft, flip=True)
        self.speedChatStyleRightArrow = makeArrowButton(self, (0.65, 0, buttonbase_ycoord - textRowHeight * 2), self.__doSpeedChatStyleRight)
        self.speedChatStyleText = SpeedChat.SpeedChat(name='OptionsPageStyleText', structure=[2000], backgroundModelName='phase_3/models/gui/ChatPanel', guiModelName='phase_3.5/models/gui/speedChatGui')
        self.speedChatStyleText.setScale(self.speed_chat_scale)
        self.speedChatStyleText.setPos(0.37, 0, buttonbase_ycoord - textRowHeight * 2 + 0.03)
        self.speedChatStyleText.reparentTo(self, DGG.FOREGROUND_SORT_INDEX)
        self.exitButton = DirectButton(parent=self, relief=None, image=(guiButton.find('**/QuitBtn_UP'), guiButton.find('**/QuitBtn_DN'), guiButton.find('**/QuitBtn_RLVR')), image_scale=1.15, text=TTLocalizer.OptionsPageExitToontown, text_scale=options_text_scale, text_pos=button_textpos, textMayChange=0, pos=(0.45, 0, -0.6), command=self.__handleExitShowWithConfirm)
        guiButton.removeNode()
        return

    def enter(self):
        self.show()
        taskMgr.remove(self.DisplaySettingsTaskName)
        self.settingsChanged = 0
        self.__setAcceptFriendsButton()
        self.__setAcceptWhispersButton()
        self.speedChatStyleText.enter()
        self.speedChatStyleIndex = base.localAvatar.getSpeedChatStyleIndex()
        self.updateSpeedChatStyle()
        if self._parent.book.safeMode:
            self.exitButton.hide()
        else:
            self.exitButton.show()

    def exit(self):
        self.ignore('confirmDone')
        self.hide()
        if self.settingsChanged != 0:
            base.settings.writeSettings()
            self.settingsChanged = 0
        self.speedChatStyleText.exit()
        if self.displaySettingsChanged:
            taskMgr.doMethodLater(self.DisplaySettingsDelay, self.writeDisplaySettings, self.DisplaySettingsTaskName)

    def unload(self):
        self.writeDisplaySettings()
        taskMgr.remove(self.DisplaySettingsTaskName)
        self.exitButton.destroy()
        self.Friends_toggleButton.destroy()
        self.Whispers_toggleButton.destroy()
        self.speedChatStyleLeftArrow.destroy()
        self.speedChatStyleRightArrow.destroy()
        del self.exitButton
        del self.Friends_Label
        del self.Whispers_Label
        del self.SpeedChatStyle_Label
        del self.Friends_toggleButton
        del self.Whispers_toggleButton
        del self.speedChatStyleLeftArrow
        del self.speedChatStyleRightArrow
        self.speedChatStyleText.exit()
        self.speedChatStyleText.destroy()
        del self.speedChatStyleText
        self.currentSizeIndex = None
        return

    def __doToggleAcceptFriends(self):
        messenger.send('wakeup')
        if base.localAvatar.acceptingNewFriends:
            base.localAvatar.acceptingNewFriends = 0
            base.settings.updateSetting('accepting-new-friends', False)
        else:
            base.localAvatar.acceptingNewFriends = 1
            base.settings.updateSetting('accepting-new-friends', True)
        self.settingsChanged = 1
        self.__setAcceptFriendsButton()

    def __doToggleAcceptWhispers(self):
        messenger.send('wakeup')
        if base.localAvatar.acceptingNonFriendWhispers:
            base.localAvatar.acceptingNonFriendWhispers = 0
            base.settings.updateSetting('accepting-non-friend-whispers', False)
        else:
            base.localAvatar.acceptingNonFriendWhispers = 1
            base.settings.updateSetting('accepting-non-friend-whispers', True)
        self.settingsChanged = 1
        self.__setAcceptWhispersButton()

    def __setAcceptFriendsButton(self):
        if base.localAvatar.acceptingNewFriends:
            self.Friends_Label['text'] = TTLocalizer.OptionsPageFriendsEnabledLabel
            self.Friends_toggleButton['text'] = TTLocalizer.OptionsPageToggleOff
        else:
            self.Friends_Label['text'] = TTLocalizer.OptionsPageFriendsDisabledLabel
            self.Friends_toggleButton['text'] = TTLocalizer.OptionsPageToggleOn

    def __setAcceptWhispersButton(self):
        if base.localAvatar.acceptingNonFriendWhispers:
            self.Whispers_Label['text'] = TTLocalizer.OptionsPageWhisperEnabledLabel
            self.Whispers_toggleButton['text'] = TTLocalizer.OptionsPageToggleOff
        else:
            self.Whispers_Label['text'] = TTLocalizer.OptionsPageWhisperDisabledLabel
            self.Whispers_toggleButton['text'] = TTLocalizer.OptionsPageToggleOn

    def __doSpeedChatStyleLeft(self):
        if self.speedChatStyleIndex > 0:
            self.speedChatStyleIndex = self.speedChatStyleIndex - 1
            self.updateSpeedChatStyle()

    def __doSpeedChatStyleRight(self):
        if self.speedChatStyleIndex < len(speedChatStyles) - 1:
            self.speedChatStyleIndex = self.speedChatStyleIndex + 1
            self.updateSpeedChatStyle()

    def updateSpeedChatStyle(self):
        nameKey, arrowColor, rolloverColor, frameColor = speedChatStyles[self.speedChatStyleIndex]
        newSCColorScheme = SCColorScheme.SCColorScheme(arrowColor=arrowColor, rolloverColor=rolloverColor, frameColor=frameColor)
        self.speedChatStyleText.setColorScheme(newSCColorScheme)
        self.speedChatStyleText.clearMenu()
        colorName = SCStaticTextTerminal.SCStaticTextTerminal(nameKey)
        self.speedChatStyleText.append(colorName)
        self.speedChatStyleText.finalize()
        self.speedChatStyleText.setPos(0.445 - self.speedChatStyleText.getWidth() * self.speed_chat_scale / 2, 0, self.speedChatStyleText.getPos()[2])
        if self.speedChatStyleIndex > 0:
            self.speedChatStyleLeftArrow['state'] = DGG.NORMAL
        else:
            self.speedChatStyleLeftArrow['state'] = DGG.DISABLED
        if self.speedChatStyleIndex < len(speedChatStyles) - 1:
            self.speedChatStyleRightArrow['state'] = DGG.NORMAL
        else:
            self.speedChatStyleRightArrow['state'] = DGG.DISABLED
        base.localAvatar.b_setSpeedChatStyleIndex(self.speedChatStyleIndex)

    def writeDisplaySettings(self, task = None):
        if not self.displaySettingsChanged:
            return
        taskMgr.remove(self.DisplaySettingsTaskName)
        base.settings.writeSettings()
        self.displaySettingsChanged = 0
        return Task.done

    def __handleExitShowWithConfirm(self):
        self.confirm = TTDialog.TTGlobalDialog(doneEvent='confirmDone', message=TTLocalizer.OptionsPageExitConfirm, style=TTDialog.TwoChoice)
        self.confirm.show()
        self._parent.doneStatus = {'mode': 'exit',
         'exitTo': 'closeShard'}
        self.accept('confirmDone', self.__handleConfirm)

    def __handleConfirm(self):
        status = self.confirm.doneStatus
        self.ignore('confirmDone')
        self.confirm.cleanup()
        del self.confirm
        if status == 'ok':
            base.cr._userLoggingOut = True
            messenger.send(self._parent.doneEvent)


class ModernTabPage(DirectFrame):

    def __init__(self, parent = aspect2d):
        self._parent = parent
        DirectFrame.__init__(self, parent=self._parent, relief=None, pos=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
        self.settingsChanged = 0
        self.load()
        return

    def destroy(self):
        self._parent = None
        DirectFrame.destroy(self)
        return

    def load(self):
        pass

    def enter(self):
        self.show()
        self.settingsChanged = 0
        self.refresh()

    def refresh(self):
        pass

    def exit(self):
        self.hide()
        if self.settingsChanged:
            base.settings.writeSettings()
            self.settingsChanged = 0

    def unload(self):
        self.ignoreAll()
        self.destroy()

    def saveSetting(self, key, value):
        ModernOptions.setSetting(key, value, write=False)
        self.settingsChanged = 1

    def rowZ(self, row):
        return textStartHeight - row * textRowHeight


class AudioTabPage(ModernTabPage):

    def load(self):
        self.musicLabel = makeLabel(self, 0, text='Music Volume')
        self.musicSlider = makeSlider(self, self.rowZ(0), (0, 100), 100, self.__musicChanged)
        self.musicValue = makeValueLabel(self, self.rowZ(0))
        self.sfxLabel = makeLabel(self, 1, text='Sound Effects Volume')
        self.sfxSlider = makeSlider(self, self.rowZ(1), (0, 100), 100, self.__sfxChanged)
        self.sfxValue = makeValueLabel(self, self.rowZ(1))
        self.toonChatSoundsLabel = makeLabel(self, 2, wordwrap=12)
        self.toonChatSoundsButton = makeToggleButton(self, (buttonbase_xcoord, 0, buttonbase_ycoord - textRowHeight * 2), self.__doToggleToonChatSounds)
        self.hint = DirectLabel(parent=self, relief=None, text='0% turns the sound off.', text_align=TextNode.ALeft, text_scale=0.045, text_fg=(0.3, 0.2, 0.1, 1), pos=(leftMargin, 0, self.rowZ(3)))
        self.ready = 0

    def refresh(self):
        self.ready = 0
        self.musicSlider['value'] = int(round(ModernOptions.getVolume('music-volume') * 100))
        self.sfxSlider['value'] = int(round(ModernOptions.getVolume('sfx-volume') * 100))
        self.ready = 1
        self.__updateLabels()

    def __updateLabels(self):
        self.musicValue['text'] = '%d%%' % int(round(self.musicSlider['value']))
        self.sfxValue['text'] = '%d%%' % int(round(self.sfxSlider['value']))
        if base.toonChatSounds:
            self.toonChatSoundsLabel['text'] = TTLocalizer.OptionsPageToonChatSoundsOnLabel.strip()
            self.toonChatSoundsButton['text'] = TTLocalizer.OptionsPageToggleOff
        else:
            self.toonChatSoundsLabel['text'] = TTLocalizer.OptionsPageToonChatSoundsOffLabel.strip()
            self.toonChatSoundsButton['text'] = TTLocalizer.OptionsPageToggleOn
        if base.sfxActive:
            self.toonChatSoundsLabel.setColorScale(1.0, 1.0, 1.0, 1.0)
            self.toonChatSoundsButton['state'] = DGG.NORMAL
        else:
            self.toonChatSoundsLabel.setColorScale(0.5, 0.5, 0.5, 0.5)
            self.toonChatSoundsButton['state'] = DGG.DISABLED

    def __musicChanged(self):
        if not self.ready:
            return
        self.saveSetting('music-volume', round(self.musicSlider['value']) / 100.0)
        ModernOptions.applyAudio()
        self.__updateLabels()

    def __sfxChanged(self):
        if not self.ready:
            return
        self.saveSetting('sfx-volume', round(self.sfxSlider['value']) / 100.0)
        ModernOptions.applyAudio()
        self.__updateLabels()

    def __doToggleToonChatSounds(self):
        messenger.send('wakeup')
        if base.toonChatSounds:
            base.toonChatSounds = 0
            self.saveSetting('toon-chat-sounds', False)
        else:
            base.toonChatSounds = 1
            self.saveSetting('toon-chat-sounds', True)
        self.__updateLabels()


class VideoTabPage(ModernTabPage):
    UiScaleTaskName = 'options-apply-ui-scale'
    rowHeight = 0.125

    def rowZ(self, row):
        return textStartHeight - row * self.rowHeight

    def load(self):
        self.screenSizes = self.__getScreenSizes()
        self.sizeIndex = 0
        self.borderless = False
        self.resolutionLabel = makeLabel(self, 0, text='Window Size', rowHeight=self.rowHeight)
        self.resolutionLeftArrow = makeArrowButton(self, (0.14, 0, self.rowZ(0) + 0.015), self.__doSizeLeft, flip=True)
        self.resolutionRightArrow = makeArrowButton(self, (0.62, 0, self.rowZ(0) + 0.015), self.__doSizeRight)
        self.resolutionText = DirectLabel(parent=self, relief=None, text='', text_align=TextNode.ACenter, text_scale=options_text_scale, pos=(0.38, 0, self.rowZ(0)))
        self.modeLabel = makeLabel(self, 1, text='Window Mode', rowHeight=self.rowHeight)
        self.modeButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(1) + 0.015), self.__doToggleMode)
        self.applyButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(2) + 0.015), self.__doApply, text='Apply')
        self.vsyncLabel = makeLabel(self, 3, text='V-Sync (next launch)', rowHeight=self.rowHeight)
        self.vsyncButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(3) + 0.015), self.__doToggle, extraArgs=['vsync'])
        self.aaLabel = makeLabel(self, 4, text='Anti-Aliasing (next launch)', rowHeight=self.rowHeight)
        self.aaButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(4) + 0.015), self.__doToggle, extraArgs=['antialias'])
        self.fpsLabel = makeLabel(self, 5, text='FPS Counter', rowHeight=self.rowHeight)
        self.fpsButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(5) + 0.015), self.__doToggle, extraArgs=['fps-meter'])
        self.uiScaleLabel = makeLabel(self, 6, text='Interface Size', rowHeight=self.rowHeight)
        self.uiScaleSlider = makeSlider(self, self.rowZ(6), (ModernOptions.UiScaleMin * 100, ModernOptions.UiScaleMax * 100), 100, self.__uiScaleChanged)
        self.uiScaleValue = makeValueLabel(self, self.rowZ(6))
        self.ready = 0

    def __getScreenSizes(self):
        screenSizes = [(800, 600),
         (1024, 768),
         (1280, 720),
         (1280, 1024),
         (1366, 768),
         (1600, 900),
         (1920, 1080)]
        maxSize = None
        try:
            if base.pipe.getDisplayWidth() > 0:
                maxSize = (base.pipe.getDisplayWidth(), base.pipe.getDisplayHeight())
            displayInfo = base.pipe.getDisplayInformation()
            for i in range(displayInfo.getTotalDisplayModes()):
                size = (displayInfo.getDisplayModeWidth(i), displayInfo.getDisplayModeHeight(i))
                if size[0] >= 800 and size[1] >= 600 and size not in screenSizes:
                    screenSizes.append(size)

        except:
            pass

        if maxSize:
            screenSizes = [size for size in screenSizes if size[0] <= maxSize[0] and size[1] <= maxSize[1]]
            if maxSize not in screenSizes:
                screenSizes.append(maxSize)
        return sorted(screenSizes)

    def refresh(self):
        self.ready = 0
        currentSize = (base.win.getXSize(), base.win.getYSize())
        undecorated = False
        if hasattr(base.win, 'getProperties'):
            undecorated = base.win.getProperties().getUndecorated()
        self.borderless = bool(base.settings.getSetting('borderless', undecorated))
        self.sizeIndex = 0
        bestDiff = None
        for i in range(len(self.screenSizes)):
            size = self.screenSizes[i]
            diff = abs(size[0] * size[1] - currentSize[0] * currentSize[1])
            if bestDiff is None or diff < bestDiff:
                bestDiff = diff
                self.sizeIndex = i

        self.uiScaleSlider['value'] = ModernOptions.clamp(float(ModernOptions.getSetting('ui-scale')), ModernOptions.UiScaleMin, ModernOptions.UiScaleMax) * 100
        self.ready = 1
        self.__updateLabels()

    def __updateLabels(self):
        size = self.screenSizes[self.sizeIndex]
        if self.borderless:
            self.resolutionText['text'] = 'Desktop'
            self.resolutionLeftArrow.hide()
            self.resolutionRightArrow.hide()
            self.modeButton['text'] = 'Borderless'
        else:
            self.resolutionText['text'] = '%s x %s' % size
            self.resolutionLeftArrow.show()
            self.resolutionRightArrow.show()
            if self.sizeIndex > 0:
                self.resolutionLeftArrow['state'] = DGG.NORMAL
            else:
                self.resolutionLeftArrow['state'] = DGG.DISABLED
            if self.sizeIndex < len(self.screenSizes) - 1:
                self.resolutionRightArrow['state'] = DGG.NORMAL
            else:
                self.resolutionRightArrow['state'] = DGG.DISABLED
            self.modeButton['text'] = 'Windowed'
        for button, key in ((self.vsyncButton, 'vsync'), (self.aaButton, 'antialias'), (self.fpsButton, 'fps-meter')):
            if ModernOptions.getSetting(key):
                button['text'] = 'On'
            else:
                button['text'] = 'Off'

        self.uiScaleValue['text'] = '%d%%' % int(round(self.uiScaleSlider['value']))

    def __doSizeLeft(self):
        if self.sizeIndex > 0:
            self.sizeIndex -= 1
            self.__updateLabels()

    def __doSizeRight(self):
        if self.sizeIndex < len(self.screenSizes) - 1:
            self.sizeIndex += 1
            self.__updateLabels()

    def __doToggleMode(self):
        messenger.send('wakeup')
        self.borderless = not self.borderless
        self.__updateLabels()

    def __doApply(self):
        messenger.send('wakeup')
        width, height = self.screenSizes[self.sizeIndex]
        ModernOptions.applyWindow(width, height, self.borderless)
        self.__updateLabels()

    def __doToggle(self, key):
        messenger.send('wakeup')
        self.saveSetting(key, not ModernOptions.getSetting(key))
        if key == 'fps-meter':
            ModernOptions.applyFpsMeter()
        self.__updateLabels()

    def __uiScaleChanged(self):
        if not self.ready:
            return
        self.uiScaleValue['text'] = '%d%%' % int(round(self.uiScaleSlider['value']))
        taskMgr.remove(self.UiScaleTaskName)
        taskMgr.doMethodLater(0.4, self.__applyUiScale, self.UiScaleTaskName)

    def __applyUiScale(self, task):
        if self.uiScaleSlider.thumb.guiItem.isButtonDown():
            return Task.again
        scale = round(self.uiScaleSlider['value']) / 100.0
        self.saveSetting('ui-scale', scale)
        base.settings.writeSettings()
        if hasattr(base, 'uiScaler'):
            base.uiScaler.apply(scale)
        return Task.done

    def unload(self):
        taskMgr.remove(self.UiScaleTaskName)
        ModernTabPage.unload(self)


class ControlsTabPage(ModernTabPage):
    CaptureEvent = 'optionsKeybindCapture'
    rowHeight = 0.092

    def load(self):
        self.capturing = None
        self.oldButtonDownEvent = None
        self.enteredChat = 0
        self.rows = {}
        for index, (action, text) in enumerate(ModernOptions.KeybindActions):
            z = textStartHeight + 0.02 - index * self.rowHeight
            label = DirectLabel(parent=self, relief=None, text=text, text_align=TextNode.ALeft, text_scale=options_text_scale, pos=(leftMargin, 0, z))
            button = makeToggleButton(self, (buttonbase_xcoord, 0, z + 0.015), self.__startCapture, scale=0.8, extraArgs=[action])
            button['image_scale'] = (0.95, 1, 1)
            self.rows[action] = (label, button)

        bottom = textStartHeight + 0.02 - len(ModernOptions.KeybindActions) * self.rowHeight
        self.hint = DirectLabel(parent=self, relief=None, text='Arrow keys and Ctrl (jump) always work too.', text_align=TextNode.ALeft, text_scale=0.042, text_fg=(0.3, 0.2, 0.1, 1), text_wordwrap=24, pos=(leftMargin, 0, bottom - 0.02))
        self.resetButton = makeToggleButton(self, (buttonbase_xcoord, 0, bottom - 0.13), self.__doReset, text='Reset Keys', scale=1.0)

    def enter(self):
        ModernTabPage.enter(self)
        if not self.enteredChat:
            try:
                localAvatar.chatMgr.fsm.request('otherDialog')
                self.enteredChat = 1
            except:
                pass

    def refresh(self):
        binds = ModernOptions.getKeybinds()
        for action, (label, button) in self.rows.items():
            if action == self.capturing:
                button['text'] = 'Press a key'
            else:
                button['text'] = ModernOptions.getKeyLabel(binds[action])

    def exit(self):
        self.__stopCapture()
        ModernTabPage.exit(self)
        if self.enteredChat:
            self.enteredChat = 0
            try:
                localAvatar.chatMgr.fsm.request('mainMenu')
            except:
                pass

    def __startCapture(self, action):
        messenger.send('wakeup')
        self.__stopCapture()
        if not base.buttonThrowers:
            return
        self.capturing = action
        thrower = base.buttonThrowers[0].node()
        self.oldButtonDownEvent = thrower.getButtonDownEvent()
        thrower.setButtonDownEvent(self.CaptureEvent)
        self.accept(self.CaptureEvent, self.__captured)
        self.refresh()

    def __stopCapture(self):
        if self.capturing is None:
            return
        self.capturing = None
        self.ignore(self.CaptureEvent)
        base.buttonThrowers[0].node().setButtonDownEvent(self.oldButtonDownEvent or '')
        self.oldButtonDownEvent = None
        self.refresh()

    def __captured(self, key):
        key = str(key)
        if key == 'escape':
            self.__stopCapture()
            return
        key = ModernOptions.ModifierAliases.get(key, key)
        if not ModernOptions.isKeyAllowed(key):
            return
        action = self.capturing
        self.__stopCapture()
        ModernOptions.setKeybind(action, key)
        self.refresh()

    def unload(self):
        self.__stopCapture()
        ModernTabPage.unload(self)

    def __doReset(self):
        messenger.send('wakeup')
        self.__stopCapture()
        ModernOptions.resetKeybinds()
        self.refresh()


class CameraTabPage(ModernTabPage):

    def load(self):
        self.sensHLabel = makeLabel(self, 0, text='Look Speed (left/right)')
        self.sensHSlider = makeSlider(self, self.rowZ(0), (ModernOptions.SensitivityMin, ModernOptions.SensitivityMax), 1.0, self.__sensChanged)
        self.sensHValue = makeValueLabel(self, self.rowZ(0))
        self.sensVLabel = makeLabel(self, 1, text='Look Speed (up/down)')
        self.sensVSlider = makeSlider(self, self.rowZ(1), (ModernOptions.SensitivityMin, ModernOptions.SensitivityMax), 1.0, self.__sensChanged)
        self.sensVValue = makeValueLabel(self, self.rowZ(1))
        self.fovLabel = makeLabel(self, 2, text='Field of View')
        self.fovSlider = makeSlider(self, self.rowZ(2), (ModernOptions.FovMin, ModernOptions.FovMax), ModernOptions.Defaults['fov'], self.__fovChanged)
        self.fovValue = makeValueLabel(self, self.rowZ(2))
        self.modeLabel = makeLabel(self, 3, text='Right-Click Look')
        self.modeButton = makeToggleButton(self, (buttonbase_xcoord, 0, self.rowZ(3) + 0.015), self.__doToggleMode)
        self.hint = DirectLabel(parent=self, relief=None, text='Right mouse button turns the camera around your toon. The scroll wheel zooms. The camera swings back when you let go or start walking.', text_align=TextNode.ALeft, text_scale=0.042, text_fg=(0.3, 0.2, 0.1, 1), text_wordwrap=34, pos=(leftMargin, 0, self.rowZ(4) + 0.02))
        self.ready = 0

    def refresh(self):
        self.ready = 0
        sensH, sensV = ModernOptions.getCameraSensitivity()
        self.sensHSlider['value'] = sensH
        self.sensVSlider['value'] = sensV
        self.fovSlider['value'] = ModernOptions.getFov()
        self.ready = 1
        self.__updateLabels()

    def __updateLabels(self):
        self.sensHValue['text'] = 'x%.1f' % self.sensHSlider['value']
        self.sensVValue['text'] = 'x%.1f' % self.sensVSlider['value']
        self.fovValue['text'] = '%d' % int(round(self.fovSlider['value']))
        if ModernOptions.getMouseLookMode() == ModernOptions.MouseLookToggle:
            self.modeButton['text'] = 'Toggle'
        else:
            self.modeButton['text'] = 'Hold'

    def __sensChanged(self):
        if not self.ready:
            return
        self.saveSetting('cam-sens-h', round(self.sensHSlider['value'], 1))
        self.saveSetting('cam-sens-v', round(self.sensVSlider['value'], 1))
        self.__updateLabels()

    def __fovChanged(self):
        if not self.ready:
            return
        fov = int(round(self.fovSlider['value']))
        self.saveSetting('fov', fov)
        ModernOptions.applyFov(fov)
        self.__updateLabels()

    def __doToggleMode(self):
        messenger.send('wakeup')
        if ModernOptions.getMouseLookMode() == ModernOptions.MouseLookToggle:
            self.saveSetting('mouselook-mode', ModernOptions.MouseLookHold)
        else:
            self.saveSetting('mouselook-mode', ModernOptions.MouseLookToggle)
        self.__updateLabels()


class CodesTabPage(DirectFrame):
    notify = DirectNotifyGlobal.directNotify.newCategory('CodesTabPage')

    def __init__(self, parent = aspect2d):
        self._parent = parent
        DirectFrame.__init__(self, parent=self._parent, relief=None, pos=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
        self.load()
        return

    def destroy(self):
        self._parent = None
        DirectFrame.destroy(self)
        return

    def load(self):
        cdrGui = loader.loadModel('phase_3.5/models/gui/tt_m_gui_sbk_codeRedemptionGui')
        instructionGui = cdrGui.find('**/tt_t_gui_sbk_cdrPresent')
        flippyGui = cdrGui.find('**/tt_t_gui_sbk_cdrFlippy')
        codeBoxGui = cdrGui.find('**/tt_t_gui_sbk_cdrCodeBox')
        self.resultPanelSuccessGui = cdrGui.find('**/tt_t_gui_sbk_cdrResultPanel_success')
        self.resultPanelFailureGui = cdrGui.find('**/tt_t_gui_sbk_cdrResultPanel_failure')
        self.resultPanelErrorGui = cdrGui.find('**/tt_t_gui_sbk_cdrResultPanel_error')
        self.successSfx = base.loader.loadSfx('phase_3.5/audio/sfx/tt_s_gui_sbk_cdrSuccess.ogg')
        self.failureSfx = base.loader.loadSfx('phase_3.5/audio/sfx/tt_s_gui_sbk_cdrFailure.ogg')
        self.instructionPanel = DirectFrame(parent=self, relief=None, image=instructionGui, image_scale=0.8, text=TTLocalizer.CdrInstructions, text_pos=TTLocalizer.OPCodesInstructionPanelTextPos, text_align=TextNode.ACenter, text_scale=TTLocalizer.OPCodesResultPanelTextScale, text_wordwrap=TTLocalizer.OPCodesInstructionPanelTextWordWrap, pos=(-0.429, 0, -0.05))
        self.codeBox = DirectFrame(parent=self, relief=None, image=codeBoxGui, pos=(0.433, 0, 0.35))
        self.flippyFrame = DirectFrame(parent=self, relief=None, image=flippyGui, pos=(0.44, 0, -0.353))
        self.codeInput = DirectEntry(parent=self.codeBox, relief=DGG.GROOVE, scale=0.08, pos=(-0.33, 0, -0.006), borderWidth=(0.05, 0.05), frameColor=((1, 1, 1, 1), (1, 1, 1, 1), (0.5, 0.5, 0.5, 0.5)), state=DGG.NORMAL, text_align=TextNode.ALeft, text_scale=TTLocalizer.OPCodesInputTextScale, width=10.5, numLines=1, focus=1, backgroundFocus=0, cursorKeys=1, text_fg=(0, 0, 0, 1), suppressMouse=1, autoCapitalize=0, command=self.__submitCode)
        submitButtonGui = loader.loadModel('phase_3/models/gui/quit_button')
        self.submitButton = DirectButton(parent=self, relief=None, image=(submitButtonGui.find('**/QuitBtn_UP'),
         submitButtonGui.find('**/QuitBtn_DN'),
         submitButtonGui.find('**/QuitBtn_RLVR'),
         submitButtonGui.find('**/QuitBtn_UP')), image3_color=Vec4(0.5, 0.5, 0.5, 0.5), image_scale=1.15, state=DGG.NORMAL, text=TTLocalizer.NameShopSubmitButton, text_scale=TTLocalizer.OPCodesSubmitTextScale, text_align=TextNode.ACenter, text_pos=TTLocalizer.OPCodesSubmitTextPos, text3_fg=(0.5, 0.5, 0.5, 0.75), textMayChange=0, pos=(0.45, 0.0, 0.0896), command=self.__submitCode)
        self.resultPanel = DirectFrame(parent=self, relief=None, image=self.resultPanelSuccessGui, text='', text_pos=TTLocalizer.OPCodesResultPanelTextPos, text_align=TextNode.ACenter, text_scale=TTLocalizer.OPCodesResultPanelTextScale, text_wordwrap=TTLocalizer.OPCodesResultPanelTextWordWrap, pos=(-0.42, 0, -0.0567))
        self.resultPanel.hide()
        closeButtonGui = loader.loadModel('phase_3/models/gui/dialog_box_buttons_gui')
        self.closeButton = DirectButton(parent=self.resultPanel, pos=(0.296, 0, -0.466), relief=None, state=DGG.NORMAL, image=(closeButtonGui.find('**/CloseBtn_UP'), closeButtonGui.find('**/CloseBtn_DN'), closeButtonGui.find('**/CloseBtn_Rllvr')), image_scale=(1, 1, 1), command=self.__hideResultPanel)
        closeButtonGui.removeNode()
        cdrGui.removeNode()
        submitButtonGui.removeNode()
        return

    def enter(self):
        self.show()
        localAvatar.chatMgr.fsm.request('otherDialog')
        self.codeInput['focus'] = 1
        self.codeInput.enterText('')
        self.__enableCodeEntry()

    def exit(self):
        self.resultPanel.hide()
        self.hide()
        localAvatar.chatMgr.fsm.request('mainMenu')

    def unload(self):
        self.instructionPanel.destroy()
        self.instructionPanel = None
        self.codeBox.destroy()
        self.codeBox = None
        self.flippyFrame.destroy()
        self.flippyFrame = None
        self.codeInput.destroy()
        self.codeInput = None
        self.submitButton.destroy()
        self.submitButton = None
        self.resultPanel.destroy()
        self.resultPanel = None
        self.closeButton.destroy()
        self.closeButton = None
        del self.successSfx
        del self.failureSfx
        return

    def __submitCode(self, input = None):
        if input == None:
            input = self.codeInput.get()
        self.codeInput['focus'] = 1
        if input == '':
            return
        messenger.send('wakeup')
        if hasattr(base, 'codeRedemptionMgr'):
            base.codeRedemptionMgr.redeemCode(input, self.__getCodeResult)
        self.codeInput.enterText('')
        self.__disableCodeEntry()
        return

    def __getCodeResult(self, result, awardMgrResult):
        self.notify.debug('result = %s' % result)
        self.notify.debug('awardMgrResult = %s' % awardMgrResult)
        self.__enableCodeEntry()
        if result == 0:
            self.resultPanel['image'] = self.resultPanelSuccessGui
            self.resultPanel['text'] = TTLocalizer.CdrResultSuccess
        elif result == 1 or result == 3:
            self.resultPanel['image'] = self.resultPanelFailureGui
            self.resultPanel['text'] = TTLocalizer.CdrResultInvalidCode
        elif result == 2:
            self.resultPanel['image'] = self.resultPanelFailureGui
            self.resultPanel['text'] = TTLocalizer.CdrResultExpiredCode
        elif result == 4:
            self.resultPanel['image'] = self.resultPanelErrorGui
            if awardMgrResult == 0:
                self.resultPanel['text'] = TTLocalizer.CdrResultSuccess
            elif awardMgrResult == 1 or awardMgrResult == 2 or awardMgrResult == 15 or awardMgrResult == 16:
                self.resultPanel['text'] = TTLocalizer.CdrResultUnknownError
            elif awardMgrResult == 3 or awardMgrResult == 4:
                self.resultPanel['text'] = TTLocalizer.CdrResultMailboxFull
            elif awardMgrResult == 5 or awardMgrResult == 10:
                self.resultPanel['text'] = TTLocalizer.CdrResultAlreadyInMailbox
            elif awardMgrResult == 6 or awardMgrResult == 7 or awardMgrResult == 11:
                self.resultPanel['text'] = TTLocalizer.CdrResultAlreadyInQueue
            elif awardMgrResult == 8:
                self.resultPanel['text'] = TTLocalizer.CdrResultAlreadyInCloset
            elif awardMgrResult == 9:
                self.resultPanel['text'] = TTLocalizer.CdrResultAlreadyBeingWorn
            elif awardMgrResult == 12 or awardMgrResult == 13 or awardMgrResult == 14:
                self.resultPanel['text'] = TTLocalizer.CdrResultAlreadyReceived
        elif result == 5:
            self.resultPanel['text'] = TTLocalizer.CdrResultTooManyFails
            self.__disableCodeEntry()
        elif result == 6:
            self.resultPanel['text'] = TTLocalizer.CdrResultServiceUnavailable
            self.__disableCodeEntry()
        if result == 0:
            self.successSfx.play()
        else:
            self.failureSfx.play()
        self.resultPanel.show()

    def __hideResultPanel(self):
        self.resultPanel.hide()

    def __disableCodeEntry(self):
        self.codeInput['state'] = DGG.DISABLED
        self.submitButton['state'] = DGG.DISABLED

    def __enableCodeEntry(self):
        self.codeInput['state'] = DGG.NORMAL
        self.codeInput['focus'] = 1
        self.submitButton['state'] = DGG.NORMAL
