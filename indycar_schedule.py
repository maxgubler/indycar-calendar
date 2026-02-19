import argparse
import base64
import json
import re
from itertools import groupby
from pathlib import Path

import dateparser
from bs4 import BeautifulSoup

from constants import CURRENT_YEAR, SESSION_ID_MAP
from helpers import get, utc_dt_to_str, write


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


def group_items(data: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Group adjacent items by id, title, and date."""

    def group_key(item: dict[str, str]):
        """Group by id, title, and date."""
        event_date = dateparser.parse(item['eventTime'], settings={'RETURN_AS_TIMEZONE_AWARE': True}).date()
        return (item['id'], item['title'], event_date)

    grouped = [list(group) for _, group in groupby(data, key=group_key)]
    return grouped


def build_sessions(items: list[dict[str, str]]) -> dict[str, str]:
    sessions = {}
    handled_indicies = []

    # Handle practice
    practice_items = [(idx, match, item) for idx, item in enumerate(items) if (
        match := re.search(r'practice (\d+)', item['title'].lower()))]

    for idx, match, item in practice_items:
        key = f'practice{match[1]}'
        if key in sessions.keys():
            raise Exception(f'Multiple key matches for {key=}')
        sessions[key] = item
        handled_indicies.append(idx)

    # Handle qualifying
    qualifying_items = [(idx, match, item) for idx, item in enumerate(items) if (
        match := re.search(r'quali.*', item['title'].lower()))]

    for quali_num, (idx, match, item) in enumerate(qualifying_items, start=1):
        # First qualiying has no number since typically there is only 1; however Indy500 has 2
        key = 'qualifying' if quali_num == 1 else f'qualifying{quali_num}'
        if key in sessions.keys():
            raise Exception(f'Multiple key matches for {key=}')
        sessions[key] = item
        handled_indicies.append(idx)

    # Handle warmup
    key = 'warmup'
    for idx, item in enumerate(items):
        if re.search(r'warmup', item['title'].lower()):
            if key in sessions.keys():
                raise Exception(f'Multiple key matches for {key=}')
            sessions[key] = item
            handled_indicies.append(idx)

    # Handle race
    idx, race_item = next(iter((i, item) for i, item in enumerate(items) if item['id'] == 'race'))
    sessions['race'] = race_item
    handled_indicies.append(idx)

    unhandled_items = [x for i, x in enumerate(items) if i not in handled_indicies]
    for unhandled_item in unhandled_items:
        print(f'Found unhandled item {json.dumps(unhandled_item)}')
        unhandled_title = unhandled_item['title']
        constructed_key = None
        if 'practice' in unhandled_title.lower():
            if practice_items:
                # Determine unhandled practice item order
                for i, (_, match, practice_item) in enumerate(practice_items):
                    is_last_practice_item = i == len(practice_items) - 1
                    practice_num = int(match[1])
                    # Assuming already sorted by eventTime
                    if unhandled_item['eventTime'] < practice_item['eventTime'] or is_last_practice_item:
                        if practice_num > 1 or is_last_practice_item:
                            # Construct the practice key by incrementing the previous practice number from title
                            constructed_key = f'practice{practice_num + 1}'
                            break
                        # Use general handler if before practice 1 or if the constructed key exists
            else:
                constructed_key = 'practice1'

        if constructed_key:
            if constructed_key not in sessions.keys():
                print(f"Constructing key from {unhandled_title=} -> {constructed_key=}")
                sessions[constructed_key] = unhandled_item
                continue
            else:
                print(f"Unable to use {constructed_key=} as it already exists. Using general title handler.")

        # General unhandled item handler
        # Extract words without spaces and make each title case
        words = re.findall(r'[\w]+', unhandled_title)
        constructed_key = ''.join([word.title() for word in words])
        print(f'Using general unhandled item handler for {unhandled_title=} -> {constructed_key=}')
        if constructed_key in sessions.keys():
            raise Exception(f'Multiple key matches for {constructed_key=}')
        sessions[constructed_key] = unhandled_item
    print()
    return sessions


def get_race_details_from_matchup_url(event: dict[str, str], api_key: str) -> dict[str, str]:
    event_for_print = dict(
        (key, f'{val}?apikey={api_key}') if key == 'matchupUrl' else (key, val) for key, val in event.items() if (
            key in ("id", "title", "webUrl", "matchupUrl"))
    )
    print(f'Handling: {json.dumps(event_for_print)}')
    race_info = json.loads(get(event['matchupUrl'], {'apikey': api_key}, sleep=0.5))

    # Add the sessions leading up to the race
    schedule_items = race_info['eventSchedule']['scheduleItems']

    # Handle cancelled sessions
    cancelled_items = [x for x in schedule_items if 'cancelled' in x.get('subtitle', '').lower()]
    if cancelled_items:
        print(f'Skipping cancelled items: {json.dumps(cancelled_items)}')
        schedule_items = [x for x in schedule_items if x not in cancelled_items]

    # Sort schedule items by eventTime
    schedule_items = sorted(schedule_items, key=lambda x: x['eventTime'])

    # Group schedule items into a list of list of dict
    grouped_items = group_items(schedule_items)

    # Keep the first entry / earliest time of the grouped item(s)
    first_items = [x[0] for x in grouped_items]

    sessions = build_sessions(first_items)

    # Map session key to timestamp only
    sessions = dict((key, utc_dt_to_str(
        dateparser.parse(val['eventTime'], settings={'RETURN_AS_TIMEZONE_AWARE': True}))
    ) for key, val in sessions.items())

    # Re-sort sessions by datetime as order may have changed during processing
    sessions = dict(sorted(sessions.items(), key=lambda x: x[1]))

    race_details = {
        'name': event['title'],
        'sessions': sessions,
        'tbc': False
    }
    return race_details


def get_race_details_from_data_url(event: dict[str, str], api_key: str) -> dict[str, str]:
    event_for_print = dict(
        (key, f'{val}?apikey={api_key}') if key == 'dataUrl' else (key, val) for key, val in event.items() if (
            key in ('id', 'title', 'webUrl', 'dataUrl'))
    )
    print(f'Handling: {json.dumps(event_for_print)}')
    race_info = json.loads(get(event['dataUrl'], {'apikey': api_key}, sleep=0.5))
    race_name = race_info['header']['title']

    sessions = {}
    # Add the sessions leading up to the race if this exists
    for session in race_info.get('leaderboard', {}).get('leaderboardSections', []):
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


def get_indycar_schedule() -> dict:
    # Get API key
    events_html = get('https://www.foxsports.com/motor/indycar/events')
    soup = BeautifulSoup(events_html, 'html.parser')
    fs_settings = soup.select_one('script[data-hid="fs-settings"]').text
    if "apiKey" not in fs_settings:
        if (fs_settings_match := re.search(r'"(.+)"', fs_settings)) and (
                fs_settings_b64 := fs_settings_match.group(1)):
            fs_settings = base64.b64decode(fs_settings_b64).decode()
        else:
            raise Exception(f"Unable to parse {fs_settings=}")
    api_key = re.findall(r'"bifrost":[\w]?+{.*"apiKey":[\w]?+"([^"]+)"', fs_settings)[0]
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
    # TODO: Fetch all data first, write to file for easier debugging, and then process
    for event in events:
        try:
            race_details = get_race_details_from_matchup_url(event, api_key)
        except KeyError as e:
            print(f"Exception handling {event['matchupUrl']}: {e!r}")
            print("Retrying with data url")
            # The dataUrl has stopped containing leaderboard with session info. Likely has race time only.
            race_details = get_race_details_from_data_url(event, api_key)
        races += [race_details]
    indycar_schedule = build_output(races)
    return indycar_schedule


def main(year: int = CURRENT_YEAR, output_path: str | Path = None) -> None:
    if year < 2025:
        raise Exception(f'Use old_indycar_schedule.py for {year=}')

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
    parser.add_argument('--output_path', type=Path, help='json file path')
    args = parser.parse_args()
    main(**vars(args))
