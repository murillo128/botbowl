"""
==========================
Author: Niels Justesen
Year: 2018
==========================
Local in-memory games and trusted-local legacy pickle storage.

The storage directories must be controlled by the local operator. Pickle is
executable Python data: validation below is not a sandbox for untrusted files.
"""
from collections import OrderedDict
from copy import copy, deepcopy
from pathlib import Path
from threading import RLock
import os
import json
import pickle
import re
import stat
import tempfile
import uuid

from botbowl.core.game import Game
from botbowl.core.model import Replay, ReplayStep
from botbowl.core.util import get_data_path
from botbowl.web.errors import Conflict, NotFound, StorageError, WebError


MAX_REPLAY_PAGE = 100


def validate_name(name, replay=False):
    pattern = r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,199}" if replay else r"[A-Za-z0-9][A-Za-z0-9 _-]{2,38}"
    if (not isinstance(name, str) or not re.fullmatch(pattern, name)
            or '..' in name or name.endswith((' ', '.'))):
        raise WebError("Invalid replay ID." if replay else "Save names must be 3–39 ASCII letters, digits, spaces, underscores or hyphens, starting with a letter or digit and ending without a space.")
    return name if replay else name.lower()


def validate_page(from_idx, num_steps):
    if (type(from_idx) is not int or from_idx < 0 or type(num_steps) is not int
            or not 1 <= num_steps <= MAX_REPLAY_PAGE):
        raise WebError("Replay offset must be nonnegative and page size must be 1–100.")


