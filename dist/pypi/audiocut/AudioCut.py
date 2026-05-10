#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# copyright (c) 2026 kanehekili
# GPL v2 or later

'''
AudioCut - precision audio cutter / joiner
'''

import sys
import tempfile
from dataclasses import dataclass
import numpy as np

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from FFAudioTools import (
    AudioProbe, AudioSegment, AudioCutter,
    loadWaveformPeaks, setupLogger, Log, BAR_DURATION_MS, OSTools,
)
from QtTools import installSigIntHandler

OPEN_FILTER = "Audio files (*.mp3 *.flac *.wav *.ogg *.aac *.m4a *.wma);;All files (*)"
SAVE_FILTER = "Audio files (*.mp3 *.flac *.wav *.ogg *.aac *.m4a);;All files (*)"

CURSOR_COLOR  = QtGui.QColor(220, 50, 50)
IN_COLOR      = QtGui.QColor(60, 200, 60)
OUT_COLOR     = QtGui.QColor(220, 160, 40)
BAR_COLOR     = QtGui.QColor(80, 140, 200)
SEL_COLOR     = QtGui.QColor(100, 180, 255, 55)
SEL_BAR_COLOR = QtGui.QColor(100, 200, 120)
PREPEND_COLOR = QtGui.QColor(80, 160, 100)
APPEND_COLOR  = QtGui.QColor(160, 120, 60)
BG_COLOR      = QtGui.QColor(28, 28, 28)
MIN_BAR_PX    = 5
SCROLL_MARGIN = 0.10  # fraction of visible bars that acts as scroll trigger zone


def _normPeaks(peaks, duration, bars_per_sec):
    target = max(1, round(duration * bars_per_sec))
    n = len(peaks)
    if n == target:
        return peaks
    if n > target:
        out = np.empty(target, dtype=np.float32)
        for i in range(target):
            s = int(i * n / target)
            e = max(s + 1, int((i + 1) * n / target))
            out[i] = peaks[s:e].max()
        return out
    else:
        idx = np.round(np.linspace(0, n - 1, target)).astype(np.intp)
        return peaks[idx].astype(np.float32)


# ---------------------------------------------------------------------------

@dataclass(eq=False)
class MediaData:
    """One slot in the assembly: holds full-file peaks (never recomputed) plus cut markers."""
    filepath:  str
    peaks:     object        # np.ndarray, fixed at load time
    duration:  float
    max_amp:   float = 1.0
    start_bar: int | None = None
    end_bar:   int | None = None
    color:     object = None  # QColor, set by MainFrame

    @property
    def n(self):
        return len(self.peaks)

    @property
    def i0(self):
        return self.start_bar if self.start_bar is not None else 0

    @property
    def i1(self):
        return (self.end_bar + 1) if self.end_bar is not None else self.n

    @property
    def active_peaks(self):
        return self.peaks[self.i0:self.i1]

    @property
    def active_duration(self):
        if self.n == 0:
            return self.duration
        return (self.i1 - self.i0) / self.n * self.duration

    def to_segment(self):
        return AudioSegment(
            filepath=self.filepath,
            n_peaks=self.n,
            duration=self.duration,
            start_bar=self.start_bar,
            end_bar=self.end_bar,
        )


# ---------------------------------------------------------------------------

class WaveformWorker(QtCore.QThread):
    waveformReady = pyqtSignal(object, float, float)   # peaks, max_amp, duration
    loadError     = pyqtSignal(str)

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            probe = AudioProbe(self.filepath)
            n_peaks = max(200, int(probe.duration * 1000 / BAR_DURATION_MS))
            peaks, max_amp = loadWaveformPeaks(self.filepath, n_peaks)
            self.waveformReady.emit(peaks, max_amp, probe.duration)
        except Exception as e:
            self.loadError.emit(str(e))


class JoinWorker(QtCore.QThread):
    progress = pyqtSignal(float)
    finished = pyqtSignal(bool, str)

    def __init__(self, segments, output_path):
        super().__init__()
        self.segments    = segments
        self.output_path = output_path
        self._cutter     = AudioCutter()

    def run(self):
        ok = self._cutter.cutAndJoin(
            self.segments,
            self.output_path,
            progress_cb=lambda p: self.progress.emit(p),
        )
        self.finished.emit(ok, self.output_path if ok else "Join failed")

    def cancel(self):
        self._cutter.cancel()


# ---------------------------------------------------------------------------

