"""Argument and output helpers for executable examples, not a lab wire format."""
import argparse
import json
from pathlib import Path


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--max-decisions', type=positive, default=8)
    parser.add_argument('--max-steps', type=positive, default=1000,
                        help='automatic resolution steps per accepted decision')
    return parser


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')
