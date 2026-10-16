"""Read version-pinned GitHub files for input review and human feedback."""

import base64
import hashlib
import json
import re


def git_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("40자리 Git SHA가 필요합니다.")
    return value


def read_tree(repository, commit, api):
    result = api(f"repos/{repository}/git/trees/{git_sha(commit)}?recursive=1")
    if result.get("truncated") is not False or not isinstance(result.get("tree"), list):
        raise ValueError("잘린 Git tree로는 검토 파일을 확인할 수 없습니다.")
    return {entry["path"]: entry for entry in result["tree"]}


def read_file(repository, tree, path, api, *, optional=False, max_bytes=10_000_000):
    entry = tree.get(path)
    if entry is None and optional:
        return None
    if (entry is None or entry.get("mode") != "100644" or entry.get("type") != "blob"
            or entry.get("size", 0) > max_bytes):
        raise ValueError("일반 검토 파일이 없거나 허용된 크기를 초과했습니다.")
    result = api(f"repos/{repository}/git/blobs/{git_sha(entry['sha'])}")
    if result.get("encoding") != "base64":
        raise ValueError("검토 파일의 Git blob 인코딩이 잘못되었습니다.")
    try:
        content = base64.b64decode("".join(result["content"].split()), validate=True)
    except (ValueError, KeyError, TypeError):
        raise ValueError("검토 파일의 Git blob을 해독할 수 없습니다.") from None
    digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    if len(content) > max_bytes or digest != entry["sha"]:
        raise ValueError("검토 파일 Git blob 무결성 확인에 실패했습니다.")
    return content


def read_object(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 중복 키는 허용하지 않습니다.")
            result[key] = value
        return result

    def finite(_):
        raise ValueError("JSON 유한 숫자만 허용합니다.")

    try:
        result = json.loads(content.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("검토 파일이 올바른 UTF-8 JSON이 아닙니다.") from None
    if not isinstance(result, dict):
        raise ValueError("검토 파일은 JSON 객체여야 합니다.")
    return result
