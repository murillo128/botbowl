"""Scheduling-independent seeds and owned MT19937 streams for lab episodes."""
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import struct

import numpy as np


PURPOSES = ("scenario", "engine", "policy-home", "policy-away", "observation")
DERIVATION_ALGORITHM = "sha256-json-uint32be-v1"
GENERATOR = "numpy.random.RandomState/MT19937"


def _identifier(value):
    # Metadata labels, never paths, URLs, arbitrary object reprs or config dumps.
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Expected a public identifier of 1..128 ASCII label characters")
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class SeedSpec:
    """A seed recipe. None selects and retains 256 bits of OS entropy.

    derivation_version is a positive namespace revision within the named V1
    algorithm. Changing it separates streams; it does not select new code.
    """

    master_seed: int = None
    episode_key: str = "0"
    purpose: str = "engine"
    component_id: str = "default"
    derivation_version: int = 1

    def __post_init__(self):
        if self.master_seed is None:
            object.__setattr__(self, "master_seed", secrets.randbits(256))
        if type(self.master_seed) is not int or not 0 <= self.master_seed < 2**256:
            raise ValueError("master_seed must be None or an integer in [0, 2**256)")
        _identifier(self.episode_key)
        _identifier(self.component_id)
        if type(self.purpose) is not str or self.purpose not in PURPOSES:
            raise ValueError("Unknown randomness purpose")
        if type(self.derivation_version) is not int or not 1 <= self.derivation_version < 2**32:
            raise ValueError("derivation_version must be an integer in [1, 2**32)")

    def to_json(self):
        return {"master_seed": self.master_seed, "episode_key": self.episode_key,
                "purpose": self.purpose, "component_id": self.component_id,
                "derivation_version": self.derivation_version}

    def canonical_bytes(self):
        return _canonical(self.to_json()).encode("utf-8")

    def seed_words(self):
        """All eight big-endian uint32 words of SHA-256, in digest order."""
        return struct.unpack(">8I", hashlib.sha256(self.canonical_bytes()).digest())

    def generator(self):
        """A new independent stream at this recipe's initial position."""
        return np.random.RandomState(self.seed_words())


def capture_stream(rng):
    """Detached immutable MT keys, position and Gaussian cache; privileged data."""
    name, keys, position, has_gauss, gaussian = rng.get_state()
    return name, tuple(int(key) for key in keys), position, has_gauss, gaussian
