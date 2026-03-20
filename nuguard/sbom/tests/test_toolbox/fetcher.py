"""
GitHub repository fetching utilities for benchmark evaluation.

This module handles fetching files from GitHub repositories
for running asset discovery against ground truth datasets.
"""
import os
import httpx
import base64
import asyncio
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of fetching a repository."""
    files: List[Tuple[str, str]]  # List of (path, content) tuples
    total_files: int
    skipped_files: int
    errors: List[str] = field(default_factory=list)
    commit_sha: Optional[str] = None


# File extensions to fetch (skip binaries, images, etc.)
ALLOWED_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs',
    '.json', '.yaml', '.yml', '.toml', '.txt', '.md', '.ipynb',
    '.env', '.env.example', '.cfg', '.ini', '.conf'
}

# Files to always include (dependency files)
ALWAYS_INCLUDE = {
    'requirements.txt', 'requirements-dev.txt', 'requirements-prod.txt',
    'pyproject.toml', 'setup.py', 'setup.cfg', 'Pipfile',
    'package.json', 'package-lock.json', 'yarn.lock',
    'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
    '.env.example', 'README.md', 'README.rst'
}

# Paths to skip
SKIP_PATHS = {
    'node_modules', '__pycache__', '.git', '.github', '.vscode',
    'dist', 'build', '.next', 'coverage', 'htmlcov', '.tox',
    'venv', '.venv', 'env', '.env', 'site-packages',
    'tests', 'test', '__tests__', 'spec', 'specs',  # Skip test directories for discovery
}

# Maximum file size to fetch (skip large files)
MAX_FILE_SIZE = 500_000  # 500KB


def parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse GitHub URL to extract owner and repo.
    
    Supports:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/tree/branch
    
    Returns: (owner, repo)
    """
    url = url.rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
    
    # Remove tree/branch suffix if present
    if '/tree/' in url:
        url = url.split('/tree/')[0]
    
    parts = url.replace('https://github.com/', '').split('/')
    if len(parts) >= 2:
        return parts[0], parts[1]
    raise ValueError(f"Invalid GitHub URL: {url}")


async def fetch_github_tree(
    owner: str,
    repo: str,
    branch: str = "main",
    token: Optional[str] = None,
    subfolder: Optional[str] = None
) -> List[dict]:
    """
    Fetch the file tree from GitHub API.
    
    Returns list of file objects with 'path', 'type', 'size', 'sha'.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "NuGuard-Benchmark"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get tree recursively
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        response = await client.get(url, headers=headers)
        
        if response.status_code == 404:
            # Try 'master' branch as fallback
            if branch == "main":
                url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/master?recursive=1"
                response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"Failed to fetch tree: {response.status_code} - {response.text}")
        
        data = response.json()
        files = [
            item for item in data.get('tree', [])
            if item['type'] == 'blob'
        ]
        
        # Filter to subfolder if specified
        if subfolder:
            subfolder = subfolder.strip('/')
            files = [f for f in files if f['path'].startswith(f"{subfolder}/")]
        
        return files


async def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    sha: str,
    token: Optional[str] = None
) -> Optional[str]:
    """
    Fetch content of a single file from GitHub.
    
    Returns file content as string, or None if failed.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "NuGuard-Benchmark"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}"
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        encoding = data.get('encoding', 'base64')
        content = data.get('content', '')
        
        if encoding == 'base64':
            try:
                return base64.b64decode(content).decode('utf-8')
            except (UnicodeDecodeError, ValueError):
                return None
        else:
            return content


def should_fetch_file(path: str, size: Optional[int] = None) -> bool:
    """
    Determine if a file should be fetched based on path and size.
    """
    # Check path exclusions
    path_parts = path.split('/')
    for part in path_parts:
        if part in SKIP_PATHS:
            return False
    
    # Check file size
    if size and size > MAX_FILE_SIZE:
        return False
    
    # Check extension
    filename = path.split('/')[-1]
    if filename in ALWAYS_INCLUDE:
        return True
    
    ext = '.' + filename.split('.')[-1] if '.' in filename else ''
    return ext.lower() in ALLOWED_EXTENSIONS


async def fetch_repo_files(
    repo_url: str,
    branch: str = "main",
    subfolder: Optional[str] = None,
    token: Optional[str] = None,
    max_files: int = 500
) -> FetchResult:
    """
    Fetch all relevant files from a GitHub repository.
    
    Args:
        repo_url: GitHub repository URL
        branch: Branch to fetch (default: main)
        subfolder: Optional subfolder to limit scope
        token: GitHub token for authentication
        max_files: Maximum files to fetch (default: 500)
    
    Returns:
        FetchResult with files and metadata
    """
    owner, repo = parse_github_url(repo_url)
    token = token or os.getenv("GITHUB_TOKEN")
    
    # Get file tree
    try:
        tree = await fetch_github_tree(owner, repo, branch, token, subfolder)
    except Exception as e:
        return FetchResult(
            files=[],
            total_files=0,
            skipped_files=0,
            errors=[str(e)]
        )
    
    # Filter files
    files_to_fetch = []
    skipped = 0
    for item in tree:
        path = item['path']
        size = item.get('size', 0)
        
        if should_fetch_file(path, size):
            files_to_fetch.append(item)
        else:
            skipped += 1
    
    # Limit number of files
    if len(files_to_fetch) > max_files:
        files_to_fetch = files_to_fetch[:max_files]
        skipped += len(files_to_fetch) - max_files
    
    # Fetch file contents in parallel (with rate limiting)
    semaphore = asyncio.Semaphore(10)  # Max 10 concurrent requests
    
    async def fetch_with_semaphore(item: dict) -> Optional[Tuple[str, str]]:
        async with semaphore:
            content = await fetch_file_content(owner, repo, item['path'], item['sha'], token)
            if content:
                return (item['path'], content)
            return None
    
    tasks = [fetch_with_semaphore(item) for item in files_to_fetch]
    results = await asyncio.gather(*tasks)
    
    files = [r for r in results if r is not None]
    errors = [f"Failed to fetch: {item['path']}" for item, r in zip(files_to_fetch, results) if r is None]
    
    return FetchResult(
        files=files,
        total_files=len(tree),
        skipped_files=skipped,
        errors=errors
    )


async def fetch_repo_for_benchmark(
    ground_truth: dict,
    token: Optional[str] = None
) -> FetchResult:
    """
    Fetch repository files based on ground truth specification.
    
    Uses commit_sha if specified for reproducibility, otherwise falls back to branch.
    
    Args:
        ground_truth: Parsed ground truth dictionary
        token: GitHub token
    
    Returns:
        FetchResult with files
    """
    # Use commit_sha for reproducibility if specified, otherwise use branch
    ref = ground_truth.get('commit_sha') or ground_truth.get('branch', 'main')
    
    return await fetch_repo_files(
        repo_url=ground_truth['repo_url'],
        branch=ref,  # GitHub API accepts both branch names and commit SHAs
        subfolder=ground_truth.get('subfolder'),
        token=token
    )


# Sync wrapper for non-async contexts
def fetch_repo_files_sync(
    repo_url: str,
    branch: str = "main",
    subfolder: Optional[str] = None,
    token: Optional[str] = None,
    max_files: int = 500
) -> FetchResult:
    """Synchronous wrapper for fetch_repo_files."""
    return asyncio.run(fetch_repo_files(repo_url, branch, subfolder, token, max_files))
