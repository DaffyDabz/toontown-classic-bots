@echo off
title Open Toontown - Bot AI (bot toons)
cd..

rem Start this after the AI (district) server has finished booting. The bot toons log in
rem through the server like players; see "Bot settings" in README.md to change how many.

rem Read the contents of PPYTHON_PATH into %PPYTHON_PATH%:
set /P PPYTHON_PATH=<PPYTHON_PATH

:main
%PPYTHON_PATH% -m toontown.bots.BotAIStart --base-channel 402000000 ^
               --max-channels 999999 --stateserver 4002 ^
               --messagedirector-ip 127.0.0.1:7199 ^
               --eventlogger-ip 127.0.0.1:7197
goto main
