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


def get_html(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text


def utc_dt_to_str(utc_dt: datetime.datetime) -> str:
    return utc_dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_race_info_urls(schedule_html):
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


def group_qualifying(qs: list[dict]):
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


def parse_race_details(race_info_html):
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


def write(races: list[dict]):
    data = {'races': races}
    os.makedirs('./out', exist_ok=True)
    year = races[-1]['sessions']['race'][:4]
    with open(f'./out/{year}.json', 'w') as f:
        json.dump(data, f, indent=4)


def main():
    schedule_html = get_html(SCHEDULE_URL)
    race_info_urls = parse_race_info_urls(schedule_html)

    races = []
    for race_info_url in race_info_urls:
        race_info_html = get_html(race_info_url)
        race_details = parse_race_details(race_info_html)
        races += [race_details]
    write(races)


if __name__ == '__main__':
    main()
