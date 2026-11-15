"""Installs the official Anthropic CLI (`ant`) into the api image, for «Войти через Anthropic» in the assistant.

The version is pinned and every archive is checked against its SHA-256 from the release page
(github.com/anthropics/anthropic-cli/releases/tag/v<version>): a file that does not match is never installed
and the build stops. Without network the image is built without `ant` (the assistant then signs in with an API key).

To update: change ANT_VERSION and the two checksums from the new release's ant_<version>_checksums.txt.
"""

import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.error
import urllib.request

ANT_VERSION = "2.9.0"
SHA256 = {
    "amd64": "69158f6a17e92e8e559e0061bea2530ac5b60976fb6d697673136d110ebe351f",
    "arm64": "2bbccb2861c647cbad708d83921dfc3e0689233fc0f50be4745df3e973166cc9",
}
DEST = "/usr/local/bin/ant"


def main() -> int:
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if arch is None:
        print(f"WARNING: no ant for the architecture {platform.machine()}; the assistant can still use an API key", file=sys.stderr)
        return 0
    url = f"https://github.com/anthropics/anthropic-cli/releases/download/v{ANT_VERSION}/ant_{ANT_VERSION}_linux_{arch}.tar.gz"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bedrockforge-image-build"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING: ant was not downloaded ({e}); the assistant can still use an API key", file=sys.stderr)
        return 0
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[arch]:
        print(f"ERROR: checksum of {url} does not match: {digest}, expected {SHA256[arch]}. Not installed.", file=sys.stderr)
        return 1
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and os.path.basename(m.name) == "ant"), None)
        if member is None:
            print(f"ERROR: no ant binary in {url}", file=sys.stderr)
            return 1
        binary = tar.extractfile(member).read()
    with open(DEST, "wb") as f:
        f.write(binary)
    os.chmod(DEST, 0o755)
    print(f"ant {ANT_VERSION} ({arch}) installed, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
