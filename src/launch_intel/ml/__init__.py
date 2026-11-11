"""Machine Learning subpackage for Launch Intelligence.
Provides trajectory forecasting (Bass diffusion & Gradient Boosting),
ensemble anomaly detection (Isolation Forest + MAD), and aspect-based sentiment NLP.
"""

from .anomalies import EnsembleAnomalyDetector, AnomalyEpisode
from .sentiment import AspectSentimentAnalyzer, SentimentSummary
from .trajectory import MLTrajectoryForecaster, TrajectoryPrediction, fit_bass_diffusion

__all__ = [
    "EnsembleAnomalyDetector",
    "AnomalyEpisode",
    "AspectSentimentAnalyzer",
    "SentimentSummary",
    "MLTrajectoryForecaster",
    "TrajectoryPrediction",
    "fit_bass_diffusion",
]
