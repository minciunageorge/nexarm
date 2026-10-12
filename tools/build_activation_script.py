#!/usr/bin/env python3
"""Produce the single-file activation download from reviewed tracked sources."""
import argparse
from pathlib import Path
import activate_pi_runner

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('output',type=Path)
args=parser.parse_args()
payload={name:(activate_pi_runner.ROOT/name).read_text() for name in activate_pi_runner.FILES}
source=(activate_pi_runner.ROOT/'tools/activate_pi_runner.py').read_text()
source=source.replace('PAYLOAD = None  # Filled only in the downloadable standalone build.', 'PAYLOAD = '+repr(payload))
with args.output.open('x') as output:
    output.write(source)
print(f'Created standalone activation script: {args.output}')
