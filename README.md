# AudioCut
MP3/FLAC/WAV audio cutter- cutting audio files visually

Version 1.0.0

![Download](https://github.com/kanehekili/AudioCut/releases/download/1.0.0/audiocut1.0.0.tar)

Audio cutter and joiner for Linux, based on ffmpeg. Cuts and joins audio files — optionally prepending or appending additional clips around the main selection.

Supported input formats: mp3, flac, wav, ogg, aac, m4a, wma.
Supported output formats: mp3, flac, wav, ogg, aac, m4a.

The current version is written in Python 3 and uses the Qt6 widget kit.

![Screenshot](https://github.com/kanehekili/AudioCut/blob/main/AudioCut.png)

### Prerequisites
* ffmpeg ≥ 3.x
* python3-pyqt6
* python3-numpy


### Features
* Display a scrollable, zoomable waveform of the loaded file
* Set precise in- and out-points to define the segment to keep
* Highlighted selection region; clear it with the **Clear selection** button
* Prepend or append a second audio clip around the main cut
* Preview the full assembly before saving
* Save the result via ffmpeg — re-encoded to a common sample rate and channel count when mixing sources
* Waveform auto-scrolls with a 10 % edge margin during cursor drag and playback
* Playback speed selector: 25 %, 50 %, 75 %, or 100 %
* When a selection is active, **Play** is scoped to that region — pause and resume stay within it

### How to cut
1. Open an audio file with the **Open** toolbar button.
2. The waveform loads automatically. Use the scrollbar or mouse wheel to scroll.
3. Play the file with the **Play** button and click anywhere on the waveform to move the cursor.
4. At the desired start of your cut, press **Start cut** (green flag icon). This sets the in-point.
5. At the desired end of your cut, press **Stop cut** (red flag icon). This sets the out-point.
6. The selected region is highlighted in the waveform.
7. Press **Save As…** to export. The output file contains only the selected segment.

> If no in- or out-point is set the entire file is exported unchanged.

### How to prepend or append a clip

AudioCut lets you glue an extra clip before (prepend) or after (append) the main cut in a single pass.

**To prepend a clip:**
1. Load and cut your main file first (see above).
2. Press the **Add before** toolbar button.
3. A file dialog opens — select the audio clip you want to place in front.
4. The waveform switches to the new clip. Set in- and out-points to define the part of that clip to use (leave them unset to use the whole file).
5. Press **Save As…** to confirm the clip and return to the main view. The prepend segment appears in green at the left of the waveform.
6. Press **Cancel** (✕) to discard the clip and return without changes.

> While selecting the clip the main file's cut markers disappear — they are saved internally and restored when you confirm or cancel.

**To append a clip:**
1. Same steps as above, but press **Add after** instead.
2. The confirmed append segment appears in amber at the right of the waveform.

> Same applies: the main cut markers are hidden during clip selection and restored afterwards.

Both a prepend and an append can be active at the same time. The status bar lists all active segments.

> Prepend and append are single slots: confirming a new prepend (or append) replaces the previous one.

**Previewing the assembly:**
Press **Play** when prepend or append clips are active. AudioCut builds a temporary WAV mix of all segments and plays it back end-to-end so you can hear the full result before saving.

**Saving the assembly:**
Press **Save As…** from the main view. All active segments (prepend → main cut → append) are joined by ffmpeg into the chosen output file.

### Install

#### Dependencies on Arch / Manjaro
```
sudo pacman -S python-pyqt6 python-numpy ffmpeg
```

#### Dependencies on Debian / Ubuntu / Mint
```
sudo apt --no-install-recommends install python3-pyqt6 python3-numpy ffmpeg
```

#### Manual install (from release tarball)
Download the latest release tarball, extract it and run the install script as root:
```
sudo ./install.sh
```
This copies the application to `/opt/audiocut/` and creates a launcher at `/usr/bin/audiocut`.
Note: the script does **not** install dependencies — install them first using the commands above for your distro.

To uninstall:
```
sudo ./uninstall.sh
```

#### Run from source
```
git clone https://github.com/kanehekili/AudioCut.git
cd AudioCut
python3 src/AudioCut.py
```

Logs are written to `src/AudioCut.log` when running from source, otherwise to `~/.config/AudioCut/AudioCut.log`.

#### Set GTK Theme for this Qt application
If you are running a desktop environment with GTK/GNOME (as opposed to LXQt or KDE) you might need to set `QT_QPA_PLATFORMTHEME`:
* Depending on the distro and version this variable may be one of: `gtk2`, `qt6ct`, `fusion`, `gtk3`

### Changes
10.05.2026
* Clear selection button (clears in/out markers without affecting the cut)
* Waveform edge-scroll: auto-scrolls when cursor enters the 10 % margin on either side, during both drag and playback
* Playback speed dropdown: 25 %, 50 %, 75 %, 100 %
* Selection-scoped playback: Play starts at the in-point and stops at the out-point; pause/resume stay within the selection
* After saving, cursor and scroll reset to the very beginning of the waveform

08.05.2026
* Initial release
