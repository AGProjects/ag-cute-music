"""The library's Trash: songs (and whole albums) moved out of the library into <music folder>/.ag-cute-music-trash,
in the same <artist>/<album>/<file> layout (on the same disk: a rename), to put back by hand if need be. A dot
folder: the scanner and the playlists leave it out. File -> Empty Trash deletes what is in it, for good.

A song's .lrc or .txt of the same name goes with it; an album's folder left with no songs in it goes too, its
images with it (cover.jpg and the like).
"""

import os
import shutil

from cutemusic.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS
from cutemusic.util import log


__all__ = ['trash_folder', 'move_to_trash', 'trash_contents', 'empty_trash']


FOLDER = '.ag-cute-music-trash'
JUNK = ('.DS_Store', 'Thumbs.db', 'desktop.ini')


def trash_folder(root):
    return os.path.join(root, FOLDER)


def _free(path):
    """path, or "name (2).ext" and so on when taken: nothing in the Trash is overwritten."""
    if not os.path.lexists(path):
        return path
    base, extension = os.path.splitext(path)
    number = 2
    while os.path.lexists(f'{base} ({number}){extension}'):
        number += 1
    return f'{base} ({number}){extension}'


def _move(path, root, errors):
    relative = os.path.relpath(path, root)
    if relative.startswith('..') or relative.split(os.sep)[0] == FOLDER:
        errors.append(f'{path}: not in the music folder')
        return False
    target = _free(os.path.join(trash_folder(root), relative))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(path, target)
    except OSError as e:
        errors.append(f'{path}: {e}')
        return False
    return True


def move_to_trash(paths, root):
    """The songs at paths (in root, the music folder) moved into its Trash: (how many, [errors])."""
    errors, moved, folders = [], 0, set()
    for path in paths:
        if not os.path.exists(path):
            continue
        if _move(path, root, errors):
            moved += 1
            folders.add(os.path.dirname(path))
            base = os.path.splitext(path)[0]
            for extension in ('.lrc', '.LRC', '.txt'):
                if os.path.exists(base + extension):
                    _move(base + extension, root, errors)
    # the folders left without songs: their images to the Trash too, then gone (up to the music folder, not it)
    for folder in sorted(folders, key=len, reverse=True):
        while folder != root and folder.startswith(root + os.sep):
            try:
                names = os.listdir(folder)
            except OSError:
                break
            if any(os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS for name in names) or \
                    any(os.path.isdir(os.path.join(folder, name)) for name in names):
                break
            for name in names:
                if name in JUNK:
                    try:
                        os.remove(os.path.join(folder, name))
                    except OSError:
                        pass
                elif name.lower().endswith(IMAGE_EXTENSIONS) or name.lower().endswith(('.lrc', '.txt', '.cue', '.log', '.m3u', '.m3u8', '.nfo')):
                    _move(os.path.join(folder, name), root, errors)
            try:
                os.rmdir(folder)
            except OSError:
                break       # something else in it: left as it is
            folder = os.path.dirname(folder)
    for error in errors:
        log.warning(f'[trash] {error}')
    log.info(f'[trash] {moved} songs moved to {trash_folder(root)}')
    return moved, errors


def trash_contents(root):
    """(files, bytes) in the Trash."""
    files = size = 0
    for directory, folders, names in os.walk(trash_folder(root)):
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(directory, name))
            except OSError:
                pass
    return files, size


def empty_trash(root):
    """What is in the Trash deleted. None, or the error."""
    folder = trash_folder(root)
    if not os.path.isdir(folder):
        return None
    try:
        shutil.rmtree(folder)
    except OSError as e:
        log.warning(f'[trash] cannot empty {folder}: {e}')
        return str(e)
    log.info(f'[trash] {folder} emptied')
    return None
