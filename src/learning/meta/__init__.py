"""
Meta-Labeling Engine (Stage 3).
Estimates the probability that an SMC/ICT setup reaches its target before its stop
using triple-barrier labeling, strictly causal features, and probability calibration.
"""

from .labeling import TripleBarrierLabeler, BarrierLabel
from .features import MetaFeatureExtractor, MetaFeatureVector
from .model import MetaClassifierModel, ModelRegistryEntry
from .meta_classifier import MetaLabelingController

__all__ = [
    "TripleBarrierLabeler",
    "BarrierLabel",
    "MetaFeatureExtractor",
    "MetaFeatureVector",
    "MetaClassifierModel",
    "ModelRegistryEntry",
    "MetaLabelingController",
]
