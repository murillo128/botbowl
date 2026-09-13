"""Audit every installed third-party package; retain unfiltered advisory output."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile', choices=('core', 'rl'), required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
extras = 'dev,web,competition,render' + (',rl' if args.profile == 'rl' else '')
subprocess.run([sys.executable, '-m', 'pip', 'install', '.[%s]' % extras,
                '-r', 'requirements/ci-tools.in'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
# The unpublished local project is reviewed/tested as source. PyPI cannot audit
# its release identity. Exclude only that identity, never an advisory or dependency.
packages = sorted((d.metadata['Name'], d.version) for d in metadata.distributions()
                  if d.metadata['Name'].lower() != 'botbowl')
requirements = args.output / 'resolved.txt'
requirements.write_text(''.join(name + '==' + version + '\n' for name, version in packages))
(args.output / 'scope.json').write_text(json.dumps({
    'profile': args.profile, 'python': sys.version,
    'excluded_local_project': 'botbowl (unpublished source tested by other jobs)',
    'third_party_packages': len(packages)}, indent=2) + '\n')
subprocess.run([sys.executable, '-m', 'pip_audit', '--strict', '--disable-pip', '--no-deps',
                '-r', str(requirements), '--format', 'json',
                '--output', str(args.output / 'audit.json')], check=True)
