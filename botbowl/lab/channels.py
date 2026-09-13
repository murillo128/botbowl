"""Versioned data boundaries for encoders, controllers and target consumers.

Only plain JSON values enter the projector. This is an API boundary, not a
sandbox for code which already owns a Game. See docs/lab/channels.md.
"""
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
import json
import math
from typing import Literal, Union, get_args, get_origin

from .observations import DecisionContext, ObservationV1, Presence


CHANNELS = ("primary", "derived", "control", "evaluation", "privileged")


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _array(items):
    return {"type": "array", "items": items}


def _schema(hint, bindings=None):
    """Translate the accepted ObservationV1 types, never a data object's fields."""
    bindings = {} if bindings is None else bindings
    hint = bindings.get(hint, hint)
    origin, args = get_origin(hint), get_args(hint)
    if origin is Union:
        return {"anyOf": [_schema(item, bindings) for item in args]}
    if origin is Literal:
        return {**_schema(type(args[0])), "enum": list(args)}
    if origin is list:
        return _array(_schema(args[0], bindings))
    if is_dataclass(origin or hint):
        cls = origin or hint
        if origin:
            bindings = {**bindings, **dict(zip(cls.__parameters__, args))}
        result = _object({field.name: _schema(field.type, bindings) for field in fields(cls)})
        if cls is Presence:
            result["x-presence"] = True
        return result
    return {"type": {str: "string", int: "integer", bool: "boolean", type(None): "null"}[hint]}


_PLANE = _array(_array({"type": "number"}))
_DERIVED = _object({name: _PLANE for name in (
    "own_tackle_zones", "opp_tackle_zones", "roll_probabilities", "block_dice")}, required=[])
_CONTROL = _object({
    "action_mask": _array({"type": "boolean"}),
    "action_ids": _array({"type": "string"}),
    "context": _schema(DecisionContext),
}, required=[])
_SCHEMAS = {
    "primary": _schema(ObservationV1), "derived": _DERIVED, "control": _CONTROL,
    # Target names/values and provenance are intentionally extensible JSON.
    # Neither this channel nor any of its descendants is a model feature.
    "evaluation": _object({"labels": {"type": "object"},
                           "estimates": {"type": "object"},
                           "provenance": {"type": "object"}}),
    "privileged": {"type": "object"},
}
_SCHEMA_NAMES = {name: name.title() + "V1" for name in CHANNELS}
_SCHEMA_NAMES["primary"] = "ObservationV1"
_METADATA_SCHEMA = _object({name: {"type": "string"} for name in (
    "episode_id", "source_id", "content_hash", "provenance")}, required=[])


