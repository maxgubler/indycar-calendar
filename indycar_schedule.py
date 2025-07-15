import argparse
import datetime
import json
import re
from pathlib import Path

import dateparser
from bs4 import BeautifulSoup

from constants import (BASE_URL, CURRENT_YEAR, DEFAULT_OUTPUT_PATH_FORMAT, RACE_INFO_TEXT, SCHEDULE_URL_FORMAT,
                       SESSION_ID_MAP)
from helpers import get, utc_dt_to_str, write


def parse_race_info_urls(schedule_html: str) -> list[str]:
    soup = BeautifulSoup(schedule_html, 'html.parser')
    race_links = soup.findAll('a', string=RACE_INFO_TEXT)
    race_info_urls = [BASE_URL + a.attrs['href'] for a in race_links]
    race_info_urls = list(dict.fromkeys(race_info_urls))  # remove potential duplicates
    return race_info_urls


def parse_session(event: str, year: int) -> dict:
    event = re.sub(r'\s+\n', '\n', event.strip())
    date, time, session_title = event.split('\n')[:3]
    tz = time.split(' ')[-1]
    if tz.upper() not in ('ET', 'EST', 'EDT'):
        raise Exception(f'Unknown timezone {tz=}')
    start_time, end_time = time.replace(f' {tz}', '').split(' - ')
    start_dt = dateparser.parse(f'{date} {year} {start_time}', settings={
        'TIMEZONE': 'US/Eastern', 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})
    end_dt = dateparser.parse(f'{date} {year} {end_time}', settings={
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
        elif 'quali' in lower_title:
            qualis.append(session)
        elif 'race' in lower_title:
            races.append(session)
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


def parse_race_details(race_info_html: str, year: int) -> dict:
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
    sessions = [parse_session(session_element, year) for session_element in session_elements]
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


def get_indycar_schedule_old(year: int) -> dict:
    schedule_url = SCHEDULE_URL_FORMAT.format(year=year)
    schedule_html = get(schedule_url)
    race_info_urls = parse_race_info_urls(schedule_html)

    races = []
    for race_info_url in race_info_urls:
        print(f'Handling {race_info_url=}')
        try:
            race_info_html = get(race_info_url)
            race_details = parse_race_details(race_info_html, year)
            races += [race_details]
        except Exception:
            print(f'Exception while handling {race_info_url=}')
            raise
    indycar_schedule = build_output(races)
    return indycar_schedule


def get_race_details_from_data_url(event: dict[str, str], api_key: str) -> dict[str, str]:
    event_for_print = dict(
        (key, f'{val}?apikey={api_key}') if key == 'dataUrl' else (key, val) for key, val in event.items() if (
            key in ("id", "title", "webUrl", "dataUrl"))
    )
    print(f'Handling: {json.dumps(event_for_print)}')
    race_info = json.loads(get(event['dataUrl'], {'apikey': api_key}, sleep=0.5))
    race_name = race_info['header']['title']

    sessions = {}
    # Add the sessions leading up to the race
    for session in race_info['leaderboard']['leaderboardSections']:
        # Map id to our desired key and fallback to use their session id
        session_key = SESSION_ID_MAP.get(session['id'], session['id'])
        sessions.update({
            session_key: utc_dt_to_str(
                dateparser.parse(session['eventTime'], settings={'RETURN_AS_TIMEZONE_AWARE': True}))
        })
    # Add the race session from the eventTime
    sessions.update({
        'race': utc_dt_to_str(
            dateparser.parse(race_info['header']['eventTime'], settings={'RETURN_AS_TIMEZONE_AWARE': True}))
    })
    # Sort sessions by datetime
    sessions = dict(sorted(sessions.items(), key=lambda x: x[1]))

    is_tba = race_info['header'].get('isTba', False)

    race_details = {
        'name': race_name,
        'sessions': sessions,
        'tbc': is_tba
    }
    return race_details


def get_race_details_from_matchup_url(event: dict[str, str], api_key: str) -> dict[str, str]:
    event_for_print = dict(
        (key, f'{val}?apikey={api_key}') if key == 'matchupUrl' else (key, val) for key, val in event.items() if (
            key in ("id", "title", "webUrl", "matchupUrl"))
    )
    print(f'Handling: {json.dumps(event_for_print)}')
    race_info = json.loads(get(event['matchupUrl'], {'apikey': api_key}, sleep=0.5))

    sessions = {}
    # Add the sessions leading up to the race
    for session in race_info['eventSchedule']['scheduleItems']:
        # Map id to our desired key and fallback to use their session id
        session_key = SESSION_ID_MAP.get(session['id'], session['id'])
        sessions.update({
            session_key: utc_dt_to_str(
                dateparser.parse(session['eventTime'], settings={'RETURN_AS_TIMEZONE_AWARE': True}))
        })
    # Sort sessions by datetime
    sessions = dict(sorted(sessions.items(), key=lambda x: x[1]))

    race_details = {
        'name': event['title'],
        'sessions': sessions,
        'tbc': False
    }
    return race_details


def get_indycar_schedule() -> dict:
    # Get API key
    events_html = get('https://www.foxsports.com/motor/indycar/events')
    soup = BeautifulSoup(events_html, 'html.parser')
    fs_settings = soup.select_one('script[data-hid="fs-settings"]')
    api_key = re.findall(r'"bifrost":[\w]?+{.*"apiKey":[\w]?+"([^"]+)"', fs_settings.text)[0]
    print(f'apikey={api_key}')

    # Structure
    # Events HTML - Contains api key and highest level api endpoint url for months
    # Months JSON - Contains endpoint for each month
    # Month JSON - Contains race ids
    # Event data JSON - Contains title and session data

    # Get months
    div_months = soup.select_one('div[api-endpoint-url]')
    months_url = div_months.get('api-endpoint-url').strip()
    print(f'{months_url=}')
    months_overview = json.loads(get(months_url, {'apikey': api_key}))['selectionGroupList'][0]['selectionList']
    # Example months_overview (months_url = 'https://api.foxsports.com/bifrost/v1/nascar/league/scores?groupId=6')
    # [{
    #     "id": "202503",
    #     "title": "MARCH",
    #     "uri": "https://api.foxsports.com/bifrost/v1/nascar/league/scores-segment/202503?groupId=6",
    #     "webUrl": "/motor/indycar/scores?groupId=6&month=March",
    #     "parameters": {
    #         "month": "March"
    #     }
    # }, ...]

    events = []
    for month_overview in months_overview:
        print(f"Fetching events for {month_overview['title']}: {month_overview['uri']}&apikey={api_key}")
        month = json.loads(get(month_overview['uri'], {'apikey': api_key}, sleep=0.5))
        # Example month (month_overview['uri'] = 'https://api.foxsports.com/bifrost/v1/nascar/league/scores-segment/202503?groupId=6')
        # {
        #     "id": "202503",
        #     "sectionList": [
        #         {
        #             "id": "5852-202503",
        #             "sectionDate": "2025-03-02T05:00:00Z",
        #             "segmentId": "202503",
        #             "selectionId": "202503",
        #             "subtitle": "SUN, MAR 2",
        #             "menuTitle": "SUN, MAR 2",
        #             "events": [
        #                 {
        #                     "template": "scores-event",
        #                     "id": "nascar5852",
        #                     "liveStartTime": "2025-03-02T16:50:00Z",
        #                     "uri": "https://api.foxsports.com/bifrost/v1/nascar/scorechip/nascar5852",
        #                     "contentUri": "auto/indy/events/5852",
        #                     "contentType": "event",
        #                     "eventStatus": 2,
        #                     "eventTime": "2025-03-02T17:00:00Z",
        #                     "sortKey": "122025030217002172005852",
        #                     "altSortKey": "122202503021700172005852",
        #                     "tvStation": "FOX",
        #                     "favoriteEntities": [
        #                         "auto/indy/league/6"
        #                     ],
        #                     "isTba": false,
        #                     "entityLink": {
        #                         "webUrl": "/motor/firestone-grand-prix-of-st-petersburg-ntt-indycar-series-mar-02-2025-racetrax-5852",
        #                         "contentUri": "auto/indy/events/5852",
        #                         "contentType": "event",
        #                         "layout": {
        #                             "path": "/layouts?type=layout&subtype=motorEventLayout",
        #                             "tokens": {
        #                                 "id": "5852",
        #                                 "eventUri": "auto/indy/events/5852",
        #                                 "leagueUri": "auto/indy/league/6"
        #                             }
        #                         },
        #                         "analyticsSport": "motor",
        #                         "type": "entity"
        #                     },
        #                     "league": "INDYCAR",
        #                     "importance": 1,
        #                     "title": "Firestone Grand Prix of St. Petersburg",
        #                     "subtitle": "Streets of St. Petersburg",
        #                     "subtitle2": "St. Petersburg, FL, USA",
        #                     "leagueLogo": {
        #                         "url": "https://b.fssta.com/uploads/application/leagues/logos/IndyCar.vresize.80.80.medium.1.png",
        #                         "altUrl": "https://b.fssta.com/uploads/application/leagues/logos/IndyCar-alternate.vresize.80.80.medium.1.png",
        #                         "type": "image-logo",
        #                         "altText": "NTT INDYCAR SERIES"
        #                     }
        #                 }
        #             ]
        #         }, ...
        #     ],
        #     "currentSectionId": "5852-202503",
        #     "quickNav": [
        #         {
        #             "id": "202503",
        #             "uri": "https://api.foxsports.com/bifrost/v1/nascar/league/scores-segment/202503?groupId=6",
        #             "webUrl": "/motor/indycar/scores?groupId=6&month=March",
        #             "title": "MARCH",
        #             "selected": true
        #         }, ...
        #     ]
        # }
        for section in month['sectionList']:
            if len(section['events']) > 1:
                print(f"WARNING: Expecting 1 event. Multiple events found for {section['id']=}:\n"
                      f"{json.dumps(section['events'], indent=4)}")

            # Get event id from section id (of the form {event_id}-{month_id})
            event_id = section['id'].replace(month['id'], '').replace('-', '')
            event = {
                'id': event_id,
                'title': section['events'][0]['title'],
                'webUrl': 'https://www.foxsports.com' + section['events'][0]['entityLink']['webUrl'],
                # Data endpoint has simpler event data and has not been very reliable
                'dataUrl': f'https://api.foxsports.com/bifrost/v1/nascar/event/{event_id}/data',
                # Matchup has races alongside practice, qualifying, may split long sessions by network (FS1, FS2, FOX)
                'matchupUrl': f'https://api.foxsports.com/bifrost/v1/nascar/event/{event_id}/matchup'
            }
            events.append(event)

    races = []
    for event in events:
        # The dataUrl has been failing for later races
        # try:
        #     race_details = get_race_details_from_data_url(event, api_key)
        # except Exception as e:
        #     print(f'Exception while handling {event["dataUrl"]!r}: {e!r}')
        #     print("Retrying with matchup url")
        race_details = get_race_details_from_matchup_url(event, api_key)
        races += [race_details]
    indycar_schedule = build_output(races)
    return indycar_schedule


def main(year: int = CURRENT_YEAR, output_path: str | Path = None) -> None:
    if year < 2025:
        raise Exception(f"Use old_indycar_schedule.py for {year=}")

    if isinstance(output_path, str):
        output_path = Path(output_path)

    indycar_schedule = get_indycar_schedule()

    if output_path:
        write(indycar_schedule, output_path)
    else:
        print(json.dumps(indycar_schedule, indent=4))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--year', type=int, default=CURRENT_YEAR, help='schedule year')
    parser.add_argument('--output_path', type=Path, default=DEFAULT_OUTPUT_PATH_FORMAT, help='json file path')
    args = parser.parse_args()
    if args.output_path == Path(DEFAULT_OUTPUT_PATH_FORMAT):
        args.output_path = Path(DEFAULT_OUTPUT_PATH_FORMAT.format(year=args.year))
    main(**vars(args))
