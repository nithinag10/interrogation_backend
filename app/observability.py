import os


def enable_langsmith(project: str, endpoint: str | None = None) -> None:
    """Enable LangSmith tracing via environment variables."""
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = project
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint


def is_langsmith_configured() -> bool:
    return bool(os.getenv("LANGSMITH_API_KEY"))
