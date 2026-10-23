import subprocess

import pytest

from hoahrb_jwts.errors import PublicationError
from hoahrb_jwts.major_checkout import sync_major_checkout


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)


def test_sync_fast_forwards_and_protects_dirty_checkout(tmp_path, monkeypatch):
    from hoahrb_jwts import major_checkout

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "checkout", "-b", "main")
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "commit.gpgsign", "false")
    (seed / "data").write_text("first")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "first")
    git(seed, "push", "origin", "main")
    target = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "-b", "main", str(remote), str(target)], check=True, capture_output=True
    )
    original = major_checkout._git

    def local_remote(path, *args):
        if args == ("remote", "get-url", "origin"):
            return major_checkout.MAJOR_DATA_URL
        return original(path, *args)

    monkeypatch.setattr(major_checkout, "_git", local_remote)
    (target / "contribution").write_text("keep me")
    sync_major_checkout(target)  # Dirty but already current is fine.
    (seed / "data").write_text("second")
    git(seed, "commit", "-am", "second")
    git(seed, "push", "origin", "main")
    with pytest.raises(PublicationError, match="local changes"):
        sync_major_checkout(target)
    assert (target / "data").read_text() == "first"
    assert (target / "contribution").read_text() == "keep me"
    (target / "contribution").unlink()
    sync_major_checkout(target)
    assert (target / "data").read_text() == "second"
