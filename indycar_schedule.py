import datetime
import json
import os
import re

import dateparser
import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://www.indycar.com'
YEAR = 2023
SCHEDULE_URL = f'{BASE_URL}/schedule?year={YEAR}'
RACE_INFO_TEXT = 'Race Info'
SPORTSTIMES_JSON_URL = f'https://raw.githubusercontent.com/sportstimes/f1/main/_db/indycar/{YEAR}.json'


def get(url: str) -> str:
    response = requests.get(url)
    response.raise_for_status()
    return response.text


def utc_dt_to_str(utc_dt: datetime.datetime) -> str:
    return utc_dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_race_info_urls(schedule_html: str) -> list[str]:
    soup = BeautifulSoup(schedule_html, 'html.parser')
    race_links = soup.findAll('a', string=RACE_INFO_TEXT)
    race_info_urls = [BASE_URL + a.attrs['href'] for a in race_links]
    race_info_urls = list(dict.fromkeys(race_info_urls))  # remove potential duplicates
    return race_info_urls


def parse_session(event: str) -> dict:
    event = re.sub(r'\s+\n', '\n', event.strip())
    date, time, session_title = event.split('\n')[:3]
    tz = time.split(' ')[-1]
    if tz.upper() not in ('ET', 'EST', 'EDT'):
        raise Exception(f'Unknown timezone {tz=}')
    start_time, end_time = time.replace(f' {tz}', '').split(' - ')
    start_dt = dateparser.parse(f'{date} {YEAR} {start_time}', settings={
        'TIMEZONE': 'US/Eastern', 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})
    end_dt = dateparser.parse(f'{date} {YEAR} {end_time}', settings={
        'TIMEZONE': 'US/Eastern', 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})
    session_details = {
        'title': session_title,
        'start_dt': start_dt,
        'end_dt': end_dt
    }
    return session_details


def group_qualifying(qs: list[dict]) -> list[dict]:
    """Group qualifying sessions if start and end are within an hour"""
    grouped = {}
    group_number = 0
    for i in range(len(qs)):
        end_dt = qs[i]['end_dt']
        if i > 0 and (qs[i]['start_dt'] - qs[i-1]['end_dt']) < datetime.timedelta(hours=1):
            start_dt = grouped.get(group_number, qs[i-1])['start_dt']
        else:
            start_dt = qs[i]['start_dt']
            group_number += 1
        grouped.update({
            group_number: {
                'title': f'qualifying{group_number}',
                'start_dt': start_dt,
                'end_dt': end_dt
            }
        })
    grouped_values = list(grouped.values())
    if len(grouped_values) == 1:
        grouped_values[0]['title'] = 'qualifying'
    return grouped_values


def clean_sessions(race_name: str, sessions: list[dict]) -> list[dict]:
    sessions = sorted(sessions, key=lambda x: x['start_dt'])
    qualifying = []
    cleaned = []
    practice_number = 1
    for session in sessions:
        title = session['title'].lower()
        if 'practice' in title:
            session['title'] = f'practice{practice_number}'
            practice_number += 1
            cleaned.append(session)
        elif 'warmup' in title:
            session['title'] = 'warmup'
            cleaned.append(session)
        elif 'race' in title:
            session['title'] = 'race'
            cleaned.append(session)
        elif 'quali' in title:
            qualifying.append(session)
        else:
            print(f'Unable to parse {title=} for {race_name=}')
    if qualifying:
        grouped_qualifying = group_qualifying(qualifying)
        cleaned += grouped_qualifying
    cleaned = sorted(cleaned, key=lambda x: x['start_dt'])
    return cleaned


def transform_sessions(sessions: list[dict]) -> dict:
    transformed = {}
    for s in sessions:
        start = utc_dt_to_str(s['start_dt'])
        transformed.update({s['title']: start})
    return transformed


def parse_race_details(race_info_html: str) -> dict:
    soup = BeautifulSoup(race_info_html, 'html.parser')
    race_name = soup.select_one('.title-container').text.strip()
    session_elements = [x.text for x in soup.select('#schedule .race-list__item')]
    sessions = [parse_session(x) for x in session_elements]
    sessions = clean_sessions(race_name, sessions)
    titles = set([x['title'] for x in sessions])
    if len(sessions) != len(titles):
        raise Exception(f'Duplicate session titles found for {race_name=}: {titles}')
    transformed_sessions = transform_sessions(sessions)
    race_details = {
        'name': race_name,
        'sessions': transformed_sessions
    }
    return race_details


def merge_data(races: list, sportstimes_data: dict) -> dict:
    st_races = sportstimes_data['races']
    st_idx_to_date = dict((i, dateparser.parse(race_date).date()) for i, st_race in enumerate(st_races) if (
        race_date := st_race.get('sessions', {}).get('race')))

    # Match race index to sportstimes race index by date since name may change
    idx_to_st_idx = {}
    for i, race in enumerate(races):
        race_date = dateparser.parse(race['sessions']['race']).date()
        st_race_idx = next(iter(st_idx for st_idx, st_date in st_idx_to_date.items() if race_date == st_date), None)
        if st_race_idx is None:
            print(f'No matching sportstimes race found for {race["name"]} on {race_date}')
        else:
            idx_to_st_idx.update({i: st_race_idx})

    # Display any sportstimes race without a match
    if (st_idx_no_matches := sorted(set([*range(0, len(st_races))]) - set(idx_to_st_idx.values()))):
        for st_idx in st_idx_no_matches:
            print(f'No matching race found for existing sportstimes race entry: {json.dumps(st_races[st_idx])}')

    # Merge
    merged_races = [{
        **(st_races[st_idx] if (isinstance(st_idx := idx_to_st_idx.get(i), int)) else {}),
        **race
    } for i, race in enumerate(races)]

    # Finishing touches
    for _round, race in enumerate(merged_races, start=1):
        # Set round by index + 1
        race['round'] = _round
        # Set slug and localeKey to lowercase name with non-alpha chars stripped and spaces replaced with hyphen
        _id = re.sub(r'\s+', '-', ''.join(x if (x.isalnum() or x == ' ') else '' for x in race['name'])).lower()
        race['slug'] = _id
        race['localeKey'] = _id
        # Remove tbc if additional session data is found
        if len(race['sessions'].keys()) > 1 and 'tbc' in race.keys():
            del race['tbc']

    merged_data = {**sportstimes_data, 'races': merged_races}
    return merged_data


def write(data: dict):
    os.makedirs('./out', exist_ok=True)
    with open(f'./out/{YEAR}.json', 'w') as f:
        json.dump(data, f, indent=4)


def main():
    sportstimes_data = json.loads(get(SPORTSTIMES_JSON_URL))
    schedule_html = get(SCHEDULE_URL)
    race_info_urls = parse_race_info_urls(schedule_html)

    races = []
    for race_info_url in race_info_urls:
        race_info_html = get(race_info_url)
        race_details = parse_race_details(race_info_html)
        races += [race_details]
    merged_sportstimes_data = merge_data(races, sportstimes_data)
    write(merged_sportstimes_data)
    print('Complete!')


if __name__ == '__main__':
    main()