class InMemoryHost:
    def __init__(self, save_dir=None, replay_dir=None, replay_cache_size=8):
        if type(replay_cache_size) is not int or replay_cache_size < 1:
            raise ValueError("Replay cache size must be positive")
        self.games = {}
        self.lock = RLock()
        self.save_dir = Path(save_dir if save_dir is not None else get_data_path('saves'))
        self.replay_dir = Path(replay_dir if replay_dir is not None else get_data_path('replays'))
        self.replay_cache_size = replay_cache_size
        self._replays = OrderedDict()

    def add_game(self, game):
        with self.lock:
            if game.game_id in self.games:
                raise Conflict("Game ID already exists.")
            # Arena serialization populates a static cache. Initialize it at
            # registration so even the first observation leaves the game alone.
            game.arena.to_json()
            self.games[game.game_id] = game

    def end_game(self, game_id):
        with self.lock:
            self.get_game(game_id)
            del self.games[game_id]

    def get_game(self, game_id):
        with self.lock:
            try:
                return self.games[game_id]
            except KeyError:
                raise NotFound("Game not found.") from None

    def get_games(self):
        with self.lock:
            return list(self.games.values())

    @staticmethod
    def is_paused(game):
        return getattr(game, '_web_paused_clocks', None) is not None

    def require_running(self, game):
        if self.is_paused(game):
            raise WebError("Game is paused. Resume before playing.", 409, 'game_paused')

    def pause_game(self, game_id):
        with self.lock:
            game = self.get_game(game_id)
            self._require_pause_allowed(game)
            if not self.is_paused(game):
                # Store clock references on the Game so trusted-local save/copy
                # preserves the session pause and primary/secondary identities.
                game._web_paused_clocks = [clock for clock in game.state.clocks if clock.is_running()]
                for clock in game._web_paused_clocks:
                    clock.pause()
            return game

    def resume_game(self, game_id):
        with self.lock:
            game = self.get_game(game_id)
            self._require_pause_allowed(game)
            if self.is_paused(game):
                for clock in game._web_paused_clocks:
                    if clock in game.state.clocks:
                        clock.resume()
                del game._web_paused_clocks
            return game

    @staticmethod
    def _require_pause_allowed(game):
        if game.config.competition_mode:
            raise WebError("Competition games cannot be paused.", 409, 'pause_not_allowed')
        if game.state.game_over or game.closed:
            raise WebError("This game has ended.", 409, 'game_ended')

    def _files(self, directory, suffix):
        try:
            # Path.glob can suppress directory read errors. A storage failure
            # must not look like a successful empty save/replay listing.
            with os.scandir(str(directory)) as entries:
                return sorted(Path(entry.path) for entry in entries if entry.name.endswith(suffix))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise StorageError() from exc

    def _save_path(self, name, create=False):
        canonical = validate_name(name)  # Before any filesystem access.
        matches = [path for path in self._files(self.save_dir, '.bb')
                   if path.stem.lower() == canonical]
        if len(matches) > 1 or (create and matches):
            raise Conflict("Save name already exists or is ambiguous ignoring case.")
        return matches[0] if matches else self.save_dir / (canonical + '.bb')

    def _read_pickle(self, path):
        try:
            # Do not follow file symlinks, including dangling ones. Storage roots
            # are operator configuration, never paths supplied by HTTP clients.
            if not stat.S_ISREG(path.lstat().st_mode):
                raise WebError("Expected a regular local save/replay file.")
            fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise WebError("Expected a regular local file.")
                return pickle.load(stream)
        except FileNotFoundError:
            raise NotFound("Local file not found.") from None
        except WebError:
            raise
        except Exception as exc:
            raise StorageError() from exc

    def save_game_exists(self, name):
        with self.lock:
            path = self._save_path(name)
            return path.exists() or path.is_symlink()

    def save_game(self, game_id, name):
        with self.lock:
            path = self._save_path(name, create=True)
            game = self.get_game(game_id)
            temporary = None
            try:
                # Pause only a copy: neither success nor filesystem/serialization
                # failure changes live clocks, decisions, RNG or object identity.
                saved = deepcopy(game)
                saved._web_saved_running_clocks = [i for i, clock in enumerate(saved.state.clocks)
                                                   if clock.is_running()]
                for i in saved._web_saved_running_clocks:
                    saved.state.clocks[i].pause()
                self.save_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=str(self.save_dir), prefix='.save-', delete=False) as stream:
                    temporary = stream.name
                    pickle.dump(saved, stream, protocol=pickle.HIGHEST_PROTOCOL)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Atomic no-clobber publication. Unlike replace(), a concurrent
                # duplicate cannot overwrite the first complete save.
                os.link(temporary, str(path))
            except FileExistsError:
                raise Conflict("Save name already exists.") from None
            except Exception as exc:
                raise StorageError() from exc
            finally:
                if temporary is not None:
                    os.unlink(temporary)

    def delete_saved_game(self, name):
        with self.lock:
            path = self._save_path(name)
            try:
                if path.is_symlink():
                    raise WebError("Symbolic links are not local save files.")
                path.unlink()
            except FileNotFoundError:
                raise NotFound("Save not found.") from None
            except OSError as exc:
                raise StorageError() from exc

    def _load_saved_game(self, path):
        game = self._read_pickle(path)
        try:
            if not isinstance(game, Game):
                raise ValueError("Not a saved Game")
            # Require the saved game-owned RNG; never silently reseed or invent
            # missing state for files from incompatible engine versions.
            game.dice._validate_state(game.capture_rng_state())
            json.dumps(game.to_json())  # Fail before registration, including corrupt JSON fields.
            return game
        except Exception as exc:
            raise StorageError() from exc

    def load_game(self, name):
        with self.lock:
            game = self._load_saved_game(self._save_path(name))
            try:
                running = getattr(game, '_web_saved_running_clocks', None)
                if running is None:
                    # The old writer paused all clocks before storing the Game.
                    game.resume_clocks()
                else:
                    if (not isinstance(running, list) or len(set(running)) != len(running)
                            or any(type(i) is not int or not 0 <= i < len(game.state.clocks) for i in running)):
                        raise ValueError("Invalid saved clock state")
                    for i in running:
                        game.state.clocks[i].resume()
                    del game._web_saved_running_clocks
                game.game_id = str(uuid.uuid4())
            except Exception as exc:
                raise StorageError() from exc
            self.add_game(game)
            return game

    def get_savenames(self):
        with self.lock:
            names = [validate_name(path.stem) for path in self._files(self.save_dir, '.bb')]
            if len(set(names)) != len(names):
                raise Conflict("Save names are ambiguous ignoring case.")
            return names

    def get_saved_games(self):
        with self.lock:
            return [(name, self._load_saved_game(self._save_path(name))) for name in self.get_savenames()]

    def get_replay_ids(self):
        with self.lock:
            return [validate_name(path.stem, replay=True) for path in self._files(self.replay_dir, '.rep')]

    def _get_replay(self, replay_id):
        replay_id = validate_name(replay_id, replay=True)
        path = self.replay_dir / (replay_id + '.rep')
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise WebError("Expected a regular local replay file.")
        except FileNotFoundError:
            self._replays.pop(replay_id, None)
            raise NotFound("Replay not found.") from None
        except OSError as exc:
            raise StorageError() from exc
        signature = (info.st_mtime_ns, info.st_size, info.st_ino)
        cached = self._replays.get(replay_id)
        if cached is not None and cached[0] == signature:
            self._replays.move_to_end(replay_id)
            return cached[1]
        self._replays.pop(replay_id, None)
        replay = self._read_pickle(path)
        try:
            if not isinstance(replay, Replay) or not isinstance(replay.steps, dict) or not isinstance(replay.actions, dict):
                raise ValueError("Not a replay")
            if any(type(idx) is not int or idx < 0 for idx in replay.steps):
                raise ValueError("Invalid replay step index")
            replay.steps = dict(sorted(replay.steps.items()))
            for step in replay.steps.values():
                if (not isinstance(step, ReplayStep) or type(step.num_reports) is not int
                        or not 0 <= step.num_reports <= len(replay.reports)):
                    raise ValueError("Invalid replay step")
                step.game['state']['reports'] = [report.to_json() for report in replay.reports[:step.num_reports]]
            replay.replay_id = replay_id
            replay.idx = 0
        except Exception as exc:
            raise StorageError() from exc
        self._replays[replay_id] = (signature, replay)
        while len(self._replays) > self.replay_cache_size:
            self._replays.popitem(last=False)
        return replay

    def load_replay(self, replay_id):
        with self.lock:
            replay = self._get_replay(replay_id)
            result = copy(replay)
            result.steps = self.get_replay_steps(replay_id, 0, MAX_REPLAY_PAGE)
            result.actions = deepcopy(replay.actions)
            result.reports = deepcopy(replay.reports)
            return result

    def get_replay_steps(self, replay_id, from_idx, num_steps):
        validate_name(replay_id, replay=True)
        validate_page(from_idx, num_steps)
        with self.lock:
            replay = self._get_replay(replay_id)
            # Offset counts frames, not sparse recording IDs (actions interleave).
            keys = list(replay.steps)[from_idx:from_idx + num_steps]
            return {idx: deepcopy(replay.steps[idx]) for idx in keys}
