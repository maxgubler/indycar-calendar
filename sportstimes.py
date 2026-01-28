import argparse
import shutil
import time
from functools import cached_property
from pathlib import Path

import git
from github import Auth, Github
from github.AuthenticatedUser import AuthenticatedUser
from github.Issue import Issue

from constants import (AUTOMATION_BRANCH_NAME, COMMIT_MESSAGE, CURRENT_YEAR, DEFAULT_OUTPUT_PATH_FORMAT, GITHUB_API_KEY,
                       LOCAL_REPO_CONFIG_PATH, LOCAL_REPO_PATH, LOCAL_REPO_SCHEDULE_DIR_PATH, PULL_REQUEST_BODY,
                       SPORTSTIMES_F1_REPO_NAME, SPORTSTIMES_F1_REPO_URL)
from helpers import delete, write
from indycar_schedule import get_indycar_schedule
from update import update_config, update_schedule


class AuthenticatedGithub(Github):
    def __init__(self, **kwargs):
        super().__init__(auth=Auth.Token(GITHUB_API_KEY), **kwargs)

    @cached_property
    def user(self) -> AuthenticatedUser:
        return self.get_user()

    def build_remote_url(self, url: str) -> str:
        """Add username and token to an https url"""
        user_login = self.user.login
        auth_remote_url = url.replace('https://', f'https://{user_login}:{GITHUB_API_KEY}@', 1)
        return auth_remote_url


def unlock_git_config():
    """Ensure config is not unecessarily locked"""
    LOCAL_REPO_PATH.joinpath('.git', 'config.lock').unlink(missing_ok=True)


def get_open_pr(gh: Github) -> Issue:
    # TODO: Specifically look for automated indycar pull request
    prs = gh.search_issues(query=f'repo:{SPORTSTIMES_F1_REPO_NAME} author:@me type:pr state:open')
    pr = next(iter(prs), None)
    return pr


def get_local_repo(gh: Github) -> git.Repo:
    """Return the local repo and clone if necessary"""
    origin_url = gh.build_remote_url(SPORTSTIMES_F1_REPO_URL)
    try:
        repo = git.Repo(LOCAL_REPO_PATH)
        if SPORTSTIMES_F1_REPO_NAME not in next(repo.remotes.origin.urls):
            del repo
            print(f'Warning: Origin did not contain {SPORTSTIMES_F1_REPO_NAME=}')
            raise git.InvalidGitRepositoryError
        # Reset the remote url in case auth changed
        unlock_git_config()
        repo.remotes.origin.set_url(origin_url)
    except (git.NoSuchPathError, git.InvalidGitRepositoryError):
        if LOCAL_REPO_PATH.exists():
            delete(LOCAL_REPO_PATH)
        repo = git.Repo.clone_from(origin_url, LOCAL_REPO_PATH, single_branch=True, no_tags=True)
    return repo


def get_forked_remote(url: str, gh: AuthenticatedGithub, repo: git.Repo) -> git.Remote:
    forked_remote_url = gh.build_remote_url(url)
    if (forked_remote := getattr(repo.remotes, gh.user.login, None)):
        # Rebuild the remote url in case auth changed
        unlock_git_config()
        forked_remote.set_url(forked_remote_url)
    else:
        forked_remote = repo.create_remote(name=gh.user.login, url=forked_remote_url)  # Set remote name to username
    return forked_remote


def create_tracked_automation_branch(forked_remote: git.Remote, repo: git.Repo) -> git.Head:
    automation_branch = repo.create_head(AUTOMATION_BRANCH_NAME)
    automation_branch.set_tracking_branch(forked_remote)
    return automation_branch


