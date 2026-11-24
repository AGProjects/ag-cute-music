"""bin/ag-cute-music --headless: the music folder shared on the LAN (sharing.py), with no window and no display:
for a NAS or a server. The folder is read (and read again every --rescan minutes, only what changed), shared on
--port and announced (Bonjour) for the AG CuteMusic instances on the LAN to browse, play and copy from.

  bin/ag-cute-music --headless [--music <folder>] [--name <name>] [--port <port>] [--rescan <minutes>]

  --music    the music folder (default: the one set in the application, else ~/Music)
  --name     the name it is listed by (default: the host's name)
  --port     default 47100 (another free one when taken)
  --rescan   minutes between two reads of the folder (default 60; 0: only at start)

As a systemd user service: systemd/ag-cute-music-share.service.
"""

import os
import signal
import sys

from PyQt6.QtCore import QCoreApplication, QTimer

from cutemusic import APP_NAME, util


def _option(name, default=None):
    arguments = sys.argv[1:]
    if name in arguments:
        index = arguments.index(name)
        if index + 1 < len(arguments):
            return arguments[index + 1]
    for argument in arguments:
        if argument.startswith(name + '='):
            return argument.split('=', 1)[1]
    return default


def main():
    application = QCoreApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    util.init()
    from cutemusic.library import Scanner, music_folder
    from cutemusic.sharing import LibraryServer, PORT
    root = os.path.abspath(os.path.expanduser(_option('--music') or music_folder()))
    if not os.path.isdir(root):
        util.log.error(f'[headless] {root} is not a folder: --music <folder>')
        return 1
    if _option('--name'):
        util.settings().setValue('sharing/name', _option('--name'))
    try:
        port = int(_option('--port', PORT))
        rescan = float(_option('--rescan', 60))
    except ValueError:
        util.log.error('[headless] --port and --rescan take numbers')
        return 2
    server = LibraryServer()
    scanner = Scanner()
    cached = scanner.cached(root)
    server.set_library(cached, root)

    def scanned(tracks):
        server.set_library(tracks, root)
        util.log.info(f'[headless] {len(tracks)} tracks shared')
    scanner.finished.connect(scanned)
    scanner.progress.connect(lambda text: None)
    if not server.start(port):
        util.log.error('[headless] cannot listen: see above')
        return 1
    util.log.info(f'[headless] pid {os.getpid()}: {root} ({len(cached)} tracks known), rescanned '
                  + (f'every {rescan:g} minutes' if rescan > 0 else 'only now'))
    scanner.scan(root)
    if rescan > 0:
        timer = QTimer(application)
        timer.timeout.connect(lambda: scanner.scan(root))
        timer.start(int(rescan * 60 * 1000))

    def stop(*args):
        util.log.info('[headless] stopping')
        server.stop()
        application.quit()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    ticks = QTimer(application)         # Python runs its signal handlers only between Qt's calls into it
    ticks.timeout.connect(lambda: None)
    ticks.start(500)
    return application.exec()
