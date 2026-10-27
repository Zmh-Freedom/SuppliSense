"""Scheduling domain errors shared by the repository and executor."""


class ExecutionDenied(RuntimeError):
    """The job must not reach an external provider."""
