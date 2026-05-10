#!/bin/bash
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

install -d /opt/audiocut
cp -r "$DIR"/*.py  /opt/audiocut/
cp -r "$DIR"/icons /opt/audiocut/
chmod 755 /opt/audiocut/AudioCut.py

cp "$DIR"/AudioCut.desktop /usr/share/applications/

ln -sf /opt/audiocut/AudioCut.py /usr/bin/audiocut

echo "AudioCut installed."
echo "Required packages: python3-pyqt6 ffmpeg python3-numpy"
