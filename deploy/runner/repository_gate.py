"""Root-only repository privacy gate; never trusts workflow-supplied booleans."""
import json
from pathlib import Path
import re
import stat
import urllib.request

TOKEN = Path('/etc/nexarm-runner/github-metadata.token')


def repository_metadata(repository, token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository identity')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,255}',token):
        raise ValueError('Invalid metadata token format; enter it only at the local hidden prompt')
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}', headers={
        'Authorization': 'Bearer ' + token, 'Accept':'application/vnd.github+json',
        'User-Agent':'nexarm-private-diagnostics', 'X-GitHub-Api-Version':'2022-11-28',
        'Cache-Control':'no-cache'})
    with urllib.request.urlopen(request, timeout=10) as response:
        metadata = json.load(response)
    if metadata.get('private') is not True or metadata.get('full_name','').lower() != repository.lower():
        raise ValueError('Robot-hosted runner requires the authenticated repository to be private')
    return metadata


def require_private(config):
    if config.get('diagnostics_enabled') is not True:
        raise ValueError('ROS/camera diagnostics are not enrolled')
    info=TOKEN.lstat()
    if not stat.S_ISREG(info.st_mode) or TOKEN.is_symlink() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Unsafe metadata credential ownership')
    repository_metadata(config['github_repository'], TOKEN.read_text().strip())
