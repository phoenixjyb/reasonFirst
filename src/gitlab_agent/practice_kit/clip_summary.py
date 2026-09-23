"""Intentionally minimal teaching baseline; not production media/robot code."""


def summarize_clips(durations):
    """Summarize ordinary numeric durations; strict validation is the exercise."""
    values = list(durations)
    total = sum(values)
    return {
        "count": len(values),
        "total_seconds": total,
        "mean_seconds": total / len(values) if values else 0.0,
    }
