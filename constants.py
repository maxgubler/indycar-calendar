import datetime

BASE_URL = 'https://www.indycar.com'
SCHEDULE_URL_FORMAT = BASE_URL + '/schedule?year={year}'
CURRENT_YEAR = datetime.datetime.now().year
DEFAULT_OUTPUT_PATH_FORMAT = 'out/{year}.json'
RACE_INFO_TEXT = 'Race Info'
