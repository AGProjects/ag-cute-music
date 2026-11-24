# Negatives

A negative is a song without its singer, for karaoke: the instrumental stem, made offline by a neural source
separation model. AG CuteMusic makes them in the background, shows them in a Negatives tab of their own, plays
them with the lyrics, switches between a song and its negative from the same point, and serves them on the LAN
through a web page where others send songs with a token of their own.

Code: `cutemusic/negatives.py` (the queue and the separator), `cutemusic/web.py` (the LAN web page and the Web tab),
the wiring in `cutemusic/window.py`, the menus' hooks in `cutemusic/browser.py` and `cutemusic/playlists.py`.


## Making them (negatives.py)

- Right-click songs, an album or an artist in the Library, or a playlist in Playlists -> **Make negative**. They
  are queued (`Negatives.add(paths, root)`) and made one at a time in the background.
- The queue is kept across restarts in `~/.ag-cute-music/negatives-queue.json` (`[[source, negative], ...]`); the
  song being made when quitting goes back first in the queue and starts over (`stop()`); `resume()` 5 s after start.
- A negative is a FLAC in the negatives folder: next to the music folder, its name plus `-Negatives`
  (`~/Music/Player` -> `~/Music/Player-Negatives`; settings `negatives/folder`), at the song's own relative path,
  extension `.flac` (`negative_path(path, root)`; `None` for a path out of the music folder).
- `copy_metadata()` copies the song's tags, cover and lyrics onto the negative (mutagen), and its `.lrc`/`.txt` and
  folder images next to it: a negative is found, shown and sung along as its song.
- `Negatives.add_jobs([(source, negative)])` queues pairs as given, the negative path not derived from the music
  folder: used by web.py for songs that are not in the music folder.
- Signals: `changed()` (queue, current, percent, install progress), `made(source, negative)`.
- `status()` gives the status-bar line; `finished` keeps this session's results `(source, negative, seconds, error)`
  (failed ones shown in the tab; `forget()` before trying again).

### The separator

- [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) with the BS-RoFormer model
  `model_bs_roformer_ep_317_sdr_12.9755.ckpt`, `--single_stem Instrumental`, FLAC out, into a temp dir, then moved
  into place through `<negative>.part`.
