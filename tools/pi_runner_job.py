#!/usr/bin/env python3
"""Capture fixed root-broker diagnostics; never accept commands or Docker arguments."""
import argparse
import json
from pathlib import Path
import subprocess
import time

OPERATIONS = ('probe', 'discover-existing', 'collect-existing', 'ros-inspect',
              'camera-one-frame', 'aurora-health', 'aurora-discover', 'aurora-recover')
# Independent read-only observations continue even when another observation fails.
SUITE = ('probe', 'discover-existing', 'ros-inspect', 'aurora-discover',
         'aurora-health', 'camera-one-frame', 'collect-existing')
BINARY = ('collect-existing', 'camera-one-frame')


def capture(operation, directory, timeout):
    if operation not in OPERATIONS:
        raise ValueError('Unapproved broker operation')
    directory.mkdir(parents=True, exist_ok=True)
    stdout = directory / ('stdout.bin' if operation in BINARY else 'stdout.txt')
    stderr = directory / 'broker-error.log'
    started = time.monotonic()
    record = {'operation': operation, 'stdout': stdout.name, 'stderr': stderr.name,
              'timed_out': False}
    with stdout.open('wb') as output, stderr.open('wb') as errors:
        try:
            result = subprocess.run(
                ['/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', operation],
                stdout=output, stderr=errors, timeout=timeout)
            record['exit_code'] = result.returncode
        except subprocess.TimeoutExpired:
            record.update(exit_code=124, timed_out=True)
            errors.write(b'\nJob adapter deadline exceeded.\n')
        except OSError as error:
            record.update(exit_code=127)
            errors.write(('\nBroker could not be invoked: ' + str(error) + '\n').encode())
    record['duration_seconds'] = round(time.monotonic() - started, 3)
    # Keep raw failed output, but never label a partial archive as a successful result.
    if record['exit_code'] == 0:
        target = directory / ('results.tar.gz' if operation in BINARY else 'metadata.txt')
        target.write_bytes(stdout.read_bytes())
    (directory / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f"{operation}: exit {record['exit_code']} ({record['duration_seconds']}s)", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=(*OPERATIONS, 'diagnostics-suite'))
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    # A fresh output directory prevents stale successful artifacts from surviving failures.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        parser.error('Output directory must be empty')
    operations = SUITE if args.operation == 'diagnostics-suite' else (args.operation,)
    deadline = time.monotonic() + 480
    records = []
    for operation in operations:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            records.append({'operation': operation, 'exit_code': 124, 'skipped': 'suite deadline'})
            continue
        directory = args.output_dir / operation if args.operation == 'diagnostics-suite' else args.output_dir
        records.append(capture(operation, directory, min(480, remaining)))
    (args.output_dir / 'manifest.json').write_text(json.dumps({
        'requested_operation': args.operation, 'results': records,
        'recovery_enabled': False, 'deployment_enabled': False,
    }, indent=2) + '\n')
    raise SystemExit(1 if any(record['exit_code'] != 0 for record in records) else 0)


if __name__ == '__main__':
    main()
