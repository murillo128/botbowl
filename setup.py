"""Setuptools build hook; package metadata lives in pyproject.toml.

BOTBOWL_BUILD_NATIVE=1 requests a mandatory native build. The default is pure
Python regardless of compiler availability. Cython is an isolated build input,
not an installed runtime dependency.
"""
import os
from pathlib import Path

from setuptools import Extension, setup


root = Path(__file__).resolve().parent
os.chdir(root)
native = os.environ.get("BOTBOWL_BUILD_NATIVE", "0")
if native not in {"0", "1"}:
    raise ValueError("BOTBOWL_BUILD_NATIVE must be 0 (Python) or 1 (required native)")

extensions = []
if native == "1":
    from Cython.Build import cythonize

    extensions = cythonize(
        [Extension("botbowl.core.pathfinding.cython_pathfinding",
                   ["botbowl/core/pathfinding/cython_pathfinding.pyx"],
                   language="c++")],
        build_dir="build/cython",
        compiler_directives={"language_level": "3"},
    )

setup(ext_modules=extensions)
