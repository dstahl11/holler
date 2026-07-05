#!/usr/bin/env python3
"""List Google Cast devices on the LAN — use the output to fill in presets.yaml.

Run from a machine on the same network as the speaker:
    python scripts/discover.py
"""

import pychromecast

def main() -> None:
    print("Scanning for Cast devices (10s)...\n")
    chromecasts, browser = pychromecast.get_chromecasts(timeout=10)
    if not chromecasts:
        print("No devices found. Same network/VLAN? mDNS allowed?")
    for cc in chromecasts:
        info = cc.cast_info
        print(f"  name : {info.friendly_name}")
        print(f"  host : {info.host}")
        print(f"  uuid : {info.uuid}")
        print(f"  model: {info.model_name}\n")
    browser.stop_discovery()

if __name__ == "__main__":
    main()
