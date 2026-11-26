"""Publish a verified daily asset, or finish publishing its pointer after a retry."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.release_seed import sha256_file, verify_release_seed


def gh(*args: str) -> str:
    return subprocess.check_output(
        ["gh", "release", *args, "--repo", os.environ["REPO"]], text=True
    )


def download_previous(directory: Path) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    assets = json.loads(gh("view", "data-latest", "--json", "assets"))["assets"]
    has_pointer = any(
        a["name"] == "data-latest.json" and a["state"].lower() == "uploaded" for a in assets
    )
    name = "data-latest.json" if has_pointer else "data-latest.pending.json"
    pointer_path = directory / "data-latest.json"
    gh("download", "data-latest", "--pattern", name, "--output", str(pointer_path))
    pointer = json.loads(pointer_path.read_bytes())
    as_of = date.fromisoformat(pointer["data_as_of"]).isoformat()
    asset_name = pointer["asset"]
    if pointer["tag"] != f"data-daily-{as_of}" or not re.fullmatch(r"[\w.-]+\.zip", asset_name):
        raise ValueError("invalid daily release pointer")
    archive = directory / asset_name
    gh("download", pointer["tag"], "--pattern", asset_name, "--output", str(archive))
    if sha256_file(archive) != pointer["sha256"]:
        raise ValueError("daily bundle checksum mismatch")
    if not has_pointer:
        if verify_release_seed(archive).data_as_of != as_of:
            raise ValueError("pending pointer date mismatch")
        gh("upload", "data-latest", str(pointer_path), "--clobber")
    return archive, as_of


def publish(bundle: Path, first: str, as_of: str, previous: str, commit: str) -> None:
    day = date.fromisoformat(as_of)
    if day <= date.fromisoformat(previous):
        print("No newer trading day to publish.")
        return
    if date.fromisoformat(first) > day:
        raise ValueError("bundle start date exceeds its end date")
    manifest = verify_release_seed(bundle)
    if manifest.data_as_of != as_of:
        raise ValueError("bundle manifest date differs from the release date")

    tag = f"data-daily-{as_of}"
    name = f"nanachi-tw-daily-{first}_to_{as_of}.zip"
    asset = bundle.with_name(name)
    bundle.rename(asset)
    digest = sha256_file(asset)
    try:
        gh(
            "create",
            tag,
            str(asset),
            "--target",
            commit,
            "--title",
            tag,
            "--latest=false",
            "--notes",
            f"台股公開資料包，日 K {first} ~ {as_of}，另含最近 60 個交易日的三大法人、"
            f"融資融券、交易日普查與處置紀錄。已通過隱私檢查。SHA256: {digest}",
        )
    except subprocess.CalledProcessError:
        # Rebuilds have a fresh manifest timestamp. Validate the published bytes,
        # then compare the actual member hashes before reusing the immutable release.
        release = json.loads(gh("view", tag, "--json", "tagName,isDraft,isPrerelease,assets"))
        if release["tagName"] != tag or release["isDraft"] or release["isPrerelease"]:
            raise ValueError(f"{tag} is not a published daily release") from None
        with TemporaryDirectory() as work:
            gh("download", tag, "--pattern", name, "--dir", work)
            existing = Path(work) / name
            digest = sha256_file(existing)
            metadata = [a for a in release["assets"] if a["name"] == name]
            if len(metadata) != 1 or metadata[0].get("digest") != "sha256:" + digest:
                raise ValueError(f"published asset digest mismatch for {tag}") from None
            published = verify_release_seed(existing)
            if published.model_dump(exclude={"created_at"}) != manifest.model_dump(
                exclude={"created_at"}
            ):
                raise ValueError(
                    f"published contents differ for {tag}; pointer preserved"
                ) from None

    pointer = {"tag": tag, "asset": name, "sha256": digest, "data_as_of": as_of}
    path = asset.with_name("data-latest.json")
    path.write_text(json.dumps(pointer) + "\n", encoding="utf-8")
    # gh --clobber deletes before uploading. Keep a recoverable pointer across that gap.
    pending = path.with_name("data-latest.pending.json")
    pending.write_bytes(path.read_bytes())
    gh("upload", "data-latest", str(pending), "--clobber")
    gh("upload", "data-latest", str(path), "--clobber")


if __name__ == "__main__":
    if sys.argv[1] == "download":
        archive, day = download_previous(Path(sys.argv[2]))
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as env_file:
            print(f"BUNDLE={archive}\nPREV_AS_OF={day}", file=env_file)
    elif sys.argv[1] == "publish":
        publish(
            Path(sys.argv[2]),
            os.environ["FIRST"],
            os.environ["AS_OF"],
            os.environ["PREV_AS_OF"],
            os.environ["GITHUB_SHA"],
        )
    else:
        raise SystemExit("expected download <directory> or publish <zip>")