- Not part of the application: installed on first use into its own venv, `~/.ag-cute-music/separator` (about 3 GB
  with CPU PyTorch), the model (~640 MB) in `~/.ag-cute-music/separator-models`. `.ready` in the venv holds
  `INSTALL` ('2'); bump `INSTALL` to make the packages be installed again (2 = `beartype>=0.21` forced, for Python
  3.14: audio-separator pins `< 0.19`, which cannot read 3.14's `Callable | None` hints).
- The venv is made with `_python()`: the running interpreter when not frozen, else `python3` on the PATH (a frozen
  bundle's own Python cannot make venvs).
- pip installs: `pip`; CPU-only torch from the PyTorch CPU index **on Linux without nvidia-smi only** (macOS gets the
  default wheel, which has MPS); `audio-separator[gpu]` with nvidia-smi, else `audio-separator[cpu]`; `audioread`;
  then `beartype>=0.21 --no-deps`.
- The install shows a non-modal progress dialog with pip's last line (`_separator_install_dialog`); Stop cancels.
- ffmpeg is required (audio-separator reads and writes through it). Missing: the queue waits with
  `ffmpeg is needed (brew install ffmpeg / sudo apt install ffmpeg / winget install ffmpeg)`.
- Run through `_RUN`, a small wrapper given to the venv's python with `-c`:
  - `AG_CUTEMUSIC_SEPARATOR_CPU=1` (device setting "CPU only") stubs `torch.cuda.is_available` and
    `torch.backends.mps.is_available` to False.
  - **macOS on Apple Silicon**: audio-separator only uses MPS when `platform.processor()` says `arm`, and Python
    often says `''` or `i386` there; the wrapper patches `Separator.get_system_info` to report `processor='arm'`.
  - `PYTORCH_ENABLE_MPS_FALLBACK=1`: an operation Metal lacks runs on the CPU instead of failing.
- Progress: the `NN%|` of its tqdm bar (`_PERCENT`). The device it really ran on is logged after each song
  (`_device()`), with the time it took.

### Settings (View -> Negatives settings)

| Setting | Key | Values | |
|---|---|---|---|
| Quality | `negatives/quality` | `best` (8 overlapping windows), `fast` (2) | `--mdxc_overlap`; fast is 3-4x quicker on a CPU |
| Runs on | `negatives/device` | `auto` (GPU when there is one), `cpu` | |
| Windows a pass | `negatives/batch` | 1, 4, 8 | `--mdxc_batch_size`; more is often faster on a GPU |

Taken when a song starts: a change applies from the next song.

### In the window

- **Negatives tab** (View -> Negatives, Ctrl+K), after Library, a `Browser` like it: the negatives made (a
  `Scanner` on the negatives folder), plus the ones being made, queued or failed, dimmed (`pending`) with their state
  in the Time column (`_show_negatives`). Right-click on pending ones (`_pending_menu`): Remove from the queue, Try
  again, Stop, Start. Move to Trash on negatives uses the system Trash, not the music folder's (`trash_negatives`).
- View -> **Stop making negatives** (the rest kept queued).
- Playing a negative switches the visual pane to the **Lyrics** view (`_update_track`).
- **Original / Negative button** (`version_button`, bottom left of the visual pane, not on the Mixer): shown when
  the song playing has its other version (`_counterpart`); `switch_version()` goes on with the other one from the
  same point, playing or paused as it was, in the queue in its place.
- `_is_negative(track)`: under the negatives folder, or a web job's negative.


## Negatives for the LAN (web.py)

A web page on the LAN where anyone with a browser sends songs and gets their negatives back, each under a **token**
of 5 digits of their choosing. Rules (as decided):

- The remote user cannot start anything: songs they send **wait** until started here (Start in the Web tab).
- The token is a namespace, not a password: a token's page shows only its songs.
- Page **Library** tab: all the token's songs, made or not, playable (streamed).
- Page **Negatives** tab: **only negatives made**; played (streamed).
- **Downloads**, the songs and their negatives alike, **only once allowed here** (Allow download, per song or per
  token; the server refuses with 403 otherwise). Until then the row says "play only", and **only the middle half**
  of the song (25 % to 75 %, `PREVIEW`) is played: `/api/song` and `/api/stream` serve a file of that part only
  (`WebQueue.preview()`: ffmpeg, once, `.preview-song.flac` / `.preview-negative.flac` in the job's folder, made
  again when the song or negative is newer), so the rest is never sent. The page's player bar (its own controls,
  not the browser's) shows the whole song's length (the job's `preview` start, its tags' `duration`), the part
  played lit, seeking held within it; the lyrics follow the song's time. Without ffmpeg: not played (503). (Streamed audio can still be
  recorded: a courtesy, not a lock.)
- **Original / Negative** in the page's player bar, as in the app: the same point, playing or paused as it was.
- Nothing else of the app is reachable from the page: no mixer, no playback of the computer, no settings.
- **The page's amplifier**: the top of the page is the Amplifier view (dark plate), and it works there, in the
  browser, on what the page plays: `amp_picture()` draws it off screen once when the server starts, as the panel
  without what moves (`/amp-panel.png`) and where the knobs, meter faces, mute and 11 are (`/amp.json`, with the
  needles' constants); the page draws the knobs, needles, PEAK lamps and mute over it and turns them as the app
  (click on the edge or scale, drag from the middle, Shift fine, wheel in a knob's column or the volume, double
  click to the middle). Web Audio: bass and treble as low and high shelves at 100 Hz and 11 kHz (equalizer-3bands'
  bands), balance as audiopanorama's simple, the volume cubic and to 11 (+6 dB), mute; the VU meters from two
  analysers after the tone and balance, the needles moved by the volume, trimmed and sprung as the app's. The
  lever switches the meter window as the app's: ANALOG, LED BAR (VUMeter's compact LED rows, the peak held),
  SPECTRUM (an analyser of 512 bands, grouped as VUMeter's). No mute. Kept per browser (localStorage `amp/...`).
- The page's **output selector**: **Streaming** (downloads as they are) or **Download**: the Download links then
  carry `&bass=&treble=&balance=`, and the server makes the file with them (`_amp_download`: ffmpeg's bass and
  treble shelves at 100 Hz and 11 kHz, pan for the balance; a FLAC with the tags and cover, "<name> (amp).flac",
  through a temporary file in the web folder). The volume is not applied to downloads. Without ffmpeg: 503.
  Without Web Audio or `/amp.json`: the whole picture (`/amp.png`), and downloads as they are.
- **Limits**: a token holds at most 50 songs and 200 MB of them (`TOKEN_FILES`, `TOKEN_BYTES`; the songs being
  sent counted, `reserve()` / `release()`); the page checks before sending, the server refuses with 413.
- **The first screen** offers two ways in: **Request negatives** (enrolling: a display name and an email address;
  the address verified with a code emailed to it, `POST /api/code` then `POST /api/verify`, unless it is verified
  already, `GET /api/email`; then a token of one's own, 5 digits not taken, `POST /api/token`) and **Retrieve
  negatives** (an email address and a token made with it, `POST /api/login`; Forgot your token? there). A token's
  link (`#12345`) opens Retrieve with the token filled in. New token… (the source menu) is Request negatives again
  (the address verified already: straight to the token).
- **Logging in**: the page asks for the token **and the email address it was made with** (`POST /api/login`); the
  server gives a key (random, `uuid4`), kept in the browser and in tokens.json (the last `KEYS`, 20, a token: as
  many browsers logged in), sent with every other request (`&key=`; else 401 and the page asks again). A wrong
  address: 403, `LOGIN_TRIES` (10) a client in `LOGIN_WINDOW` (10 min), then 429. Log out forgets the key here and
  there (`POST /api/logout`).
- **New token…** (the first screen's link, or the source menu): digits not taken, a name and an email address,
  **verified first**: a 6-digit code emailed to it (`POST /api/code`; good `CODE_VALID`, 15 min, `CODE_TRIES`, 5
  tries; resent at most once a minute), typed on the page (`POST /api/token` with `&code=`; 428 while one is needed).
  An address verified once (verified.json) makes more tokens without a code. Logging in to digits that are no token
  yet turns to making it. A token from before, with songs and no owner, goes to whoever makes it first. Needs the
  email notifications set up (no code can be sent otherwise: no token made).
- **View -> Web users...** (also the Web tab's Web users... button): every token, its owner's name and email
  address (double click: changed; an address set here is taken as verified, and a changed one logs out the
  browsers logged in with the old), Verified, Songs, Logged in; Add... (a token with its owner, made here),
  Remove... (the subscription: owner and logins forgotten, the digits free; the songs kept or removed too), Log
  out everywhere. Tokens with songs and no owner are listed too, to be given one.
- **Forgot your token?** (the first screen): the tokens made with the email address typed are emailed to it
  (`POST /api/recover`; the page gets the same answer whether there are any or not, so it learns nothing; once
  every 10 minutes an address and a client, `RECOVER_EVERY`; needs the email notifications set up).
- **Public address** (File -> Web libraries..., "Public address (shootback tunnel)"; settings `web/tunnel/...`:
  the master's host:port, its password, the page's public address there): while the page is served, shootback's
  slaver (`shootback/slaver.py`, as run by hand: `-m <master> -t 127.0.0.1:<port> -k <password>`) runs in a
  process of its own (`cutemusic/tunnel.py`; a standalone build runs itself with `--tunnel`), started again 5 s
  after it ends, its output in the log; Connected once it reaches the master. The public address is listed first
  in the dialog and in the emails. The password is the master's `-k` (shootback/master.py and slaver.py set
  it where the handshake reads it: before, both ignored `-k` and used the default key).
- **Request download** (the page's player bar, at its right, for a song not downloadable: only its middle half
  played): asks for it to be allowed (`POST /api/request`, marked `requested`); in the Web tab its row bold with a
  bell, its Download column "requested" (amber; tooltip: when), the window alerted. Allow download clears it and
  emails the owner ("Downloads allowed"). The button then reads "Download requested"; once allowed it is gone.
- The Web tab shows a token by its owner, "Adi <adi@example.com>" (its token in the tooltip; "Web 12345" for one
  with no owner), as do the source button and Copy to web library.
- **The emails** carry no application's name: From is the address set (with a name only when "From name" is set
  in Web libraries..., which then also signs them). They hold what is known: the owner's address and the page's
  address the token was made at (its Host then, kept as the token's `site`; the LAN's or the tunnel's public one),
  else this computer's. The verification code's email names the page it was asked from.
- **Email notifications** (File -> Web libraries..., "Email the token's owner when their negatives are made";
  settings `web/mail/...`: SMTP server, port, security STARTTLS / SSL / none, user, password (kept as it is in
  the settings), From; Send a test): when a token's songs are made (or fail) from its queue, or Set negative...,
  its owner gets one email (the news gathered for `NOTIFY_DELAY`, 60 s, while the queue goes on), with the songs
  and the page's addresses with the token; sent in a thread (`send_mail`), the result in the log.
- **Whose a token is**: a new token is opened with a name and an email address (the page asks for both; `POST
  /api/token`, once, kept in `~/.ag-cute-music/web/tokens.json` as {token: {name, email}}; the address is where
  to notify them, never given back to the page); uploads to a token with no owner and no songs are refused. The
  window shows it: "Web 12345 (Adi)" in the source button and Copy to web library, "12345  Adi <address>" in the
  Web tab. Tokens from
  before, with songs and no name, open as they are.
- **By album**: the page's Library and Negatives list the songs under their album's row (album, artist, year, how
  many; a click folds it, kept in the browser), by disc and track; the albums by artist and name, the songs without
  an album under Unknown album, the ones being sent first. The tags are read once per job (`jobs_of`, kept in
  jobs.json); the filter matches title, artist and album too; the next song played is the next in that order.
- **Lyrics** over the player bar, as the app's strip: three lines, the sung one in the middle, synced by their
  stamps or plain ones by where the song is; A− A+ their size (kept in the browser). `/api/lyrics` finds them as
  the app does (`read_lyrics`: a .lrc/.txt beside the song, its tags, the cached online ones), else in the
  negative's tags; a song put in a token from the music folder takes its .lrc/.txt with it.

### Turning it on

- File -> **Web libraries...** (also the Web tab's Web page... button), a modal dialog: **Enable** (settings
  `web/on`, served again at start; the Web tab shown only while enabled), the port (`web/port`,
  default **47101**; `sharing.py`'s library sharing is 47100), Apply restarts the server on it (stays on the old one
  if the new is taken), and this computer's LAN addresses with the port (`sharing._addresses()`), each with Copy,
  links once served on it.
- The server is a `ThreadingMixIn` `HTTPServer` on all interfaces, in a daemon thread; no TLS, no Bonjour.

### Jobs

One job per song, kept in `~/.ag-cute-music/web/jobs.json`; files in `~/.ag-cute-music/web/<token>/<job id>/`: the
song as sent (`<name>`), and its negative `<name stem> (negative).flac`. Not in the negatives folder.

```
{id, token, name, upload, result, state, percent, error, added, size, download, sha1, was}
```

| State | Meaning |
|---|---|
| `song` | put in here from the app: a plain library song, its negative not asked for (Start makes it) |
| `waiting` | sent from the page: waits for Start (counted in the Web tab's name, amber) |
| `queued` | given to the Negatives queue (`add_jobs`) |
| `making` | being made, `percent` |
| `done` | negative there |
| `failed` | `error`; Start tries again |

`was` keeps the state a job had before Start: if the negatives' queue is cleared, it goes back to it (`song` or
`waiting`). States follow the Negatives queue in `_sync()`, on its `changed()`: web jobs share the one queue and
the one separator with the local negatives, one song at a time.

**Made already**: when a job is added, an existing negative is copied in and the job is `done` at once:
for a song put in from the music folder, its negative in the negatives folder; otherwise any `done` job with the
same file (SHA-1 of its bytes). Jobs from before `sha1` was added have none and are not matched. By hand: Web tab
-> **Set negative...** (a FLAC chosen, copied in, `done`; also replaces a negative).

### HTTP

```
GET  /                            the page (PAGE, one self-contained HTML string in web.py)
GET  /amp.png                     the amplifier picture
GET  /api/jobs?token=             {jobs: [{id, name, state, percent, error, added, size, download}]}
POST /api/upload?token=&name=     the song as the raw body (max 1 GB; audio extensions only): a job, waiting
GET  /api/song?token=&id=         the song, inline, ranges served (the player seeks)
GET  /api/song-download?token=&id=  the song as a file: only once allowed (as the negative)
GET  /api/stream?token=&id=       the negative, inline, ranges served (done only)
GET  /api/download?token=&id=     the negative as a file: 403 unless allowed
POST /api/remove?token=&id=       a job and its files gone (not while being made)
```

A token is `^\d{5}$` (400 otherwise); a job of another token is 404. File names are reduced to a base name without
control/reserved characters (`_safe_name`).

### The page

Looks like the app (its dark palette from `theme.py`): the amplifier on top, then a **Library | Negatives** tab
bar, a source button showing **Web 12345** (menu: Change token...), a filter field, **Add songs...**, a table as the
app's tree, a player bar (`<audio>` with the Original / Negative button), a status line. The token is asked once,
kept in `localStorage` and in the URL (`#12345`, bookmarkable). Songs are sent by drop anywhere on the page or Add
songs..., with upload progress (XHR). It polls `/api/jobs` every 2 s. At the end of a song, the next of the same
list plays.

### In the app

- **Web tab** (after Negatives): the jobs by token (Token / song, State, Download, Sent). Its name shows how many
  wait, in amber, **Web (2)**; a new one also makes the window ask for attention (`QApplication.alert`) and puts a
  line in the Library's status. Buttons: **Start**, **Start all waiting**, **Set negative...**,
  **Allow / Forbid download**, **Remove**, **Add songs...**; double click starts. A token's row selects all its songs.
  Right-click (`_context_menu`): **Play** (here, after the song playing), **Play negative**, **Start making
  negative**, **Stop making negative** (`stop_jobs`: back to `song` / `waiting`; when it was the one being made,
  the rest of the queue goes on; a queue stopped from View stays stopped), Allow / Forbid download,
  Set negative..., Remove.
- Songs into a token from here (as `song`): right-click -> **Copy to web library** -> a token / New token... (the
  Library, a playlist), **drop** on the Web tab (on a token's row: that token; from the Library or a file manager;
  folders walked), **Ctrl+V** (files copied in a file manager, or paths as text), **Add songs...**. Copied in a
  thread.
- The **Negatives tab**'s right-click -> **Copy to web library** (this computer's negatives, not a token's): the
  songs they are of (`_counterpart`) copied in, each job's folder getting the song and its negative, made at once.
- **Each token is a library** in the Library's source button, under "Sent from the web page" after the libraries on
  the LAN (`WebQueue.sources()`): choosing **Web 12345** shows its songs in the Library tab and its negatives in the
  Negatives tab (pending ones dimmed with their state), as Connect to a library does for a LAN library. There, Make
  negative = Start; Move to Trash = Remove (confirmed); the pending menu has Start / Remove / Stop. The
  Original / Negative button works with a token's songs too (`WebQueue.counterpart`, `is_negative`).


## macOS notes

- **ffmpeg**: `brew install ffmpeg`. An app started from the Finder has only `/usr/bin:/bin:/usr/sbin:/sbin` as
  PATH: negatives.py adds `/opt/homebrew/bin` and `/usr/local/bin` to `os.environ['PATH']` at import on darwin, so
  `shutil.which('ffmpeg')` finds it and the separator (which runs ffmpeg itself) does too. Not tried on a Mac yet.
- **python3 for the venv**: the .app is frozen, so `_python()` uses `python3` from the PATH (after the change
  above: Homebrew's first if there, else `/usr/bin/python3`, the Command Line Tools' stub, which asks to install
  them when missing). Check which one makes the venv; a Homebrew Python 3.14 needs the beartype fix (INSTALL 2).
- **GPU**: Apple Silicon uses MPS through the `processor='arm'` patch; check the log line
  `[negatives] made ... (on mps, best, 1 a pass)`. Try Windows a pass 4 or 8 there.
- The default PyTorch wheel on macOS (no CPU index) is used, with MPS.
- The web page needs nothing Mac-specific; the macOS firewall may ask to allow incoming connections on the port.
- The Library's and playlists' Copy to web library, the drop/paste on the Web tab: Finder copies give file URLs,
  as file managers on Linux do.


## State and what to try next

- Local negatives: in use on Linux.
- The web page and the Web tab: tested off screen (the server end to end with a stand-in for the separator, the
  page in headless Chrome, the Web tab and the Web page dialog with `QT_QPA_PLATFORM=offscreen`), **not yet clicked
  through in the running app**.
- The token is not a password (100 000 tokens; anyone on the LAN can try them). Rate limiting or longer tokens if
  it matters.
- A song format the remote browser cannot decode does not play on the page (its download works).
- Same song, different file (another encode, retagged): not matched as made already, made again.
- In the page's Library tab, the row playing is highlighted only while its own version (the song) plays; the
  Negatives tab likewise.


## Made in the background

The negatives are made by a process of their own (`ag-cute-music --negatives`, `negatives.worker_main`), not the
window's: started when something is queued, it goes on when AG CuteMusic quits (a session of its own) and ends once
the queue is empty; one at a time (`negatives-worker.lock`). The queue file is shared, changed under a lock
(`_edit_queue`); the worker writes what it does to `negatives-worker.json` (what is being made, its percent, what
finished), read by the window every second (`Negatives` as a client: the same attributes and signals). Stop making
negatives sends it SIGTERM (the song being made queued again first). Its log: `~/.ag-cute-music/negatives-worker.log`.
On Windows the window makes them itself, as before (stopped on quitting).

## The Negatives tab, by whose they are

The Negatives tab's tree starts with whose the negatives are (`Browser.owners`): **Local** (made here, from the
music folder), then each web account ("Name <email>", or "No account") and under it its tokens ("Token 12345"), then
the view chosen (Year, Artist, Album, Folder) as in the Library. A song copied into several tokens has its negative
under each of them (each token's job has a negative of its own), and under Local when made here too. Move to Trash
on a web account's negative removes its job (as in the Web tab, confirmed). The web negatives' tags are read once
a file (`WebQueue._read_track`, by path and time): the tab is drawn again at every change.
