"""Bounded explicit recording and atomic per-episode JSON/JSONL storage.

The recorder binds to Timeline's existing admission/settlement hooks. It never
chooses/retries actions and never serializes Game, RNG or callback objects.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat

from .actions import ActionControl, ActionRequestV1
from .channels import InputProfile, PRIMARY_PROFILE, make_channel, project_inputs
from .observations import observe
from .records import (CHANNEL_FILES, MAX_EPISODE_BYTES, MAX_RECORD_BYTES, MAX_ROWS,
                      SCHEMAS, EpisodeManifestV1, RecordError, TransitionV1,
                      decode_json, encode_json, identifier, require, safe_relative,
                      validate_episode, validate_provenance, validate_row)
from .rules import describe_rules
from .timeline import Timeline, TimelineContext


class RecordingError(RuntimeError):
    """Operational recording failure; the engine action must never be retried."""


class PartialEpisodeError(RecordError):
    """The requested episode was never atomically confirmed."""


def _path(root, relative):
    """Resolve only literal, non-symlink descendants of the authorized root."""
    safe_relative(relative)
    current = root
    for part in relative.split('/'):
        current = current / part
        require(not current.is_symlink(), 'Symlink in recording path')
    try:
        current.resolve().relative_to(root)
    except ValueError as error:
        raise RecordError('Path escapes authorized root') from error
    return current


def _read_file(path, limit):
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Expected a regular JSON file')
            require(os.fstat(stream.fileno()).st_size <= limit, 'File byte limit')
            payload = stream.read(limit + 1)
            require(len(payload) <= limit, 'File byte limit')
            return payload
    except OSError as error:
        raise RecordError('Cannot read recording file') from error


def _sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class JsonlEpisodeWriter:
    """Single-use Recorder[(channel_name, plain_row)], with explicit confirmation.

    Final directories are never overwritten. A .partial directory is visible
    throughout recording/failure, and cannot be read as a confirmed episode.
    """

    def __init__(self, destination, relative_path):
        safe_relative(relative_path)
        require(not relative_path.endswith('.partial'), 'The .partial final suffix is reserved')
        self.root = Path(destination).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.final = _path(self.root, relative_path)
        self.partial = _path(self.root, relative_path + '.partial')
        require(not self.final.exists() and not self.partial.exists(), 'Episode destination already exists')
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.partial.mkdir()
        self._streams = {}
        self._info = {}
        self._bytes = 0
        self.failed = False
        self.confirmed = False
        self.closed = False
        self._status('recording')

    def _status(self, status):
        # Operational marker, deliberately not EpisodeManifestV1.
        with (self.partial / 'status.json').open('wb') as stream:
            stream.write(encode_json({'schema_version': 1, 'status': status}))
            stream.flush()
            os.fsync(stream.fileno())

    def _fail(self, error):
        self.failed = True
        self.close()
        try:
            self._status('writer_failed')
        except OSError:
            pass  # The original error and .partial directory remain visible.
        raise RecordingError('Episode writer failed; recording is partial; do not retry the action') from error

    def append(self, record):
        require(not self.failed and not self.closed and not self.confirmed, 'Writer is not open')
        name, row = record
        validate_row(name, row)
        payload = encode_json(row) + b'\n'
        info = self._info.get(name)
        require(self._bytes + len(payload) <= MAX_EPISODE_BYTES, 'Episode byte limit')
        require(info is None or info['rows'] < MAX_ROWS, 'Channel row limit')
        try:
            if info is None:
                self._open_channel(name)
                info = self._info[name]
            written = self._streams[name].write(payload)
            if written != len(payload):
                raise OSError('Short JSONL write')
            info['rows'] += 1
            info['bytes'] += len(payload)
            info['digest'].update(payload)
            self._bytes += len(payload)
        except OSError as error:
            self._fail(error)

    def _open_channel(self, name):
        path = _path(self.partial, CHANNEL_FILES[name])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._streams[name] = path.open('xb')
        self._info[name] = {'path': CHANNEL_FILES[name], 'schema_version': 1,
                            'rows': 0, 'bytes': 0, 'digest': hashlib.sha256()}

    def flush(self):
        require(not self.failed and not self.closed, 'Writer is not open')
        try:
            for stream in self._streams.values():
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            self._fail(error)

    def confirm(self, manifest, channels):
        require(not self.failed and not self.closed and not self.confirmed, 'Writer is not open')
        try:
            for name in channels:
                require(name in CHANNEL_FILES, 'Unknown storage channel')
                if name not in self._info:
                    self._open_channel(name)
            self.flush()
            data = deepcopy(manifest)
            data['files'] = {name: {**{k: v for k, v in info.items() if k != 'digest'},
                                    'sha256': info['digest'].hexdigest()}
                             for name, info in self._info.items()}
            validated = EpisodeManifestV1(data)
            # Confirmation also validates actual persisted bytes and every reference.
            rows = _load_rows(self.partial, validated.to_json(), list(self._info))
            validate_episode(validated.to_json(), rows)
            with (self.partial / 'manifest.json').open('xb') as stream:
                stream.write(encode_json(data))
                stream.flush()
                os.fsync(stream.fileno())
            self.close()
            for directory in sorted({p.parent for p in self.partial.rglob('*.jsonl')}, key=str):
                _sync_directory(directory)
            self._status('confirmed')
            _sync_directory(self.partial)
            require(not self.final.exists(), 'Episode destination appeared before confirmation')
            os.rename(self.partial, self.final)
            self.confirmed = True
            return validated
        except (OSError, ValueError) as error:
            self._fail(error)

    def close(self):
        error = None
        for stream in self._streams.values():
            if not stream.closed:
                try:
                    stream.close()
                except OSError as caught:
                    error = caught
        self.closed = True
        if error is not None:
            self.failed = True
            raise RecordingError('Failed closing episode writer; recording is partial') from error


def _load_rows(directory, manifest, names):
    require(type(names) in (list, tuple) and all(type(n) is str for n in names) and
            len(names) == len(set(names)), 'Select explicit unique channel names')
    require(not set(names) - set(manifest['files']), 'Missing requested channel')
    rows = {}
    for name in names:
        info = manifest['files'][name]
        payload = _read_file(_path(directory, info['path']), MAX_EPISODE_BYTES)
        require(len(payload) == info['bytes'] and hashlib.sha256(payload).hexdigest() == info['sha256'],
                'Channel byte count/hash mismatch')
        require(not payload or payload.endswith(b'\n'), 'Partial JSONL line')
        lines = payload.splitlines()
        require(len(lines) == info['rows'] and len(lines) <= MAX_ROWS, 'Channel row count mismatch')
        rows[name] = []
        for line in lines:
            row = decode_json(line)
            validate_row(name, row)
            rows[name].append(row)
    return rows


class EpisodeReader:
    """Selective loading: inputs never open transition/event/target/audit files."""

    def __init__(self, destination, relative_path):
        self.root = Path(destination).resolve()
        self.directory = _path(self.root, relative_path)
        if relative_path.endswith('.partial') or not (self.directory / 'manifest.json').is_file():
            raise PartialEpisodeError('Episode has no confirmed manifest')
        self._manifest = EpisodeManifestV1(decode_json(
            _read_file(_path(self.directory, 'manifest.json'), MAX_RECORD_BYTES)))

    @property
    def manifest(self):
        return self._manifest.to_json()

    def read_channels(self, names):
        return _load_rows(self.directory, self.manifest, names)

    def iter_channel(self, name):
        """Yield validated rows with one bounded line in memory.

        Authenticate the selected file before yielding, then validate each row.
        Exhaust the iterator to check row count and the second-pass digest too.
        No other channel is opened. Closing the generator releases its file.
        """
        manifest = self.manifest
        require(name in manifest['files'], 'Missing requested channel')
        info = manifest['files'][name]
        path = _path(self.directory, info['path'])
        try:
            fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Expected a regular JSON file')
                require(os.fstat(stream.fileno()).st_size == info['bytes'], 'Channel byte count mismatch')
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    require(total <= info['bytes'], 'Channel byte count mismatch')
                    digest.update(chunk)
                require(total == info['bytes'] and digest.hexdigest() == info['sha256'],
                        'Channel byte count/hash mismatch')
                stream.seek(0)
                digest = hashlib.sha256()
                count, total = 0, 0
                while True:
                    line = stream.readline(MAX_RECORD_BYTES + 2)
                    if not line:
                        break
                    require(line.endswith(b'\n') and len(line) <= MAX_RECORD_BYTES + 1,
                            'Partial or oversized JSONL line')
                    count += 1
                    total += len(line)
                    require(count <= info['rows'] and total <= info['bytes'], 'Channel bounds exceeded')
                    digest.update(line)
                    row = decode_json(line[:-1])
                    validate_row(name, row)
                    yield row
                require(count == info['rows'] and total == info['bytes'] and
                        digest.hexdigest() == info['sha256'], 'Channel changed or row count mismatch')
        except OSError as error:
            raise RecordError('Cannot read recording file') from error

    def read_episode(self):
        manifest = self.manifest
        rows = self.read_channels(list(manifest['files']))
        validate_episode(manifest, rows)
        return {'manifest': manifest, 'channels': rows}

    def read_inputs(self):
        manifest = self.manifest
        profile = InputProfile.from_json(manifest['profile'])
        names = list(dict.fromkeys(['primary'] + [path.split('.')[0] for path, _ in profile.fields]))
        rows = self.read_channels(names)
        primary = rows['primary']
        require([r['observation_id'] for r in primary] == list(range(1, len(primary) + 1)),
                'Input observation IDs have gaps/duplicates')
        branches = {b['branch_id'] for b in manifest['branches']}
        for row in primary:
            ctx = row['context']
            require(ctx['episode_id'] == manifest['episode_id'] and ctx['branch_id'] in branches and
                    ctx['event_seq'] <= manifest['final_context']['event_seq'] and
                    ctx['decision_seq'] <= manifest['final_context']['decision_seq'], 'Foreign input context')
        lookups = {name: {r['observation_id']: r for r in values} for name, values in rows.items()}
        for boundary in ('initial', 'final'):
            ref = manifest[boundary + '_observation']
            require(ref in lookups['primary'] and lookups['primary'][ref]['context'] == manifest[boundary + '_context'],
                    'Missing or inconsistent input boundary')
        require(all(set(lookup) == set(lookups['primary']) for lookup in lookups.values()),
                'Input channel observation coverage mismatch')
        require(all(len(lookups[n]) == len(rows[n]) for n in names), 'Duplicate input observation ID')
        result = []
        for row in rows['primary']:
            ref = row['observation_id']
            channels = {}
            for name in names:
                require(ref in lookups[name], 'Missing authorized input observation')
                selected = lookups[name][ref]
                require(selected['context'] == row['context'], 'Input context mismatch')
                channels[name] = selected['channel']
            result.append({'observation_id': ref, 'context': deepcopy(row['context']),
                           **project_inputs(channels, profile)})
        return result


class _RecordingTimeline(Timeline):
    def __init__(self, game, recorder, episode_id, branch_id):
        self.recorder = recorder
        super().__init__(game, episode_id=episode_id, branch_id=branch_id)

    def _begin(self, action):
        self.recorder._check()
        if action is not None:
            self.recorder._pre = self.recorder._capture(self.recorder._observation)
        super()._begin(action)

    def _settle(self, failed=False):
        records = super()._settle(failed)
        if not self.recorder.failed:
            post = self.recorder._capture(self.recorder._observation)
            for record in records:
                self.recorder._capture(self.recorder._transition, record, post)
        return records

    def _emit(self, kind, data):
        super()._emit(kind, data)
        if not self.recorder.failed:
            event = self._events[-1].to_json()
            event.update(schema_version=1, payload_version=1,
                         event_id=[self.context.episode_id, self.context.branch_id, self.context.event_seq])
            self.recorder._capture(self.recorder._append, 'events', event)

    def _failure(self, error):
        super()._failure(error)
        if not self.recorder.failed:
            self.recorder._append('diagnostics', {'schema_version': 1, **self._operational_errors[-1]})

    def _macro_result(self, macro, before, result):
        super()._macro_result(macro, before, result)
        if not self.recorder.failed:
            self.recorder._append('macros', {'schema_version': 1, **self._macros[-1]})

    def _validate_checkpoint(self, checkpoint):
        raise RecordingError('A persistent recorder cannot rewind; use a new recording for restored execution')

    def fork(self, branch_id):
        self.recorder._check()
        identifier(branch_id)
        parent = self.context.to_json()
        super().fork(branch_id)
        self.recorder._branches.append({'branch_id': branch_id, 'parent': parent})


class EpisodeRecorder:
    """Attach at the empty timeline prefix; explicitly drive and finish an episode.

    `advance` also captures admission failures, which precede Timeline hooks.
    Direct Game/PolicyDriver/ActionControl advances still capture all accepted
    boundaries, including every primitive of a macro.
    """

    def __init__(self, game, destination, relative_path, *, episode_id, source_family,
                 scenario_id, policies, seed_plan, branch_id='root', profile=PRIMARY_PROFILE,
                 rule_trace=False, rule_trace_options=None, _session=None):
        for value in (episode_id, source_family, scenario_id, branch_id):
            identifier(value)
        require(type(profile) is InputProfile, 'Expected InputProfile')
        profile.__post_init__()
        encode_json(policies)
        encode_json(seed_plan)
        self.game = game
        self.failed = False
        self.finished = False
        self._rows = {name: [] for name in ('primary', 'transitions', 'events', 'macros', 'diagnostics')}
        self._bytes = 0
        self._pre = None
        self._source = source_family
        self._scenario = scenario_id
        self._profile = profile
        self._branches = [{'branch_id': branch_id, 'parent': None}]
        self._provenance = {'rules': describe_rules(game.config, game.ruleset, game.arena,
                                                   game.state.home_team, game.state.away_team).to_json(),
                            'policies': deepcopy(policies), 'seed_plan': deepcopy(seed_plan)}
        encode_json(self._provenance)
        validate_provenance(self._provenance)
        previous = game.timeline
        if _session is None:
            self.timeline = _RecordingTimeline(game, self, episode_id, branch_id)
        else:
            require(type(previous) is Timeline and previous.context.decision_seq == 0
                    and not previous._decisions and not previous._macros
                    and not previous._operational_errors and previous._pending is None
                    and previous.context.episode_id == episode_id
                    and previous.context.branch_id == branch_id,
                    'Session recording requires an untouched coach-decision prefix')
            # Transfer the already validated synthetic prefix without replaying
            # setup, reseeding, resetting counters or dropping uncaused events.
            self.timeline = object.__new__(_RecordingTimeline)
            self.timeline.__dict__.update(vars(previous))
            self.timeline.recorder = self
            game.timeline = self.timeline
        self._initial_context = TimelineContext(episode_id, branch_id).to_json()
        try:
            self.writer = JsonlEpisodeWriter(destination, relative_path)
        except Exception:
            game.timeline = previous
            raise
        try:
            if _session is not None:
                self._append('primary', {'schema_version': 1, 'observation_id': 1,
                    'context': self._initial_context, 'channel': _session._recording_initial})
                self._initial_observation = 1
                for captured in self.timeline.events:
                    event = captured.to_json()
                    event.update(schema_version=1, payload_version=1,
                                 event_id=[episode_id, branch_id, captured.context.event_seq])
                    self._append('events', event)
                self._observation()
            else:
                self._initial_observation = self._observation()
        except BaseException:
            self.writer.close()
            game.timeline = previous
            raise
        self.rule_trace = None
        if rule_trace:
            from .rule_traces import RuleTrace
            self.rule_trace = RuleTrace(game, rules=self._provenance['rules'], **(rule_trace_options or {}))

    def _check(self):
        if self.failed or self.finished:
            raise RecordingError('Recorder is failed or finished; no further recorded advance is allowed')

    def _capture(self, operation, *args):
        try:
            return operation(*args)
        except (ValueError, TypeError) as error:
            self.failed = True
            raise RecordingError('Recorder capture failed; recording is partial; do not retry the action') from error

    def _append(self, name, row):
        try:
            validate_row(name, row)
            payload = encode_json(row)
            require(self._bytes + len(payload) + 1 <= MAX_EPISODE_BYTES, 'Episode byte limit')
            require(len(self._rows.setdefault(name, [])) < MAX_ROWS, 'Channel row limit')
            self._rows[name].append(decode_json(payload))
            self._bytes += len(payload) + 1
        except ValueError as error:
            self.failed = True
            raise RecordingError('Recorder rejected data; recording is partial; do not retry the action') from error

    def _observation(self):
        channel = make_channel('primary', observe(self.game, self.timeline._entities, 'home').to_json())
        ctx = self.timeline.context.to_json()
        previous = self._rows['primary'][-1] if self._rows['primary'] else None
        if previous is not None and previous['channel'] == channel and previous['context'] == ctx:
            return previous['observation_id']
        ref = len(self._rows['primary']) + 1
        self._append('primary', {'schema_version': 1, 'observation_id': ref, 'context': ctx, 'channel': channel})
        return ref

    def _transition(self, envelope, post):
        data = envelope.to_json()
        row = {key: data[key] for key in ('before', 'after', 'actor_id', 'action', 'event_start',
                                         'event_stop', 'next_actor_id', 'status', 'macro_id', 'primitive_order')}
        row.update(schema_version=1, transition_id=[data['after']['episode_id'], data['after']['branch_id'],
                                                    data['after']['decision_seq']],
                   source_family=self._source, scenario_id=self._scenario,
                   pre_observation=self._pre, post_observation=post,
                   end={'kind': 'terminal', 'reason': 'game_over'} if data['terminal'] else None)
        row = TransitionV1(row).to_json()
        index = data['after']['decision_seq'] - 1
        if index < len(self._rows['transitions']):
            previous = self._rows['transitions'].pop()
            self._bytes -= len(encode_json(previous)) + 1
            require(index == len(self._rows['transitions']), 'Cannot rewrite an earlier decision')
        self._append('transitions', row)

    @property
    def records(self):
        return deepcopy(self._rows)

    def advance(self, action=None, *, max_steps=100000):
        self._check()
        failures = len(self._rows['diagnostics'])
        try:
            if type(action) is ActionRequestV1:
                action = self.actions.decode(action)
            return self.game.advance(action, max_steps=max_steps)
        except Exception as error:
            if not self.failed and len(self._rows['diagnostics']) == failures:
                self.timeline._failure(error)
            raise

    @property
    def actions(self):
        if not hasattr(self, '_actions'):
            self._actions = ActionControl(self.game, self.timeline._entities)
        return self._actions

    def execute_macro(self, macro, *, max_steps=100000):
        self._check()
        failures = len(self._rows['diagnostics'])
        try:
            return self.actions.execute_macro(macro, max_steps=max_steps)
        except Exception as error:
            if not self.failed and len(self._rows['diagnostics']) == failures:
                self.timeline._failure(error)
            raise

    def append_channel(self, name, observation_id, data):
        """Explicit trusted producer data; never inferred from engine internals."""
        self._check()
        require(name in ('derived', 'control', 'evaluation', 'privileged'), 'Not a side channel')
        require(type(observation_id) is int and 1 <= observation_id <= len(self._rows['primary']),
                'Unknown observation reference')
        require(all(row['observation_id'] != observation_id for row in self._rows.get(name, [])),
                'Duplicate side-channel observation')
        row = {'schema_version': 1, 'observation_id': observation_id, 'channel': make_channel(name, data)}
        if name in ('derived', 'control'):
            row['context'] = self._rows['primary'][observation_id - 1]['context']
        self._append(name, row)

    def finish(self, *, truncation_reason=None):
        self._check()
        terminal = self.game.state.game_over
        require(terminal == (truncation_reason is None), 'Supply truncation reason exactly for a nonterminal episode')
        end = {'kind': 'terminal', 'reason': 'game_over'} if terminal else {
            'kind': 'truncated', 'reason': truncation_reason}
        identifier(end['reason'])
        final = self._observation()
        if self._rows['transitions'] and self._rows['transitions'][-1]['after'] == self.timeline.context.to_json():
            self._rows['transitions'][-1]['end'] = end
        manifest = {'schema_version': 1, 'episode_id': self.timeline.context.episode_id,
                    'source_family': self._source, 'scenario_id': self._scenario,
                    'initial_context': self._initial_context, 'final_context': self.timeline.context.to_json(),
                    'initial_observation': self._initial_observation, 'final_observation': final,
                    'end': end, 'provenance': self._provenance, 'profile': self._profile.to_json(),
                    'schemas': SCHEMAS, 'branches': self._branches, 'files': {}}
        try:
            if self.rule_trace is not None:
                self._rows['rule_traces'] = []
                for row in self.rule_trace.events:
                    self._append('rule_traces', row)
                self._append('rule_trace_status', self.rule_trace.status)
            for name, rows in self._rows.items():
                for row in rows:
                    self.writer.append((name, row))
            result = self.writer.confirm(manifest, list(self._rows))
            self.finished = True
            return result
        except (ValueError, OSError, RecordingError) as error:
            self.failed = True
            self.writer.close()
            raise RecordingError('Episode confirmation failed; do not retry any engine action') from error

    def close(self):
        """Close an unfinished writer without manufacturing an episode outcome."""
        self.writer.close()
        self.finished = True
