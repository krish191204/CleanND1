"""ML module: training, retraining, active learning."""
from .features import extract_bot_features
from .train_bot import train_bot_classifier, build_seed_dataset
from .retrain import retrain_bot_classifier, evaluate_models
from .active_learning import select_for_labeling, diversity_sample

__all__ = [
    "extract_bot_features",
    "train_bot_classifier",
    "build_seed_dataset",
    "retrain_bot_classifier",
    "evaluate_models",
    "select_for_labeling",
    "diversity_sample",
]