def _json_copy(value):
    """Reject custom objects, non-string keys, tuples and non-finite numbers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [_json_copy(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _json_copy(item) for key, item in value.items()}
    raise ValueError("Expected plain, finite JSON data")


def _validate(schema, value, path):
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate(option, value, path)
                return
            except ValueError:
                pass
        raise ValueError("Incompatible nullable field: " + path)
    kind = schema["type"]
    valid = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "integer": type(value) is int,
             "boolean": type(value) is bool, "null": value is None,
             "number": type(value) in (int, float)}[kind]
    if not valid or (type(value) is float and not math.isfinite(value)):
        raise ValueError("Incompatible type at " + path + ": expected " + kind)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Unknown value/version at " + path)
    if kind == "object" and "properties" in schema:
        properties = schema["properties"]
        if set(value) - set(properties) or set(schema["required"]) - set(value):
            raise ValueError("Unknown or missing fields at " + path)
        for key, item in value.items():
            _validate(properties[key], item, path + "." + key)
        if schema.get("x-presence") and value["present"] is not (value["value"] is not None):
            raise ValueError("Inconsistent presence at " + path)
    elif kind == "array":
        for item in value:
            _validate(schema["items"], item, path + "[]")


def _leaves(schema, path):
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            if option.get("type") != "null":
                yield from _leaves(option, path)
    elif schema["type"] == "object":
        for key, child in schema["properties"].items():
            yield from _leaves(child, path + "." + key)
    elif schema["type"] == "array":
        yield from _leaves(schema["items"], path + "[]")
    else:
        yield path, schema["type"]


# No categorical-ID exception is defined in V1. Side references and slots also
# stay out of encoder features; metadata preserves their entity/list alignment.
_METADATA_PATHS = frozenset((
    "primary.schema_version", "primary.observer_team", "primary.players[].id",
    "primary.players[].team", "primary.players[].slot", "primary.teams[].id",
    "primary.balls[].carrier.value", "primary.match.kicking_team.value",
    "primary.match.receiving_team.value", "primary.decision.actor_team.value",
    "primary.decision.active_team.value", "primary.decision.active_player.value",
    "primary.decision.subject.value", "primary.decision.target_player.value",
    "primary.decision.options[].team.value", "primary.decision.sufficiency",
))
_PRIMARY_FIELDS = tuple((path, kind) for path, kind in _leaves(_SCHEMAS["primary"], "primary")
                        if path not in _METADATA_PATHS)
_DERIVED_FIELDS = tuple(_leaves(_DERIVED, "derived"))
_MASK_FIELD = ("control.action_mask[]", "boolean")
_ALLOWED = dict(_PRIMARY_FIELDS + _DERIVED_FIELDS + (_MASK_FIELD,))


@dataclass(frozen=True)
class InputProfile:
    """An immutable, closed allowlist. `[]` means one typed array dimension."""
    name: str
    fields: tuple
    include_action_mask: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unknown InputProfile version")
        if type(self.name) is not str or self.name not in ("primary", "enriched", "custom"):
            raise ValueError("Unknown InputProfile name")
        if type(self.include_action_mask) is not bool or type(self.fields) is not tuple:
            raise ValueError("Invalid profile types")
        seen = set()
        for field in self.fields:
            if (type(field) is not tuple or len(field) != 2
                    or any(type(item) is not str for item in field)):
                raise ValueError("Invalid profile field")
            path, kind = field
            if path in seen or path not in _ALLOWED or _ALLOWED[path] != kind:
                raise ValueError("Forbidden, unknown or incompatible profile path/type: " + path)
            seen.add(path)
        if self.include_action_mask != (_MASK_FIELD[0] in seen):
            raise ValueError("Action mask requires an explicit matching option and path")
        if self.name != "custom":
            expected = _PRIMARY_FIELDS + (_DERIVED_FIELDS if self.name == "enriched" else ())
            expected += (_MASK_FIELD,) if self.include_action_mask else ()
            if self.fields != expected:
                raise ValueError("Fields do not match the named profile")

    def to_json(self):
        return {"schema_version": self.schema_version, "name": self.name,
                "fields": [{"path": path, "type": kind} for path, kind in self.fields],
                "include_action_mask": self.include_action_mask}

    @classmethod
    def from_json(cls, data):
        _keys(data, ("schema_version", "name", "fields", "include_action_mask"))
        if type(data["fields"]) is not list:
            raise ValueError("Profile fields must be an array")
        for field in data["fields"]:
            _keys(field, ("path", "type"))
        return cls(data["name"], tuple((item["path"], item["type"]) for item in data["fields"]),
                   data["include_action_mask"], data["schema_version"])


def input_profile(name="primary", include_action_mask=False):
    if type(name) is not str or name not in ("primary", "enriched"):
        raise ValueError("Unknown named InputProfile")
    selected = _PRIMARY_FIELDS + (_DERIVED_FIELDS if name == "enriched" else ())
    selected += (_MASK_FIELD,) if include_action_mask else ()
    return InputProfile(name, selected, include_action_mask)


PRIMARY_PROFILE = input_profile()
ENRICHED_PROFILE = input_profile("enriched")


def _keys(data, expected):
    if type(data) is not dict or set(data) != set(expected):
        raise ValueError("Unknown or missing envelope fields")


def _name(name):
    if type(name) is not str or name not in CHANNELS:
        raise ValueError("Unknown channel")


def channel_descriptor(name):
    _name(name)
    return {"channel": name, "schema": _SCHEMA_NAMES[name], "schema_version": 1}


def channel_schema(name):
    """Return an independent JSON-schema descriptor, including Presence semantics."""
    _name(name)
    return deepcopy(_SCHEMAS[name])


def _validate_descriptor(name, descriptor):
    _keys(descriptor, ("channel", "schema", "schema_version"))
    if (type(descriptor["schema_version"]) is not int
            or type(descriptor["channel"]) is not str
            or type(descriptor["schema"]) is not str
            or descriptor != channel_descriptor(name)):
        raise ValueError("Unknown or mismatched channel schema/version")


def make_channel(name, data, metadata=None):
    """Validate and copy one channel. Passing ObservationV1 needs .to_json()."""
    _name(name)
    _validate(_SCHEMAS[name], data, name)
    _validate(_METADATA_SCHEMA, {} if metadata is None else metadata, name + ".metadata")
    return {"descriptor": channel_descriptor(name), "data": _json_copy(data),
            "metadata": _json_copy({} if metadata is None else metadata)}


def _read_channel(name, envelope):
    _keys(envelope, ("descriptor", "data", "metadata"))
    _validate_descriptor(name, envelope["descriptor"])
    return make_channel(name, envelope["data"], envelope["metadata"])


def _channel_names(channels):
    if type(channels) is not dict:
        raise ValueError("Channels must be a plain dictionary")
    for name in channels:
        _name(name)


def _extract(data, path):
    if data is None:
        return None  # A validated absent Presence ancestor, never a missing key.
    if not path:
        return _json_copy(data)
    field, *rest = path
    parts = field.split("[]")
    value = data[parts[0]]

    def descend(item, dimensions):
        if dimensions:
            return [descend(child, dimensions - 1) for child in item]
        return _extract(item, rest)

    return descend(value, len(parts) - 1)


def project_inputs(channels, profile=PRIMARY_PROFILE):
    """Pure allowlist projection; only selected channel payloads are accessed.

    Feed only `features` to an encoder. It contains JSON leaves/arrays, not a
    padded tensor. Categorical encoding and padding belong to that encoder.
    """
    if type(profile) is not InputProfile:
        raise ValueError("Expected a known InputProfile")
    profile.__post_init__()
    _channel_names(channels)
    selected = dict.fromkeys(path.split(".")[0] for path, _ in profile.fields)
    data, metadata = {}, {}
    for name in selected:
        if name not in channels:
            raise ValueError("Missing selected channel: " + name)
        envelope = _read_channel(name, channels[name])
        data[name] = envelope["data"]
        metadata[name] = {"descriptor": envelope["descriptor"], "source": envelope["metadata"]}
    features = {}
    for path, _ in profile.fields:
        try:
            features[path] = _extract(data, path.split("."))
        except KeyError as error:
            raise ValueError("Missing selected path: " + path) from error
    if "primary" in data:
        metadata["primary"]["fields"] = {
            path: _extract(data, path.split(".")) for path in sorted(_METADATA_PATHS)}
    return {"features": features, "metadata": {"profile": profile.to_json(), "channels": metadata}}


def select_channels(channels, names):
    """Separate controller/target access; no implicit selection of other channels."""
    _channel_names(channels)
    names = _selection(names)
    result = {}
    for name in names:
        if name not in channels:
            raise ValueError("Missing selected channel: " + name)
        result[name] = _read_channel(name, channels[name])
    return result


def _selection(names):
    if type(names) not in (list, tuple) or any(type(name) is not str for name in names):
        raise ValueError("Select an explicit list of channel names")
    for name in names:
        _name(name)
    if len(set(names)) != len(names):
        raise ValueError("Duplicate channel selection")
    return names


def export_channels(channels, names, profile=PRIMARY_PROFILE):
    """Return (manifest, independent JSON blobs) for an explicit export selection.

    A recorder can store each blob separately. No dataset or storage API is
    required, and privileged data is never embedded in the manifest.
    """
    if type(profile) is not InputProfile:
        raise ValueError("Expected a known InputProfile")
    profile.__post_init__()
    selected = select_channels(channels, names)
    descriptors = {name: channel_descriptor(name) for name in selected}
    manifest = {"format_version": 1, "channels": descriptors, "profile": profile.to_json()}
    blobs = {name: json.dumps(envelope, allow_nan=False, sort_keys=True)
             for name, envelope in selected.items()}
    return manifest, blobs


def import_channels(manifest, load_blob, names):
    """Load only named blobs via load_blob(channel_name), before any projection.

    The loader belongs to storage/control code and is never passed to the
    projector. The returned profile describes the export, not target authority.
    """
    _keys(manifest, ("format_version", "channels", "profile"))
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise ValueError("Unknown channel export version")
    profile = InputProfile.from_json(manifest["profile"])
    descriptors = manifest["channels"]
    _channel_names(descriptors)
    for name, descriptor in descriptors.items():
        _validate_descriptor(name, descriptor)
    names = _selection(names)
    if set(names) - set(descriptors):
        raise ValueError("Missing selected channel descriptor")
    result = {}
    for name in names:
        payload = load_blob(name)
        if type(payload) is not str:
            raise ValueError("Expected a JSON channel blob string")
        result[name] = _read_channel(name, json.loads(payload, object_pairs_hook=_unique_keys))
    return result, profile


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result
