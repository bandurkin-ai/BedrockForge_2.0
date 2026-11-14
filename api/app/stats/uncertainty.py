"""Bootstrap and local (per-location) statistics over realization ensembles.

Vectorized NumPy re-implementations of ``bootstrap``, ``local_expectation``,
``local_standard_deviation``, ``local_percentile`` and
``local_probability_exceedance`` from ``geostats.py``. Differences: the
resampling is seeded (``numpy.random.Generator``) and reproducible, and the
``local_*`` functions skip NaN realizations (a cell with no valid realization
is NaN) instead of propagating them.
"""
import warnings

import numpy as np

from ._utils import as_1d, check_weights


def bootstrap(data, stat, n_boot=1000, weights=None, seed=None):
    """Bootstrap distribution of ``stat(sample)``.

    Samples are drawn with replacement, with probability proportional to
    ``weights`` (e.g. declustering weights); the statistic itself is then
    computed without weights, as in GSLIB-style workflows.

    :param stat: callable taking a 1D array and returning a number
    :param weights: optional non-negative weights, same length as ``data``
    :param seed: seed of the random generator (None = nondeterministic)
    :return: array of ``n_boot`` realizations of the statistic
    """
    data = as_1d(data, "data")
    if data.size == 0:
        raise ValueError("empty data")
    if not np.all(np.isfinite(data)):
        raise ValueError("data must be finite")
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    w = check_weights(weights, data.size)
    if w.sum() <= 0:
        raise ValueError("weights must not all be zero")
    rng = np.random.default_rng(seed)
    uniform = weights is None
    cdf = None if uniform else np.cumsum(w) / w.sum()
    out = np.empty(n_boot)
    for i in range(n_boot):
        if uniform:
            idx = rng.integers(0, data.size, data.size)
        else:
            idx = np.minimum(np.searchsorted(cdf, rng.random(data.size), side="right"), data.size - 1)
        out[i] = stat(data[idx])
    return out


def resample_groups(groups, n_boot, seed=None):
    """Cluster bootstrap: ``n_boot`` index arrays, each made of whole groups drawn with replacement.

    :param groups: 1D array of group labels (e.g. hole id of every sample)
    :return: list of integer index arrays into ``groups``
    """
    groups = np.asarray(groups)
    if groups.ndim != 1 or groups.size == 0:
        raise ValueError("groups must be a non-empty 1D array")
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    _, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(inverse.max() + 1)]
    rng = np.random.default_rng(seed)
    return [np.concatenate([members[g] for g in rng.integers(0, len(members), len(members))])
            for _ in range(n_boot)]


def _stack(realizations):
    r = np.asarray(realizations, dtype=float)
    if r.ndim < 1 or r.shape[0] == 0:
        raise ValueError("realizations must have a leading axis of at least 1 realization")
    return r


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # "all-NaN slice" is a legal, NaN result here
        return fn(*args, **kwargs)


def local_expectation(realizations):
    """Mean across realizations (axis 0), ignoring NaN."""
    return _quiet(np.nanmean, _stack(realizations), axis=0)


def local_std(realizations):
    """Standard deviation across realizations (axis 0, population), ignoring NaN."""
    return _quiet(np.nanstd, _stack(realizations), axis=0)


def local_percentile(realizations, p):
    """p-th percentile (0-100) across realizations (axis 0), ignoring NaN."""
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    return _quiet(np.nanpercentile, _stack(realizations), p, axis=0)


def local_prob_exceed(realizations, threshold):
    """Probability that a realization is >= ``threshold`` (axis 0), ignoring NaN."""
    r = _stack(realizations)
    valid = np.isfinite(r).sum(axis=0)
    hit = (np.where(np.isfinite(r), r, -np.inf) >= threshold).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(valid > 0, hit / valid, np.nan)