class WaveformWidget(QtWidgets.QWidget):
    cursorPositioned = pyqtSignal(float)
    dragSeek         = pyqtSignal(float)
    dragFinished     = pyqtSignal()
    scrollChanged    = pyqtSignal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks            = None
        self.duration         = 0.0
        self.cursor_pos       = 0.0
        self.in_pos           = None
        self.out_pos          = None
        self._drag            = None
        self._scrollFrac      = 0.0
        self._dragMoved       = False
        self._slotBoundaries  = []   # list of (start_bar, end_bar, QColor)

        self.setMinimumHeight(100)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

    # --- public setters ---

    def loadPeaks(self, peaks, duration):
        """Initial load — resets cursor/markers/scroll and shows a single-file waveform."""
        self.cursor_pos      = 0.0
        self.in_pos          = None
        self.out_pos         = None
        self._scrollFrac     = 0.0
        self._slotBoundaries = []
        self.setAssembly([(peaks, duration, BAR_COLOR)])

    def setAssembly(self, slots):
        """Update waveform from multiple (peaks, duration, color) slots without resetting markers."""
        if not slots:
            return
        combined  = np.concatenate([s[0] for s in slots])
        total_dur = sum(s[1] for s in slots)
        boundaries, idx = [], 0
        for peaks, dur, color in slots:
            boundaries.append((idx, idx + len(peaks), color))
            idx += len(peaks)
        self._slotBoundaries = boundaries
        self.peaks    = combined
        self.duration = total_dur
        self.cursor_pos = min(self.cursor_pos, max(0.0, total_dur))
        if self.in_pos is not None:
            self.in_pos  = min(self.in_pos,  total_dur)
        if self.out_pos is not None:
            self.out_pos = min(self.out_pos, total_dur)
        self.update()
        self._emitScroll()

    def setCursor(self, pos):
        self.cursor_pos = pos
        self._scrollToCursor()
        self.update()

    def resetToStart(self):
        self._scrollFrac  = 0.0
        self.cursor_pos   = 0.0
        self._emitScroll()
        self.update()

    def clearSelection(self):
        self.in_pos  = None
        self.out_pos = None
        self.update()

    def setIn(self, pos):
        self.in_pos = max(0.0, min(pos, self.duration))
        if self.out_pos is not None and self.out_pos < self.in_pos:
            self.out_pos = None
        self.update()

    def setOut(self, pos):
        pos = max(0.0, min(pos, self.duration))
        if self.in_pos is not None and pos <= self.in_pos:
            self.in_pos  = None
            self.out_pos = None
        else:
            self.out_pos = pos
        self.update()

    def clear(self):
        self.peaks            = None
        self.duration         = 0.0
        self.cursor_pos       = 0.0
        self.in_pos           = None
        self.out_pos          = None
        self._scrollFrac      = 0.0
        self._slotBoundaries  = []
        self.update()
        self._emitScroll()

    def setScrollBar(self, start_bar):
        if self.peaks is None:
            return
        n     = len(self.peaks)
        n_vis = self._nVisible()
        max_s = max(1, n - n_vis)
        self._scrollFrac = max(0.0, min(1.0, start_bar / max_s))
        self.update()

    # --- zoom / scroll helpers ---

    def _nVisible(self):
        if self.peaks is None or self.width() <= 0:
            return 0
        n = len(self.peaks)
        return min(n, max(1, self.width() // MIN_BAR_PX))

    def _startBar(self):
        if self.peaks is None:
            return 0
        n     = len(self.peaks)
        n_vis = self._nVisible()
        max_s = max(0, n - n_vis)
        return int(self._scrollFrac * max_s)

    def _visibleTimeRange(self):
        if self.peaks is None or self.duration <= 0:
            return (0.0, max(self.duration, 1.0))
        n   = len(self.peaks)
        s   = self._startBar()
        nv  = self._nVisible()
        t_s = (s / n) * self.duration
        t_e = ((s + nv) / n) * self.duration
        return (t_s, t_e)

    def _scrollToCursor(self):
        if self.peaks is None or self.duration <= 0:
            return
        n   = len(self.peaks)
        nv  = self._nVisible()
        if nv >= n:
            return
        margin  = max(1, int(nv * SCROLL_MARGIN))
        s       = self._startBar()
        cur_bar = int(self.cursor_pos / self.duration * n)
        max_s   = max(1, n - nv)

        if cur_bar < s + margin:
            start = max(0, cur_bar - margin)
        elif cur_bar >= s + nv - margin:
            start = min(max_s, cur_bar - (nv - margin))
        else:
            return

        self._scrollFrac = start / max_s
        self._emitScroll()

    def _emitScroll(self):
        if self.peaks is None:
            self.scrollChanged.emit(0, 0, 0)
            return
        self.scrollChanged.emit(self._startBar(), self._nVisible(), len(self.peaks))

    # --- coordinate helpers ---

    def _posToX(self, pos):
        if self.peaks is None or self.duration <= 0:
            return 0
        n  = len(self.peaks)
        s  = self._startBar()
        nv = self._nVisible()
        bw = max(MIN_BAR_PX, self.width() // nv) if nv > 0 else MIN_BAR_PX
        return int((pos / self.duration * n - s) * bw)

    def _xToPos(self, x):
        if self.peaks is None or self.duration <= 0:
            return 0.0
        n  = len(self.peaks)
        s  = self._startBar()
        nv = self._nVisible()
        bw = max(MIN_BAR_PX, self.width() // nv) if nv > 0 else MIN_BAR_PX
        return max(0.0, min(self.duration, (s + x / bw) / n * self.duration))

    # --- painting ---

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        h = self.height()

        p.fillRect(0, 0, w, h, BG_COLOR)

        if self.peaks is not None and self.duration > 0:
            n  = len(self.peaks)
            s  = self._startBar()
            nv = self._nVisible()
            bw = max(MIN_BAR_PX, w // nv)

            # selection tint
            if self.in_pos is not None and self.out_pos is not None and self.out_pos > self.in_pos:
                x1 = self._posToX(self.in_pos)
                x2 = self._posToX(self.out_pos)
                p.fillRect(x1, 0, x2 - x1, h, SEL_COLOR)

            # bars from bottom up
            p.setPen(Qt.PenStyle.NoPen)
            in_sel = self.in_pos is not None and self.out_pos is not None and self.out_pos > self.in_pos
            for i, peak in enumerate(self.peaks[s:s + nv]):
                bar_idx  = s + i
                bar_time = (bar_idx / n) * self.duration
                slot_color = BAR_COLOR
                for b0, b1, bc in self._slotBoundaries:
                    if b0 <= bar_idx < b1:
                        slot_color = bc
                        break
                in_range = in_sel and self.in_pos <= bar_time < self.out_pos
                p.setBrush(SEL_BAR_COLOR if in_range else slot_color)
                x     = i * bw
                bar_h = max(1, int(peak * (h - 2)))
                p.drawRect(x, h - bar_h, bw - 1, bar_h)

            # dividers between slots
            if len(self._slotBoundaries) > 1:
                p.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 1))
                for b0, _, _ in self._slotBoundaries[1:]:
                    if s < b0 <= s + nv:
                        x_div = (b0 - s) * bw
                        p.drawLine(x_div, 0, x_div, h)

            # in-point (green)
            if self.in_pos is not None:
                p.setPen(QtGui.QPen(IN_COLOR, 2))
                x = self._posToX(self.in_pos)
                p.drawLine(x, 0, x, h)

            # out-point (orange)
            if self.out_pos is not None and self.out_pos != self.in_pos:
                p.setPen(QtGui.QPen(OUT_COLOR, 2))
                x = self._posToX(self.out_pos)
                p.drawLine(x, 0, x, h)

            # cursor (red)
            p.setPen(QtGui.QPen(CURSOR_COLOR, 1))
            cx = self._posToX(self.cursor_pos)
            p.drawLine(cx, 0, cx, h)

        else:
            p.setPen(QtGui.QColor(90, 90, 90))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Open a file to display waveform")

        p.end()

    # --- mouse ---

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.duration <= 0:
            return
        self._dragMoved = False
        self._drag      = 'cursor'
        self.cursor_pos = self._xToPos(int(event.position().x()))
        self._scrollToCursor()
        self.update()
        self.cursorPositioned.emit(self.cursor_pos)

    def mouseMoveEvent(self, event):
        if self._drag is None or self.duration <= 0:
            return
        pos = self._xToPos(int(event.position().x()))
        self._dragMoved = True
        if self._drag == 'cursor':
            self.cursor_pos = pos
            self._scrollToCursor()
            self.update()
            self.dragSeek.emit(pos)

    def mouseReleaseEvent(self, event):
        had_drag = self._drag is not None and self._dragMoved
        self._drag = None
        if had_drag:
            self.dragFinished.emit()

    def wheelEvent(self, event):
        if self.peaks is None:
            return
        n   = len(self.peaks)
        nv  = self._nVisible()
        if nv >= n:
            return
        step  = max(1, nv // 10)
        max_s = n - nv
        start = self._startBar()
        if event.angleDelta().y() > 0:
            start = max(0, start - step)
        else:
            start = min(max_s, start + step)
        self._scrollFrac = start / max_s if max_s > 0 else 0.0
        self.update()
        self._emitScroll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._emitScroll()


# ---------------------------------------------------------------------------

class AudioPlayer(QtCore.QObject):
    cursorMoved   = pyqtSignal(float)
    durationKnown = pyqtSignal(float)
    playbackEnded = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._loadedPath  = None
        self._pendingSeek = None
        self._buffered    = False
        self._player      = QMediaPlayer()
        self._audioOutput = QAudioOutput()
        self._player.setAudioOutput(self._audioOutput)

        self._player.positionChanged.connect(
            lambda ms: self.cursorMoved.emit(ms / 1000.0)
        )
        self._player.durationChanged.connect(
            lambda ms: self.durationKnown.emit(ms / 1000.0)
        )
        self._player.playbackStateChanged.connect(self._onStateChanged)
        self._player.mediaStatusChanged.connect(self._onMediaStatus)

    def _onStateChanged(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.playbackEnded.emit()

    def _onMediaStatus(self, status):
        if status == QMediaPlayer.MediaStatus.BufferedMedia:
            self._buffered = True
            if self._pendingSeek is not None:
                self._player.setPosition(int(self._pendingSeek * 1000))
                self._pendingSeek = None
        elif status in (QMediaPlayer.MediaStatus.NoMedia,
                        QMediaPlayer.MediaStatus.LoadingMedia,
                        QMediaPlayer.MediaStatus.InvalidMedia):
            self._buffered = False

    def load(self, path):
        if self._loadedPath == path:
            return
        self._loadedPath  = path
        self._pendingSeek = None
        self._buffered    = False
        self._player.setSource(QUrl.fromLocalFile(path))

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def isPlaying(self):
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def isLoaded(self, path):
        return self._loadedPath == path

    def setRate(self, rate):
        self._player.setPlaybackRate(rate)

    def seek(self, pos):
        if self._buffered:
            self._player.setPosition(int(pos * 1000))
        else:
            self._pendingSeek = pos

    def playAt(self, pos):
        if self._buffered:
            self._player.setPosition(int(pos * 1000))
            self._player.play()
        else:
            self._pendingSeek = pos
            self._player.play()

    def stop(self):
        self._player.stop()

    def shutdown(self):
        self._player.stop()


# ---------------------------------------------------------------------------

class MainFrame(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AudioCut")
        self.setWindowIcon(self._icon("audiocut.png"))
        self.resize(1000, 280)

        # _slots is ordered for playback: [prepend*, primary, append*]
        # _primaryIdx points to the primary file's MediaData within _slots.
        self._slots           = []    # list[MediaData]
        self._primaryIdx      = 0
        self._clipMode        = None  # 'prepend' | 'append' | None
        self._clipData        = None  # MediaData being assembled in clip mode
        self._savedFile       = None  # primary filepath saved during clip mode
        self._currentFile     = None
        self._outputPath      = None
        self._previewPath     = None
        self._playingAssembly = False
        self._previewWorker   = None
        self._player          = AudioPlayer()
        self._waveWorker      = None
        self._joinWorker      = None

        self._buildUI()
        self._connectSignals()

    @staticmethod
    def _icon(name):
        return QtGui.QIcon(OSTools().joinPathes(OSTools().getLocalPath(__file__), "icons", name))

    def _buildUI(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(32, 32))
        self._tb = tb

        self._actOpen     = tb.addAction(self._icon("loadfile.png"),      "Open")
        self._sepOpen     = tb.addSeparator()
        self._actStartCut   = tb.addAction(self._icon("start-icon.png"),    "Start cut")
        self._actStopCut    = tb.addAction(self._icon("stop-red-icon.png"), "Stop cut")
        self._actClearSel   = tb.addAction(self._icon("clear-all.png"),     "Clear selection")
        tb.addSeparator()
        self._actPrepend  = tb.addAction(self._icon("prependClip.png"),   "Add before")
        self._actAppend   = tb.addAction(self._icon("appendClip.png"),    "Add after")
        self._sepAdd      = tb.addSeparator()
        self._actSaveAs   = tb.addAction(self._icon("save-as-icon.png"),  "Save As…")
        self._sepSave     = tb.addSeparator()
        self._actPlay     = tb.addAction(self._icon("play.png"),           "Play")
        self._speedCombo  = QtWidgets.QComboBox()
        self._speedCombo.addItems(["25%", "50%", "75%", "100%"])
        self._speedCombo.setCurrentIndex(3)
        self._speedCombo.setToolTip("Playback speed")
        tb.addWidget(self._speedCombo)
        self._sepClip     = tb.addSeparator()
        self._actConfirm  = tb.addAction(QtGui.QIcon(),                    "Use clip")
        self._actCancel   = tb.addAction(self._icon("window-close.png"),  "Cancel")
        self._actConfirm.setVisible(False)
        self._actCancel.setVisible(False)
        self._sepClip.setVisible(False)

        container = QtWidgets.QWidget()
        layout    = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._waveform  = WaveformWidget()
        self._scrollBar = QtWidgets.QScrollBar(Qt.Orientation.Horizontal)
        self._scrollBar.setRange(0, 0)
        self._scrollBar.setVisible(False)

        layout.addWidget(self._waveform)
        layout.addWidget(self._scrollBar)
        self.setCentralWidget(container)

        self._statusLabel = QtWidgets.QLabel("Ready")
        self._timeLabel   = QtWidgets.QLabel("0:00.000  ◆  0:00.000")
        self._progressBar = QtWidgets.QProgressBar()
        self._progressBar.setFixedWidth(180)
        self._progressBar.setVisible(False)
        self.statusBar().addWidget(self._statusLabel, 1)
        self.statusBar().addPermanentWidget(self._timeLabel)
        self.statusBar().addPermanentWidget(self._progressBar)

    def _connectSignals(self):
        self._actOpen.triggered.connect(self._openFile)
        self._actPlay.triggered.connect(self._playPause)
        self._actStartCut.triggered.connect(self._onStartCut)
        self._actStopCut.triggered.connect(self._onStopCut)
        self._actClearSel.triggered.connect(self._onClearSelection)
        self._speedCombo.currentIndexChanged.connect(self._onSpeedChanged)
        self._actPrepend.triggered.connect(lambda: self._startClip('prepend'))
        self._actAppend.triggered.connect(lambda: self._startClip('append'))
        self._actSaveAs.triggered.connect(self._saveAsOrConfirm)
        self._actConfirm.triggered.connect(self._confirmClip)
        self._actCancel.triggered.connect(self._cancelClip)

        self._waveform.dragSeek.connect(self._onDragSeek)
        self._waveform.cursorPositioned.connect(self._onCursorPositioned)
        self._waveform.scrollChanged.connect(self._onWaveformScrolled)

        self._scrollBar.valueChanged.connect(self._onScrollBarMoved)

        self._player.cursorMoved.connect(self._onPlayerCursorMoved)
        self._player.durationKnown.connect(self._onDurationKnown)
        self._player.playbackEnded.connect(self._onPlaybackEnded)

    # --- scrollbar sync ---

    def _onWaveformScrolled(self, start_bar, n_visible, n_total):
        self._scrollBar.blockSignals(True)
        if n_total <= 0 or n_visible >= n_total:
            self._scrollBar.setVisible(False)
        else:
            self._scrollBar.setVisible(True)
            self._scrollBar.setRange(0, n_total - n_visible)
            self._scrollBar.setPageStep(n_visible)
            self._scrollBar.setSingleStep(max(1, n_visible // 10))
            self._scrollBar.setValue(start_bar)
        self._scrollBar.blockSignals(False)

    def _onScrollBarMoved(self, value):
        self._waveform.setScrollBar(value)

    # --- file open ---

    def _openFile(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Audio File", "", OPEN_FILTER,
            options=QtWidgets.QFileDialog.Option.DontUseNativeDialog
        )
        if not path:
            return
        Log.info("open: %s", path)
        self._actPlay.setIcon(self._icon("play.png"))
        self._outputPath      = None
        self._playingAssembly = False
        self._clearPreview()
        self._currentFile = path
        primary = MediaData(
            filepath=path,
            peaks=np.zeros(0, dtype=np.float32),
            duration=0.0,
            color=BAR_COLOR,
        )
        self._slots      = [primary]
        self._primaryIdx = 0
        self._loadWaveform(path)
        self._player.load(path)

    def _loadWaveform(self, path):
        self._currentFile = path
        self._waveform.clear()
        self._updateTimeLabel(0.0)
        self.setWindowTitle(f"AudioCut  —  {OSTools().getFileNameOnly(path)}")
        self._statusLabel.setText(f"Loading {OSTools().getFileNameOnly(path)}…")
        if self._waveWorker and self._waveWorker.isRunning():
            self._waveWorker.wait()
        self._waveWorker = WaveformWorker(path)
        self._waveWorker.waveformReady.connect(self._onWaveformReady)
        self._waveWorker.loadError.connect(self._onLoadError)
        self._waveWorker.start()

    def _onWaveformReady(self, peaks, max_amp, duration):
        self._waveform.loadPeaks(peaks, duration)
        if self._clipMode:
            color = PREPEND_COLOR if self._clipMode == 'prepend' else APPEND_COLOR
            self._clipData = MediaData(
                filepath=self._currentFile,
                peaks=peaks,
                duration=duration,
                max_amp=max_amp,
                color=color,
            )
        else:
            md = self._slots[self._primaryIdx]
            md.peaks    = peaks
            md.duration = duration
            md.max_amp  = max_amp
            self._rebuildAssembly()
        try:
            probe = AudioProbe(self._currentFile)
            self._statusLabel.setText(
                f"{OSTools().getFileNameOnly(self._currentFile)}  |  {probe.infoString()}"
            )
        except Exception:
            self._statusLabel.setText(OSTools().getFileNameOnly(self._currentFile or ""))

    def _onLoadError(self, msg):
        Log.error("waveform load failed: %s", msg)
        self._statusLabel.setText(f"Error: {msg}")

    # --- clip mode ---

    def _startClip(self, mode):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Open file to {mode}", "", OPEN_FILTER,
            options=QtWidgets.QFileDialog.Option.DontUseNativeDialog
        )
        if not path:
            return
        Log.info("clip mode %s: %s", mode, path)
        self._actPlay.setIcon(self._icon("play.png"))
        self._clipMode  = mode
        self._savedFile = self._currentFile
        self._clipData  = None
        self._setClipToolbar(True)
        self._loadWaveform(path)
        self._player.load(path)

    def _confirmClip(self):
        if self._clipData is None:
            return
        w        = self._waveform
        clip_dur = w.duration
        in_p     = w.in_pos  if w.in_pos  is not None else 0.0
        out_p    = w.out_pos if w.out_pos is not None else clip_dur
        n        = self._clipData.n
        sb = round(in_p * n / clip_dur) if clip_dur > 0 else 0
        eb = int(out_p * n / clip_dur)  if clip_dur > 0 else n - 1
        sb = max(0, min(sb, n - 1))
        eb = max(sb, min(eb, n - 1))
        self._clipData.start_bar = sb
        self._clipData.end_bar   = eb

        if self._clipMode == 'prepend':
            self._slots.insert(0, self._clipData)
            self._primaryIdx += 1
        else:
            self._slots.append(self._clipData)

        Log.info("clip confirmed: %s  start_bar=%d end_bar=%d", self._clipMode, sb, eb)
        self._clipMode = None
        self._clipData = None
        self._setClipToolbar(False)
        self._restorePrimary()
        self._waveform.in_pos  = None   # assembly time coords shifted
        self._waveform.out_pos = None
        self._rebuildAssembly()
        self._showAssembly()

    def _cancelClip(self):
        Log.info("clip cancelled")
        self._clipMode = None
        self._clipData = None
        self._setClipToolbar(False)
        self._restorePrimary()
        self._rebuildAssembly()

    def _restorePrimary(self):
        if self._savedFile is None:
            return
        self._currentFile = self._savedFile
        self._savedFile   = None
        if self._currentFile:
            self.setWindowTitle(f"AudioCut  —  {OSTools().getFileNameOnly(self._currentFile)}")
            self._player.load(self._currentFile)
        self._actPlay.setIcon(self._icon("play.png"))
        self._waveform.cursor_pos = 0.0
        self._waveform.in_pos     = None
        self._waveform.out_pos    = None

    # --- assembly helpers ---

    def _primaryOffset(self):
        """Start time of primary slot within the combined waveform."""
        return sum(md.active_duration for md in self._slots[:self._primaryIdx])

    def _globalToFilePos(self, global_pos):
        """Map waveform global position to the primary file's clock (for seeking)."""
        if not self._slots:
            return global_pos
        md         = self._slots[self._primaryIdx]
        file_start = md.i0 * md.duration / md.n if md.n > 0 else 0.0
        local_t    = global_pos - self._primaryOffset()
        return max(0.0, file_start + local_t)

    def _rebuildAssembly(self):
        self._clearPreview()
        if not self._slots:
            return

        if len(self._slots) == 1:
            md = self._slots[0]
            if md.n > 0:
                self._waveform.setAssembly([(md.peaks, md.duration, BAR_COLOR)])
                # Restore markers from model (e.g. returning from clip mode).
                # setIn/setOut during interactive use will override these.
                if self._waveform.in_pos is None and md.start_bar is not None:
                    self._waveform.in_pos = md.start_bar / md.n * md.duration
                if self._waveform.out_pos is None and md.end_bar is not None:
                    self._waveform.out_pos = (md.end_bar + 1) / md.n * md.duration
                self._waveform.update()
            return

        # Multi-slot: show only the active region of each slot.
        densities = [md.n / md.duration for md in self._slots if md.n > 0 and md.duration > 0]
        bps = min(densities) if densities else 1000 / BAR_DURATION_MS

        raw_amps = [float(md.active_peaks.max()) * md.max_amp
                    for md in self._slots if len(md.active_peaks) > 0]
        global_amp = max(raw_amps) if raw_amps else 1.0
        if global_amp <= 0:
            global_amp = 1.0

        slots = []
        for md in self._slots:
            sc  = md.max_amp / global_amp
            ap  = md.active_peaks * sc
            dur = md.active_duration
            if len(ap) > 0 and dur > 0:
                slots.append((_normPeaks(ap, dur, bps), dur, md.color))

        if slots:
            self._waveform.setAssembly(slots)
            # in/out markers are preserved — they stay visible across rebuilds.

    # --- playback cursor ---

    def _hasValidSelection(self):
        w = self._waveform
        return w.in_pos is not None and w.out_pos is not None

    def _onPlayerCursorMoved(self, pos):
        if not self._player.isPlaying():
            return
        if self._playingAssembly or self._clipMode:
            global_pos = pos
        elif self._slots:
            md          = self._slots[self._primaryIdx]
            file_start  = md.i0 * md.duration / md.n if md.n > 0 else 0.0
            global_pos  = self._primaryOffset() + (pos - file_start)
        else:
            global_pos = pos
        if self._hasValidSelection() and global_pos >= self._waveform.out_pos:
            self._player.stop()
            self._waveform.setCursor(self._waveform.out_pos)
            self._updateTimeLabel(self._waveform.out_pos)
            return
        self._waveform.setCursor(global_pos)
        self._updateTimeLabel(global_pos)

    def _setClipToolbar(self, active):
        for act in (self._actOpen, self._actPrepend, self._actAppend):
            act.setVisible(not active)
        self._sepOpen.setVisible(not active)
        self._sepAdd.setVisible(not active)
        self._sepClip.setVisible(active)
        self._actCancel.setVisible(active)

    # --- playback ---

    def _playPause(self):
        if not self._slots or self._currentFile is None:
            return
        if self._player.isPlaying():
            self._player.pause()
            self._actPlay.setIcon(self._icon("play.png"))
            return
        if self._clipMode:
            self._playingAssembly = False
            self._player.play()
            self._actPlay.setIcon(self._icon("pause.png"))
            return
        if self._hasValidSelection():
            w = self._waveform
            in_sel = w.in_pos <= w.cursor_pos < w.out_pos
            start = w.cursor_pos if in_sel else w.in_pos
        else:
            start = self._waveform.cursor_pos
        has_assembly = len(self._slots) > 1
        if has_assembly:
            if self._previewPath and OSTools().fileExists(self._previewPath):
                self._playingAssembly = True
                self._player.load(self._previewPath)
                self._player.playAt(start)
                self._waveform.setCursor(start)
                self._actPlay.setIcon(self._icon("pause.png"))
            else:
                self._startPreviewBuild()
        else:
            self._playingAssembly = False
            file_pos = self._globalToFilePos(start)
            self._player.seek(file_pos)
            self._waveform.setCursor(start)
            self._player.play()
            self._actPlay.setIcon(self._icon("pause.png"))

    def _clearPreview(self):
        if self._previewPath:
            try:
                OSTools().removeFile(self._previewPath)
            except OSError:
                pass
        self._previewPath = None

    def _startPreviewBuild(self):
        self._applyInOutMarkers()
        self._rebuildAssembly()
        tmp = tempfile.mktemp(suffix='.wav', prefix='audiocut_preview_')
        Log.info("building preview: %s", tmp)
        self._statusLabel.setText("Building preview…")
        self._actPlay.setEnabled(False)
        segments = [md.to_segment() for md in self._slots]
        self._previewWorker = JoinWorker(segments, tmp)
        self._previewWorker.finished.connect(self._onPreviewReady)
        self._previewWorker.start()

    def _onPreviewReady(self, ok, path):
        self._actPlay.setEnabled(True)
        if not ok:
            Log.error("preview build failed")
            self._statusLabel.setText("Preview failed")
            return
        Log.info("preview ready: %s", path)
        self._clearPreview()
        self._previewPath     = path
        self._playingAssembly = True
        self._showAssembly()
        self._player.load(path)
        self._player.playAt(self._waveform.cursor_pos)
        self._actPlay.setIcon(self._icon("pause.png"))

    def _onPlaybackEnded(self):
        self._actPlay.setIcon(self._icon("play.png"))
        if self._playingAssembly:
            self._waveform.setCursor(0.0)
            self._updateTimeLabel(0.0)
        self._playingAssembly = False

    def _onDurationKnown(self, duration):
        if self._waveform.duration <= 0:
            self._waveform.duration = duration

    # --- seek ---

    def _onCursorPositioned(self, global_pos):
        if not self._slots or self._currentFile is None:
            return
        file_pos = global_pos if (self._playingAssembly or self._clipMode) else self._globalToFilePos(global_pos)
        self._player.seek(file_pos)
        self._updateTimeLabel(global_pos)

    def _onDragSeek(self, global_pos):
        if not self._slots or self._currentFile is None:
            return
        file_pos = global_pos if (self._playingAssembly or self._clipMode) else self._globalToFilePos(global_pos)
        self._player.seek(file_pos)
        self._updateTimeLabel(global_pos)

    # --- scissors ---

    def _onStartCut(self):
        if not self._slots or self._waveform.duration <= 0:
            return
        pos = self._waveform.cursor_pos
        self._waveform.setIn(pos)
        self._clearPreview()
        self._updateTimeLabel(pos)

    def _onStopCut(self):
        if not self._slots or self._waveform.duration <= 0:
            return
        pos = self._waveform.cursor_pos
        self._waveform.setOut(pos)
        self._clearPreview()
        self._updateTimeLabel(pos)

    def _onClearSelection(self):
        self._waveform.clearSelection()
        self._clearPreview()

    _SPEED_VALUES = [0.25, 0.50, 0.75, 1.00]

    def _onSpeedChanged(self, index):
        self._player.setRate(self._SPEED_VALUES[index])

    def _applyInOutMarkers(self):
        """Before save/preview: map in/out time positions to whichever slots they
        fall in, trim boundary slots, and drop slots entirely outside the selection."""
        w     = self._waveform
        in_t  = w.in_pos
        out_t = w.out_pos
        if in_t is None and out_t is None:
            return
        in_t  = in_t  if in_t  is not None else 0.0
        out_t = out_t if out_t is not None else w.duration

        # Capture slot boundaries before any edits.
        offsets = []
        off = 0.0
        for md in self._slots:
            offsets.append(off)
            off += md.active_duration

        to_remove = []
        for i, md in enumerate(self._slots):
            if md.n == 0 or md.duration == 0:
                continue
            s_start = offsets[i]
            s_end   = s_start + md.active_duration
            if s_end <= in_t or s_start >= out_t:
                to_remove.append(i)
                continue
            if in_t > s_start:
                local_t = in_t - s_start
                bar = md.i0 + int(local_t * md.n / md.duration)
                md.start_bar = max(md.i0, min(bar, md.i1 - 1))
            if out_t < s_end:
                local_t = out_t - s_start
                bar = md.i0 + int(local_t * md.n / md.duration)
                md.end_bar = max(md.start_bar if md.start_bar is not None else md.i0,
                                 min(bar, md.i1 - 1))

        for i in reversed(to_remove):
            del self._slots[i]
            if i < self._primaryIdx:
                self._primaryIdx -= 1
            elif i == self._primaryIdx:
                self._primaryIdx = max(0, min(self._primaryIdx, len(self._slots) - 1))

        self._waveform.in_pos  = None
        self._waveform.out_pos = None

    # --- status bar ---

    def _updateTimeLabel(self, pos):
        in_p  = self._waveform.in_pos
        out_p = self._waveform.out_pos
        cut   = (out_p - in_p) if (in_p is not None and out_p is not None and out_p > in_p) else 0.0
        self._timeLabel.setText(f"{self._fmtTime(pos)}  ◆  {self._fmtTime(cut)}")

    def _showAssembly(self):
        parts = [f"[{OSTools().getFileNameOnly(md.filepath)}]" for md in self._slots]
        self._statusLabel.setText("  +  ".join(parts))

    @staticmethod
    def _fmtTime(secs):
        total_ms = int(round((secs or 0.0) * 1000))
        m  = total_ms // 60000
        s  = (total_ms % 60000) // 1000
        ms = total_ms % 1000
        return f"{m}:{s:02d}.{ms:03d}"

    # --- save / join ---

    def _saveAsOrConfirm(self):
        if self._clipMode:
            self._confirmClip()
        else:
            self._chooseSavePath()

    def _chooseSavePath(self):
        if not self._slots:
            QtWidgets.QMessageBox.warning(self, "AudioCut", "Open a file first.")
            return
        primary = self._slots[self._primaryIdx]
        ext     = OSTools().getExtension(primary.filepath)
        default = OSTools().getPathWithoutExtension(primary.filepath) + "_cut" + ext
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Output As", default, SAVE_FILTER,
            options=QtWidgets.QFileDialog.Option.DontUseNativeDialog
        )
        if path:
            self._outputPath = path
            self._doJoin()

    def _doJoin(self):
        if not self._slots or not self._outputPath:
            return
        self._applyInOutMarkers()
        self._rebuildAssembly()
        segments = [md.to_segment() for md in self._slots]
        if len(segments) == 1 and segments[0].start is None and segments[0].end is None:
            QtWidgets.QMessageBox.information(
                self, "AudioCut", "No cut points set and no prepend/append — nothing to do."
            )
            return

        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        self._progressBar.setVisible(True)
        self._actSaveAs.setEnabled(False)
        self._statusLabel.setText("Joining…")

        Log.info("joining %d segment(s) -> %s", len(segments), self._outputPath)
        self._joinWorker = JoinWorker(segments, self._outputPath)
        self._joinWorker.progress.connect(lambda p: self._progressBar.setValue(int(p)))
        self._joinWorker.finished.connect(self._onJoinFinished)
        self._joinWorker.start()

    def _onJoinFinished(self, ok, msg):
        self._progressBar.setVisible(False)
        self._actSaveAs.setEnabled(True)
        if ok:
            Log.info("saved: %s", msg)
            self._statusLabel.setText(f"Saved: {msg}")
            self._waveform.resetToStart()
            self._player.seek(0.0)
            self._updateTimeLabel(0.0)
        else:
            Log.error("join failed")
            self._statusLabel.setText("Join failed")
            QtWidgets.QMessageBox.warning(self, "AudioCut", "Join failed.\nCheck the log for details.")

    # --- cleanup ---

    def closeEvent(self, event):
        Log.info("shutdown")
        self._player.stop()
        if self._waveWorker and self._waveWorker.isRunning():
            self._waveWorker.wait()
        if self._joinWorker and self._joinWorker.isRunning():
            self._joinWorker.cancel()
            self._joinWorker.wait()
        if self._previewWorker and self._previewWorker.isRunning():
            self._previewWorker.cancel()
            self._previewWorker.wait()
        self._clearPreview()
        event.accept()


# ---------------------------------------------------------------------------

def main():
    setupLogger()
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("AudioCut")
    _sigTimer = installSigIntHandler(app)
    win = MainFrame()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
