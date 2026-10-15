import argparse
from pathlib import Path
import re


REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}"
)
COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def image_name(repository: str, kind: str) -> str:
    if kind not in ("db", "data", "model"):
        raise ValueError("Package kind must be db, data or model.")
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Repository must be a valid owner/name pair.")
    owner, name = repository.lower().split("/")
    package = name + "-" + kind
    if name in (".", "..") or not COMPONENT.fullmatch(package):
        raise ValueError("Repository name cannot be used as a container package name.")
    return f"ghcr.io/{owner}/{package}"


def validate_tag(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError("Image tag must be 1-128 valid container-tag characters.")
    return tag


def pushed_digest(log: str) -> str:
    digests = set(re.findall(r"\bdigest:\s+(sha256:[0-9a-f]{64})(?=\s|$)", log))
    if len(digests) != 1:
        raise ValueError("Push output must contain exactly one image digest.")
    return digests.pop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validated GHCR image references.")
    commands = parser.add_subparsers(dest="command", required=True)
    name = commands.add_parser("name")
    name.add_argument("--repository", required=True)
    name.add_argument("--kind", required=True, choices=("db", "data", "model"))
    name.add_argument("--tag", required=True)
    digest = commands.add_parser("digest")
    digest.add_argument("push_log", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "name":
            image = image_name(arguments.repository, arguments.kind)
            tag = validate_tag(arguments.tag)
            print(f"image={image}\ntag={tag}\nreference={image}:{tag}")
        else:
            print(pushed_digest(arguments.push_log.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
