"""Statistics module: transforms, declustering, variograms, uncertainty.

Clean, vectorized (NumPy/SciPy) API; GSLIB-compatible results are checked
against ``geostatspy.geostats`` in tests.
"""
from .transforms import nscore, backtr, NormalScoreTransform
from .declustering import cell_declus, polygonal_declus
from .variogram import experimental_variogram, variogram_map, VariogramModel
from .estimation import idw
from .uncertainty import (bootstrap, resample_groups, local_expectation, local_std,
                          local_percentile, local_prob_exceed)

__all__ = [
    "nscore", "backtr", "NormalScoreTransform",
    "cell_declus", "polygonal_declus",
    "experimental_variogram", "variogram_map", "VariogramModel",
    "idw", "bootstrap", "resample_groups", "local_expectation", "local_std",
    "local_percentile", "local_prob_exceed",
]