def update_sportstimes(source_path: Path):
    gh = AuthenticatedGithub()
    repo = get_local_repo(gh)
    origin = repo.remotes.origin
    main = repo.heads.main
    main.checkout(force=True)
    origin.fetch()
    origin.pull()

    # Get source GitHub repo
    gh_sportstimes_f1_repo = gh.get_repo(SPORTSTIMES_F1_REPO_NAME, lazy=True)

    # Get forked GitHub repo (fallback to hardcoded sportstimes/f1 in query when using alternative fork source)
    gh_forked_repo = next(iter(gh.search_repositories(query=f'{SPORTSTIMES_F1_REPO_NAME} user:@me fork:only')),
                          next(iter(gh.search_repositories(query='sportstimes/f1 user:@me fork:only')), None))

    if not gh_forked_repo:
        gh_forked_repo = gh_sportstimes_f1_repo.create_fork(
            name=SPORTSTIMES_F1_REPO_NAME.replace('/', '-'), default_branch_only=True)
        time.sleep(3)

    # Ensure forked remote is fetched and is named for the current username
    forked_remote = get_forked_remote(url=gh_forked_repo.clone_url, gh=gh, repo=repo)
    forked_remote.fetch()
    # Sync main with fork
    forked_remote.push(force=True)

    # Check for an open pull request
    open_pr = get_open_pr(gh)
    if not open_pr:
        # Delete local automation branch if it exists
        if hasattr(repo.heads, AUTOMATION_BRANCH_NAME):
            repo.delete_head(AUTOMATION_BRANCH_NAME, force=True)
        # Prune remote tracking branches if they exist
        repo.git.remote('prune', forked_remote)
        # Delete remote automation branch if it exists
        if hasattr(forked_remote.refs, AUTOMATION_BRANCH_NAME):
            forked_remote.push(AUTOMATION_BRANCH_NAME, delete=True)
    else:
        # Ensure automation branch exists if there is an open pull request
        if not hasattr(forked_remote.refs, AUTOMATION_BRANCH_NAME):
            raise Exception(f'Remote branch {AUTOMATION_BRANCH_NAME!r} not found '
                            f'for remote {gh_forked_repo.clone_url!r}')

    # Create local automation branch if necessary
    if not (local_auto_branch := getattr(repo.heads, AUTOMATION_BRANCH_NAME, None)):
        local_auto_branch = repo.create_head(AUTOMATION_BRANCH_NAME)

    # Checkout local automation branch
    local_auto_branch.checkout(force=True)

    # Pull from remote automation branch if it exists
    if hasattr(forked_remote.refs, AUTOMATION_BRANCH_NAME):
        forked_remote.pull(AUTOMATION_BRANCH_NAME)

    # Merge main if behind
    if repo.is_ancestor(local_auto_branch, main) and local_auto_branch.commit != main.commit:
        repo.git.merge(main)

    # Copy the updated schedule year json file into the repo
    local_repo_schedule_path = LOCAL_REPO_SCHEDULE_DIR_PATH.joinpath(source_path.name)

    # Handle a new year if the schedule does not exist
    if not local_repo_schedule_path.is_file():
        print(f"No existing schedule found for '{local_repo_schedule_path}'. Updating config to include the year.")
        update_config(file_path=LOCAL_REPO_CONFIG_PATH, year=int(source_path.stem))
        # Add config file to stage for commit
        relative_config_path = LOCAL_REPO_CONFIG_PATH.relative_to(LOCAL_REPO_PATH)
        repo.index.add([relative_config_path])

    # Complete copying the new schedule
    shutil.copy2(source_path, local_repo_schedule_path)

    # Add schedule file to stage for commit
    relative_schedule_path = local_repo_schedule_path.relative_to(LOCAL_REPO_PATH)
    repo.index.add([relative_schedule_path])

    # Commit
    repo.index.commit(message=COMMIT_MESSAGE)

    # Push
    forked_remote.push()

    # Track branch on forked remote
    unlock_git_config()
    local_auto_branch.set_tracking_branch(forked_remote.refs[AUTOMATION_BRANCH_NAME])

    # Create pull request if needed
    if not open_pr:
        base = gh_sportstimes_f1_repo.default_branch
        head = f'{gh_forked_repo.owner.login}:{AUTOMATION_BRANCH_NAME}'
        # head_repo = gh_forked_repo.name  # For same org forks

        open_pr = gh_sportstimes_f1_repo.create_pull(base=base, head=head, title=COMMIT_MESSAGE,
                                                     body=PULL_REQUEST_BODY, maintainer_can_modify=True)
    print(f'Pull Request URL: {open_pr.html_url}')


def main(output_path: str | Path, year: int = CURRENT_YEAR):
    try:
        output_path = update_schedule(output_path, year)
    except FileNotFoundError:
        indycar_schedule = get_indycar_schedule()
        write(indycar_schedule, output_path)
    if output_path:
        update_sportstimes(output_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--year', type=int, default=CURRENT_YEAR, help='schedule year')
    parser.add_argument('--output_path', type=Path, default=DEFAULT_OUTPUT_PATH_FORMAT, help='json file to update')
    args = parser.parse_args()
    if args.output_path == Path(DEFAULT_OUTPUT_PATH_FORMAT):
        args.output_path = Path(DEFAULT_OUTPUT_PATH_FORMAT.format(year=args.year))
    main(**vars(args))
