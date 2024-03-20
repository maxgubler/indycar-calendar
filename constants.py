import datetime
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Indycar schedule parser
BASE_URL = 'https://www.indycar.com'
SCHEDULE_URL_FORMAT = BASE_URL + '/schedule?year={year}'
CURRENT_YEAR = datetime.datetime.now().year
DEFAULT_OUTPUT_PATH_FORMAT = 'out/{year}.json'
RACE_INFO_TEXT = 'Race Info'

# Use existing output to avoid fetching / parsing while debugging the update / sportstimes
DEBUG_NEW_SCHEDULE_PATH = os.getenv('DEBUG_NEW_SCHEDULE_PATH')

# sportstimes/f1
GITHUB_API_KEY = os.getenv('GITHUB_API_KEY')
SPORTSTIMES_F1_REPO_NAME = os.getenv('SPORTSTIMES_F1_REPO_NAME', 'sportstimes/f1')
SPORTSTIMES_F1_REPO_URL = f'https://github.com/{SPORTSTIMES_F1_REPO_NAME}.git'
LOCAL_REPO_PATH = Path('repo')
LOCAL_REPO_SCHEDULE_DIR_PATH = LOCAL_REPO_PATH.joinpath('_db', 'indycar')
AUTOMATION_BRANCH_NAME = 'indycar-calendar-automated-update'
COMMIT_MESSAGE = 'Update Indycar [indycar-calendar / automated]'
PULL_REQUEST_BODY = 'Generated by `indycar-calender`\n\nPlease contact in case of 🔥🏎🔥'
