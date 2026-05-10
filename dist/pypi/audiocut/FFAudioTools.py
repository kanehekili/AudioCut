'''
AudioCut - FFmpeg audio tools
copyright (c) 2026 kanehekili
'''

import os
import gzip
import subprocess
import logging
import tempfile
from itertools import tee
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass

import numpy as np

Log = logging.getLogger("AudioCut")

WAVEFORM_COLUMNS = 1200
BAR_DURATION_MS  = 100

_OGG_DEFAULT_BR = 160  # kb/s fallback when source bitrate is unknown


def _lossyQualityArgs(out_ext, source_br_kbps):
    """Return ffmpeg quality args for a lossy output format.

    For ogg/Vorbis the -q:a scale runs 0 (worst) to 10 (best), which is the
    inverse of mp3 VBR.  Use -b:a targeting the source bitrate instead so the
    output stays at the same quality level without exceeding the original.
    For every other lossy format -q:a 0 means best VBR quality.
    """
    if out_ext == '.ogg':
        br = source_br_kbps if source_br_kbps > 0 else _OGG_DEFAULT_BR
        return ["-b:a", f"{br}k"]
    return ["-q:a", "0"]


class OSTools():
    __instance = None

    def __new__(cls):
        if OSTools.__instance is None:
            OSTools.__instance = object.__new__(cls)
        return OSTools.__instance

    def getHomeDirectory(self):
        return os.path.expanduser("~")

    def getLocalPath(self, fileInstance):
        return os.path.dirname(os.path.realpath(fileInstance))

    def getFileNameOnly(self, path):
        return os.path.basename(path)

    def getExtension(self, path, withDot=True):
        comp = os.path.splitext(path)
        if len(comp) > 1:
            return comp[1] if withDot else comp[1][1:]
        return comp[0]

    def getPathWithoutExtension(self, path):
        return os.path.splitext(path)[0] if path else ""

    def fileExists(self, path):
        return os.path.isfile(path)

    def removeFile(self, path):
        if self.fileExists(path):
            os.remove(path)

    def canWriteToFolder(self, path):
        return os.access(path, os.W_OK)

    def ensureDirectory(self, path):
        if not os.access(path, os.F_OK):
            try:
                os.makedirs(path)
                os.chmod(path, 0o777)
            except OSError as e:
                logging.log(logging.ERROR, "target not created: " + path)
                logging.log(logging.ERROR, "Error: " + str(e.strerror))

    def joinPathes(self, *pathes):
        res = pathes[0]
        for _, tail in self.__pairwise(pathes):
            res = os.path.join(res, tail)
        return res

    def __pairwise(self, iterable):
        a, b = tee(iterable)
        next(b, None)
        return list(zip(a, b))

    def compressor(self, source, dest):
        with open(source, 'rb') as srcFile:
            bindata = bytearray(srcFile.read())
            with gzip.open(dest, 'wb') as gz:
                gz.write(bindata)
        os.remove(source)

    def namer(self, name):
        return name + ".gz"


def setupLogger():
    _ost = OSTools()
    scriptDir = _ost.getLocalPath(__file__)
    if _ost.canWriteToFolder(scriptDir):
        log_dir = scriptDir
    else:
        log_dir = _ost.joinPathes(_ost.getHomeDirectory(), ".config", "AudioCut")
        _ost.ensureDirectory(log_dir)
    log_path = _ost.joinPathes(log_dir, "AudioCut.log")
    fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.rotator = _ost.compressor
    fh.namer   = _ost.namer
    logging.basicConfig(
        handlers=[fh, logging.StreamHandler()],
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s : %(message)s',
    )


@dataclass
class AudioSegment:
    filepath:  str
    n_peaks:   int        = 0
    duration:  float      = 0.0
    start_bar: int | None = None
    end_bar:   int | None = None

    @property
    def start(self) -> float | None:
        if self.start_bar is None or self.n_peaks <= 0:
            return None
        return self.start_bar / self.n_peaks * self.duration

    @property
    def end(self) -> float | None:
        if self.end_bar is None or self.n_peaks <= 0:
            return None
        return (self.end_bar + 1) / self.n_peaks * self.duration


class AudioProbe:
    NA = "N/A"

    def __init__(self, path):
        self.path = path
        self.duration = 0.0
        self.codec = self.NA
        self.sample_rate = 0
        self.channels = 0
        self.bit_rate = 0
        self._probe()

    def _probe(self):
        cmd = ["ffprobe", "-show_format", "-show_streams", "-v", "quiet", self.path]
        result = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ).communicate()
        if not result[0]:
            raise IOError(f"Not a media file: {self.path}")
        for line in result[0].decode("utf-8").splitlines():
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip()
            if key == "codec_name" and self.codec == self.NA:
                self.codec = val
            elif key == "duration" and self.duration == 0.0:
                try:
                    self.duration = float(val)
                except ValueError:
                    pass
            elif key == "sample_rate" and self.sample_rate == 0:
                try:
                    self.sample_rate = int(val)
                except ValueError:
                    pass
            elif key == "channels" and self.channels == 0:
                try:
                    self.channels = int(val)
                except ValueError:
                    pass
            elif key == "bit_rate" and self.bit_rate == 0:
                try:
                    self.bit_rate = int(val) // 1024
                except ValueError:
                    pass

    def infoString(self):
        return (
            f"{self.codec} | {self.sample_rate} Hz | "
            f"{self.channels}ch | {self.bit_rate} kb/s | "
            f"{self.duration:.1f}s"
        )


