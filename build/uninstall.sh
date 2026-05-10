#!/bin/bash
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./uninstall.sh"
    exit 1
fi

rm -rf /opt/audiocut
rm -f  /usr/bin/audiocut
rm -f  /usr/share/applications/AudioCut.desktop

echo "AudioCut uninstalled."
