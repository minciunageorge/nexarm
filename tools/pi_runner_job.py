#!/usr/bin/env python3
"""Job adapter: read-only root broker, no direct Docker access."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['probe', 'discover-existing', 'collect-existing','ros-inspect','camera-one-frame','aurora-health','aurora-discover','aurora-recover'])
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / ('results.tar.gz' if args.operation in ('collect-existing','camera-one-frame') else 'metadata.txt')
    with target.open('wb') as output, (args.output_dir / 'broker-error.log').open('wb') as errors:
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', args.operation],
                                stdout=output, stderr=errors, timeout=480)
    if result.returncode:
        target.unlink()
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
