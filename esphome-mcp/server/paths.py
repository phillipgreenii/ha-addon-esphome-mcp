"""Filesystem containment helpers.

All user-supplied paths must pass through safe_join (for relative paths under a
base) or safe_filename (for basenames only) before being used for I/O.
"""
import os


class ContainmentError(ValueError):
    """Raised when a user-supplied path escapes its allowed base."""


def _is_inside(base_real: str, candidate_real: str) -> bool:
    return (
        candidate_real == base_real
        or candidate_real.startswith(base_real + os.sep)
    )


def safe_join(base: str, user_path: str) -> str:
    """Join user_path onto base, refusing anything that escapes base.

    Refuses: empty paths, null bytes, absolute paths, paths whose normalized
    form escapes base, paths that traverse through symlinks (parent or leaf)
    that resolve outside base.

    Returns the realpath of the joined result, so callers receive a fully-
    resolved path. This narrows but does not eliminate TOCTOU: a symlink
    swap between this call and the caller's I/O can still redirect access.
    Use O_NOFOLLOW for paranoid callers.
    """
    if not user_path or "\x00" in user_path:
        raise ContainmentError("empty or null-containing path")
    if os.path.isabs(user_path):
        raise ContainmentError(f"absolute path not allowed: {user_path!r}")

    base_real = os.path.realpath(base)
    candidate = os.path.normpath(os.path.join(base_real, user_path))

    if not _is_inside(base_real, candidate):
        raise ContainmentError(f"path escapes base: {user_path!r}")

    # Walk every existing ancestor (including the candidate itself if present)
    # and ensure each resolves inside base. Catches symlinked parents whose
    # leaf does not yet exist.
    walker = candidate
    while True:
        try:
            os.lstat(walker)
            exists = True
        except FileNotFoundError:
            exists = False
        except OSError as e:
            # Permission denied / other lstat failure: refuse rather than
            # treat as nonexistent.
            raise ContainmentError(
                f"could not check ancestor {walker!r}: {e.strerror}"
            ) from e

        if exists:
            real = os.path.realpath(walker)
            if not _is_inside(base_real, real):
                raise ContainmentError(
                    f"symlink escapes base via {walker!r}"
                )
            break
        if walker == base_real:
            # Reached base without finding an existing ancestor. Either base
            # itself does not exist yet (acceptable — caller may mkdir it) or
            # the candidate is at the base level. Either way, containment was
            # already proven by _is_inside above; stop walking.
            break
        parent = os.path.dirname(walker)
        if parent == walker:
            break
        walker = parent

    return os.path.realpath(candidate)


def safe_filename(name: str) -> str:
    """Validate a single filename (no directory components allowed)."""
    if not name or "\x00" in name:
        raise ContainmentError("empty or null-containing filename")
    if name in (".", ".."):
        raise ContainmentError(f"invalid filename: {name!r}")
    if os.sep in name or (os.altsep and os.altsep in name):
        raise ContainmentError(f"filename must not contain separators: {name!r}")
    return name
