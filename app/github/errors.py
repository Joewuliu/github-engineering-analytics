class GitHubError(Exception):
    """Base class for all GitHub integration errors."""


class GitHubRepositoryNotFoundError(GitHubError):
    """The requested repository does not exist on GitHub."""


class GitHubAuthenticationError(GitHubError):
    """GitHub rejected our credentials."""


class GitHubRateLimitError(GitHubError):
    """GitHub's API rate limit has been exceeded."""


class GitHubServerError(GitHubError):
    """GitHub returned a 5xx server error."""


class GitHubConnectionError(GitHubError):
    """A network-level failure occurred while contacting GitHub."""


class GitHubTimeoutError(GitHubError):
    """The request to GitHub timed out."""


class GitHubResponseError(GitHubError):
    """GitHub returned a response we could not understand."""
