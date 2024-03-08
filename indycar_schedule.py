import datetime
import json
import os
import re

import dateparser
import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://www.indycar.com'
YEAR = 2024
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


def group_qualifying(qualis: list[dict]) -> list[dict]:
    """Group qualifying sessions if start and end are within an hour"""
    grouped = {}
    group_number = 0
    for i in range(len(qualis)):
        end_dt = qualis[i]['end_dt']
        if i > 0 and (qualis[i]['start_dt'] - qualis[i-1]['end_dt']) < datetime.timedelta(hours=1):
            start_dt = grouped.get(group_number, qualis[i-1])['start_dt']
        else:
            start_dt = qualis[i]['start_dt']
            group_number += 1
        grouped.update({
            group_number: {
                'session_key': f'qualifying{group_number}',
                'start_dt': start_dt,
                'end_dt': end_dt
            }
        })
    grouped_values = list(grouped.values())
    if len(grouped_values) == 1:
        grouped_values[0]['title'] = 'Qualifying'
        grouped_values[0]['session_key'] = 'qualifying'
    return grouped_values


def group_races(races: list[dict]) -> list[dict]:
    """Group race sessions if start and end are within an hour. Handle special multi-race event."""
    grouped = {}
    group_number = 0
    for i in range(len(races)):
        end_dt = races[i]['end_dt']
        if i > 0 and (races[i]['start_dt'] - races[i-1]['end_dt']) < datetime.timedelta(hours=1):
            start_dt = grouped.get(group_number, races[i-1])['start_dt']
        else:
            start_dt = races[i]['start_dt']
            group_number += 1
        grouped.update({
            group_number: {
                'session_key': f'race{group_number}',
                'start_dt': start_dt,
                'end_dt': end_dt
            }
        })
    grouped_values = list(grouped.values())
    if len(grouped_values) == 1:
        grouped_values[0]['title'] = 'Race'
        grouped_values[0]['session_key'] = 'race'
    return grouped_values


def clean_sessions(race_name: str, sessions: list[dict]) -> list[dict]:
    """Return sessions with session_key based on session title"""
    sorted_sessions = sorted(sessions, key=lambda x: x['start_dt'])
    qualis = []
    races = []
    cleaned = []
    practice_number = 1
    for session in sorted_sessions:
        title = session['title']
        lower_title = title.lower()
        if 'practice' in lower_title:
            session['session_key'] = f'practice{practice_number}'
            practice_number += 1
            cleaned.append(session)
        elif 'warmup' in lower_title:
            session['session_key'] = 'warmup'
            cleaned.append(session)
        elif 'race' in lower_title:
            races.append(session)
        elif 'quali' in lower_title:
            qualis.append(session)
        else:
            print(f'WARNING: Unable to parse a session key from {title=} for {race_name=}')
            session['session_key'] = title
            cleaned.append(session)
    if qualis:
        grouped_qualifying = group_qualifying(qualis)
        cleaned += grouped_qualifying
    if races:
        grouped_races = group_races(races)
        cleaned += grouped_races
    cleaned = sorted(cleaned, key=lambda x: x['start_dt'])
    return cleaned


def transform_sessions(sessions: list[dict]) -> dict:
    transformed = {}
    for s in sessions:
        start = utc_dt_to_str(s['start_dt'])
        transformed.update({s['session_key']: start})
    return transformed


def parse_race_details(race_info_html: str) -> dict:
    soup = BeautifulSoup(race_info_html, 'html.parser')
    race_name = soup.select_one('.title-container').text.strip()
    session_elements = [x.text for x in soup.select('#schedule .race-list__item')]
    # Try to use broadcast if race is TBD in the session element
    tbd_race_idx = next(iter(i for i, x in enumerate(session_elements) if re.search(r'TBD\s+Race', x)), None)
    is_tbd = isinstance(tbd_race_idx, int)
    if is_tbd:
        print(f'WARNING: Found TBD race in session elements. Attempting to use broadcast data. {race_name=}')
        broadcast_elements = soup.select('#broadcasts .race-list .race-list__item')
        session_elements[tbd_race_idx] = next(iter(
            x.text for x in broadcast_elements if re.search(r'INDYCAR.*Race|\nRace\s?', x.text, re.IGNORECASE)))
    sessions = [parse_session(x) for x in session_elements]
    cleaned_sessions = clean_sessions(race_name, sessions)
    session_keys = set([x['session_key'] for x in cleaned_sessions])
    if len(cleaned_sessions) != len(session_keys):
        raise Exception(f'Duplicate session keys found for {race_name=}: {session_keys}')
    transformed_sessions = transform_sessions(cleaned_sessions)
    race_details = {
        'name': race_name,
        'sessions': transformed_sessions,
        'tbc': is_tbd  # To be "confirmed" is used in output
    }
    return race_details


def build_output(races: list) -> dict:
    # Finishing touches
    for _round, race in enumerate(races, start=1):
        # Set round by index + 1
        race['round'] = _round
        # Set slug and localeKey to lowercase name with non-alpha chars stripped and spaces replaced with hyphen
        _id = re.sub(r'\s+', '-', ''.join(x if (x.isalnum() or x == ' ') else '' for x in race['name'])).lower()
        race['slug'] = _id
        race['localeKey'] = _id
        race['longitude'] = 0
        race['latitude'] = 0
    output = {'races': races}
    return output


def write(data: dict):
    os.makedirs('./out', exist_ok=True)
    with open(f'./out/{YEAR}.json', 'w') as f:
        json.dump(data, f, indent=4)


def main():
    schedule_html = get(SCHEDULE_URL)
    race_info_urls = parse_race_info_urls(schedule_html)

    races = []
    for race_info_url in race_info_urls:
        print(f'Handling {race_info_url=}')
        try:
            race_info_html = get(race_info_url)
            race_details = parse_race_details(race_info_html)
            races += [race_details]
        except Exception:
            print(f'Exception while handling {race_info_url=}')
            raise
    output = build_output(races)
    write(output)
    print('Complete!')


if __name__ == '__main__':
    main()
