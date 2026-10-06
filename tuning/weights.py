"""Weight sets as files, and a name for each one.

A weight set is a plain dict keyed by ai.WEIGHT_NAMES. On disk it is JSON, one object,
keys sorted, so that two files with the same weights are the same bytes. Its id is the
first twelve hex digits of the sha1 of that canonical text: enough to tell any two sets
apart in a results file, short enough to read in a table.

The id is taken over the COMPLETE set, defaults filled in, so a file that names three
weights and one that names all of them get the same id when they mean the same weights.
The flip side: adding a new term to ai.WEIGHT_NAMES changes every id, including the
default's, because the complete set has a new key. Results recorded before a term was added
still replay -- the games are in the file -- but their ids won't match a fresh run's, so a
resumed match starts over. Finish what is in flight before adding a term.
"""

import hashlib
import json

from royals_engine import ai as AI


def default():
    return dict(AI.DEFAULT_WEIGHTS)


def complete(weights):
    """A full weight set: the defaults with `weights` laid over them. Validates by the
    same rules setWeights uses, without putting anything in force."""
    full = default()
    for name, value in weights.items():
        if name not in AI.WEIGHT_RANGES:
            raise KeyError("no evaluation weight called %r" % (name,))
        if type(value) is not int:
            raise TypeError("%s must be an int, got %r" % (name, value))
        low, high = AI.WEIGHT_RANGES[name]
        if not low <= value <= high:
            raise ValueError("%s = %d is outside %d..%d" % (name, value, low, high))
        full[name] = value
    return full


def canonical(weights):
    return json.dumps(complete(weights), sort_keys=True, separators=(",", ":"))


def weight_id(weights):
    return hashlib.sha1(canonical(weights).encode("ascii")).hexdigest()[:12]


def save(weights, path):
    with open(path, "w") as f:
        f.write(json.dumps(complete(weights), sort_keys=True, indent=2) + "\n")


def load(path):
    with open(path) as f:
        return complete(json.load(f))
