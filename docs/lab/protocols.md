# Public lab roles

`botbowl.lab` exports `Simulation`, `Policy`, `Observer`, `Recorder`, `Scenario`,
`Evaluator` and `LegacyBotAdapter`. The six roles are `typing.Protocol`
contracts in `botbowl.lab.protocols`; implementations satisfy them structurally,
without inheriting a framework or `Game`. Imports use no optional integrations,
plugin discovery, dataset-directed imports or executable configuration.

| Role and type parameters (in order) | Explicit operations |
| --- | --- |
| `Simulation[Observation, Control, Action, Result, Snapshot]` | `reset(seed=None) -> None`, `observe(control) -> Observation`, `legal_actions(control) -> Sequence[Action]`, `step(action) -> Result`, `snapshot() -> Snapshot`, `restore(snapshot) -> None`, `close() -> None` |
| `Policy[Observation, Control, RNG, Action]` | `act(observation, control, rng) -> Action` |
| `Observer[Observation, RNG, Transformed]` | `transform(observation, rng) -> Transformed` |
| `Recorder[Record]` | `append(record) -> None`, `flush() -> None`, `close() -> None` |
| `Scenario[Factory, Built]` | `build(factory, seed) -> Built` |
| `Evaluator[Context, Evaluation]` | `evaluate(context) -> Evaluation` |

Seed arguments are `Optional[int]`; concrete implementations document accepted
ranges and reproducibility. All arguments except `reset`'s seed are positional
in the protocol, so structural implementations need not match parameter names.
Input-only parameters are contravariant and output-only parameters covariant.
Simulation actions and snapshots are invariant because they enter and leave it.
An implementation defines its result, action, factory and data types; this
delivery does not define a universal record schema or RNG API.

## Ownership and mutability

The caller owns each constructed role and explicitly closes resources it owns.
Inputs are borrowed for that call and must not be mutated, except that policy
and observer calls may advance their explicitly supplied RNG. Implementations
copy mutable inputs they retain, including records, actions and restoration
data. A scenario borrows the explicitly supplied factory and returns an instance
owned by its caller; factory lifetime and any shared resources remain the
factory provider's responsibility.

Returned observations, legal actions, transformed values, step/evaluation
results and snapshots are detached caller-owned data or deeply immutable
values. Mutating a returned list or a nested value must not change the
simulation, the source observation, or a retained record. A frozen dataclass
containing mutable lists is not deeply immutable. Implementations may share
deeply immutable values. Live `Game`, `Team`, `Player`, engine-backed `Action`
targets and RNG objects must not escape through these data outputs.

`reset` resets simulation-owned state; observation is requested separately.
`observe` and `legal_actions` do not advance the simulation. `step` applies the
explicitly supplied action and returns the implementation's result. Snapshot
and restore are controller operations: snapshots can contain privileged data,
including copied RNG state, and must stay outside restricted callback inputs.
Concrete simulation implementations own snapshot compatibility, validation,
failure semantics and lifecycle details; these protocols do not make existing
`Game` satisfy `Simulation` or supply an engine snapshot implementation.

`flush` and `close` run only when called. Concrete recorders document whether
their `close` flushes; the example flushes explicitly. Closing a simulation
does not call a policy, observer, recorder or evaluator. Callback exceptions
propagate to their explicit caller; there is no automatic retry or scheduler.

## Authority and evaluation separation

The controller keeps the simulation/engine and supplies only the view and
decision control authorized for the policy's seat and task. Control may carry
detached action identifiers, masks or decision metadata, never an engine handle,
engine-bound callable, snapshot or privileged state. `observe(control)` and
`legal_actions(control)` implementations must enforce that view/decision scope;
possession of a controller's simulation reference is itself privileged.

Observers receive an already authorized observation and can only derive from
it. They do not gain additional observation authority. Policy/observer RNGs
are explicitly allocated caller-owned streams, separate from engine RNG and
from each other; passing an engine RNG, alias or engine-bound RNG method would
violate this contract. Providers must also avoid capturing privileged objects
in callback instances/closures. Python protocols check signatures, not aliasing,
authorization or malicious code: these are integration contracts, not a sandbox
or runtime security certification. They are intentionally not runtime-checkable.

The evaluator receives a separately constructed evaluation context. That context
may contain explicitly authorized targets or privileged data for evaluation;
neither it nor its results are automatically inserted into policy/observer
inputs or a training channel. Only the caller explicitly invokes evaluation.
The existing [observation](observations.md) and [channel](channels.md) boundaries
retain their own schemas and authority. There is no implicit wiring to them.

## Legacy compatibility

`botbowl.Policy` remains the existing `callable(Game) -> Action` consumed by
`PolicyDriver`. `botbowl.lab.Policy` has the distinct restricted three-argument
`act` contract. Existing public imports and metadata submodules remain valid;
no API is removed or deprecated and no replacement schedule is claimed.

`LegacyBotAdapter(bot)` borrows an existing `Agent` and declares
`requires_game_access = True`. Construction does not call the bot. Explicit
`new_game(game, team)`, `act(game)` (also `adapter(game)`) and `end_game(game)`
forward the actual objects and propagate callback exceptions unchanged. The
adapter can be passed to the existing `PolicyDriver`. It grants full mutable
Game access, including privileged RNG and team state; returned legacy actions
may reference engine players. It does not satisfy the restricted lab policy
contract or enforce its detached-data ownership rules. The caller owns bot RNG,
initialization and finalization; it must avoid double initialization if the
existing factory's `control="policy"` already called the bot's `new_game`.

## Executable evidence

`examples/lab_protocols.py` supplies typed, minimal in-memory doubles for all six
roles and explicitly composes one toy increment. Its tuple checkpoint and
integer evaluator are only examples, not engine snapshots, datasets, storage or
an evaluation system. Run against an installed package:

```sh
python examples/lab_protocols.py
pytest tests/lab/test_protocols.py
pip install -r requirements/public-types.txt
python tools/ci/check_lab_types.py
python tools/ci/check_public_types.py
```

The strict bounded lab check accepts the protocol roots, real example and
structural/variance examples, and rejects each marked incorrect role signature
and legacy/restricted mismatch. It follows legacy imports silently without
global ignores; this does not claim full-engine strict typing. Lint CI runs the
type checks; ordinary core CI runs the runtime, import and example tests against
installed Python/native wheels. Import guards reject even attempted imports of
Flask, Gym/Gymnasium, pygame, torch and NFL modules, plus plugin discovery.
