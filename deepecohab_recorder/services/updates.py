"""Read-only public release checks, independent of Qt and experiment files."""
from dataclasses import asdict, dataclass
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import __version__

REPOSITORY = "https://github.com/Marcin-A-Lipiec/deepecohab-acquisition"
API_URL = "https://api.github.com/repos/Marcin-A-Lipiec/deepecohab-acquisition/releases/latest"
INSTRUCTIONS_URL = REPOSITORY + "#updating"
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 8


class UpdateCheckError(ValueError):
    pass


@dataclass(frozen=True)
class UpdateStatus:
    status: str
    installed_version: str
    latest_version: str = ""
    release_url: str = REPOSITORY + "/releases"
    message: str = ""
    instructions_url: str = INSTRUCTIONS_URL

    def as_dict(self):
        return asdict(self)


def no_release(installed_version=__version__):
    return UpdateStatus("no_release", installed_version, message=
        "No public stable release was found. It may not have been published yet, "
        "or the repository may be private. Your installation has not been changed.")


def parse_release(payload, installed_version=__version__):
    """Only an official stable release with its expected wheel is installable."""
    if not isinstance(payload, dict):
        raise UpdateCheckError("GitHub returned an invalid release response.")
    tag = payload.get("tag_name", "")
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag) if isinstance(tag, str) else None
    if not match or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateCheckError("GitHub did not return a stable release with a vX.Y.Z tag.")
    current = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", installed_version)
    if not current:
        raise UpdateCheckError("This development version cannot be compared with stable releases.")
    version = tag[1:]
    release_url = REPOSITORY + "/releases/tag/" + tag
    if payload.get("html_url") != release_url:
        raise UpdateCheckError("The release does not belong to the official repository.")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateCheckError("GitHub returned an invalid installation asset list.")
    name = f"deepecohab_acquisition-{version}-py3-none-any.whl"
    wheels = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    ready = len(wheels) == 1 and wheels[0].get("browser_download_url") == (
        REPOSITORY + f"/releases/download/{tag}/{name}")
    newest, installed = tuple(map(int, match.groups())), tuple(map(int, current.groups()))
    if not ready:
        return UpdateStatus("release_incomplete", installed_version, version, release_url,
            "A release is listed, but its installation package is not ready. "
            "Wait for the maintainer to finish publishing it; keep your current installation.")
    if newest > installed:
        status, message = "update_available", f"Version {version} is available. Stop recording, save your work and close the GUI before updating."
    elif newest == installed:
        status, message = "up_to_date", "You have the latest published stable version."
    else:
        status, message = "development", "Your installed version is newer than the latest published stable release."
    return UpdateStatus(status, installed_version, version, release_url, message)


def decode_release(raw, installed_version=__version__):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpdateCheckError("GitHub's response exceeded the release-check size limit.")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise UpdateCheckError("GitHub returned unreadable release information.") from exc
    return parse_release(payload, installed_version)


def check_updates(installed_version=__version__, *, opener=None):
    """No credentials, downloads, preference writes or automatic installation."""
    request = Request(API_URL, headers={"Accept": "application/vnd.github+json",
        "User-Agent": f"DeepEcoHAB-Acquisition/{installed_version}"})
    try:
        with (opener or urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return no_release(installed_version)
        if exc.code in (403, 429):
            raise UpdateCheckError("GitHub refused or rate-limited the request. Try again later or open the Releases page.") from exc
        raise UpdateCheckError(f"GitHub could not complete the check (HTTP {exc.code}). Try again later.") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise UpdateCheckError("Could not reach GitHub. Check your internet connection or try again later. "
            "The application continues to work offline.") from exc
    return decode_release(raw, installed_version)