def loadWaveformPeaks(filepath, n_peaks=WAVEFORM_COLUMNS):
    """Decode audio to mono float32, return peak array of n_peaks values (one bar = BAR_DURATION_MS ms)."""
    cmd = [
        "ffmpeg", "-i", filepath,
        "-f", "f32le", "-ac", "1",
        "-vn", "pipe:1",
        "-loglevel", "quiet",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not result.stdout:
        raise IOError(f"Could not decode audio: {filepath}")

    samples = np.frombuffer(result.stdout, dtype=np.float32)
    if len(samples) == 0:
        return np.zeros(n_peaks, dtype=np.float32), 0.0

    chunk = max(1, len(samples) // n_peaks)
    peaks = np.zeros(n_peaks, dtype=np.float32)
    for i in range(n_peaks):
        s = i * chunk
        e = min(s + chunk, len(samples))
        if s < len(samples):
            peaks[i] = float(np.max(np.abs(samples[s:e])))

    mx = float(peaks.max())
    if mx > 0:
        peaks /= mx
    return peaks, mx


class AudioCutter:
    def __init__(self):
        self._cancelled = False
        self._proc = None

    def cancel(self):
        self._cancelled = True
        if self._proc:
            self._proc.terminate()

    def cutAndJoin(self, segments, output_path, progress_cb=None):
        """
        Cut each segment to a temp file then concat.
        Returns True on success. Calls progress_cb(0..100) along the way.
        """
        self._cancelled = False
        _ost = OSTools()
        tmp_files = []
        concat_list = None

        try:
            max_ch   = 1
            max_rate = 44100
            max_br   = 0
            for seg in segments:
                try:
                    p = AudioProbe(seg.filepath)
                    if p.channels    > max_ch:   max_ch   = p.channels
                    if p.sample_rate > max_rate: max_rate = p.sample_rate
                    if p.bit_rate    > max_br:   max_br   = p.bit_rate
                except Exception:
                    pass

            out_ext    = _ost.getExtension(output_path).lower()
            out_is_wav = out_ext == '.wav'

            n = len(segments)
            for i, seg in enumerate(segments):
                if self._cancelled:
                    return False

                src_ext  = _ost.getExtension(seg.filepath).lower()
                tmp_ext  = '.wav' if out_is_wav else src_ext
                tmp      = tempfile.mktemp(suffix=tmp_ext, prefix=f"audiocut_{i}_")
                lossless = src_ext in ('.wav', '.flac', '.aiff', '.aif')
                args     = ["ffmpeg", "-y", "-loglevel", "quiet"]
                if out_is_wav:
                    args += ["-i", seg.filepath]
                    if seg.start is not None:
                        args += ["-ss", str(seg.start)]
                    if seg.end is not None:
                        args += ["-t", str(seg.end - (seg.start or 0.0))]
                    args += ["-ac", str(max_ch), "-ar", str(max_rate), tmp]
                elif lossless:
                    if seg.start is not None:
                        args += ["-ss", str(seg.start)]
                    args += ["-i", seg.filepath]
                    if seg.end is not None:
                        args += ["-t", str(seg.end - (seg.start or 0.0))]
                    args += ["-c", "copy", tmp]
                else:
                    args += ["-i", seg.filepath]
                    if seg.start is not None:
                        args += ["-ss", str(seg.start)]
                    if seg.end is not None:
                        args += ["-t", str(seg.end - (seg.start or 0.0))]
                    args += ["-ac", str(max_ch), "-ar", str(max_rate)]
                    args += _lossyQualityArgs(out_ext, max_br)
                    args += [tmp]

                Log.info("cut seg %d: %s", i, args)
                self._proc = subprocess.Popen(args)
                self._proc.wait()
                if self._proc.returncode != 0:
                    Log.info("ffmpeg cut failed for segment %d", i)
                    return False
                tmp_files.append(tmp)
                if progress_cb:
                    progress_cb((i + 1) / n * 90)

            if self._cancelled:
                return False

            concat_list = tempfile.mktemp(suffix=".txt", prefix="audiocut_concat_")
            with open(concat_list, "w") as f:
                for t in tmp_files:
                    f.write(f"file '{t}'\n")

            out_lossless = out_ext in ('.wav', '.flac', '.aiff', '.aif')
            concat_codec = ["-c", "copy"] if out_lossless else ["-ac", str(max_ch), "-ar", str(max_rate)] + _lossyQualityArgs(out_ext, max_br)
            args = [
                "ffmpeg", "-y", "-loglevel", "quiet",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                *concat_codec, output_path,
            ]
            Log.info("concat: %s", args)
            self._proc = subprocess.Popen(args)
            self._proc.wait()
            if self._proc.returncode != 0:
                Log.info("ffmpeg concat failed")
                return False

            if progress_cb:
                progress_cb(100.0)
            return True

        except Exception as e:
            Log.info("cutAndJoin error: %s", e)
            return False
        finally:
            for t in tmp_files:
                try:
                    _ost.removeFile(t)
                except Exception:
                    pass
            if concat_list:
                try:
                    _ost.removeFile(concat_list)
                except Exception:
                    pass
