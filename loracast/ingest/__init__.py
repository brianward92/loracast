"""Podcast transcript ingestion: discover episodes, acquire transcripts, ASR fallback."""

from .pipeline import PodcastPipeline

__all__ = ["PodcastPipeline"]
