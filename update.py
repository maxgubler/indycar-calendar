import argparse
import datetime
import os
import shutil
from pathlib import Path
from stat import S_IREAD

from jsondiff import diff

from constants import CURRENT_YEAR, DEBUG_NEW_SCHEDULE_PATH, DEFAULT_OUTPUT_PATH_FORMAT
from helpers import read, write
from indycar_schedule import get_indycar_schedule, get_indycar_schedule_old


def backup_file(source_path: Path) -> str | None:
    backup_dir = source_path.parent.joinpath('backup')
    last_modified_dt = datetime.datetime.fromtimestamp(source_path.stat().st_mtime)
    last_modified = last_modified_dt.isoformat(timespec='seconds').replace(':', '_')
    backup_file_name = f'{source_path.stem}-{last_modified}{source_path.suffix}'  # {source file name}-{bak timestamp}
    backup_path = backup_dir.joinpath(backup_file_name)
    os.makedirs(backup_path.parent, exist_ok=True)
    if backup_path.is_file() and not os.access(backup_path, os.W_OK):  # Check if exists and is read-only
        return
    shutil.copy2(source_path, backup_path)
    backup_path.chmod(S_IREAD)  # Set as read-only
    backup_path_posix = backup_path.as_posix()
    print(f'Backed up existing data to {backup_path!r}')
    return backup_path_posix


def retain_past_deleted_sessions(old_schedule: dict, new_schedule: dict) -> dict:
    """Set new_schedule race value to old_schedule if the only change is session deletion in the past"""
    new_schedule = new_schedule.copy()
    races_diff = diff(old_schedule, new_schedule, syntax='symmetric').get('races', {})
    past_deleted_sessions = []
    for idx, val in races_diff.items():
        try:
            if (not isinstance(idx, int)) or (not isinstance(val, dict)) or (
                    not set(val) == {'sessions'}) or (not getattr(list(val['sessions'])[0], 'label', None) == 'delete'):
                break
            deleted_values = list(val['sessions'].values())[0]
            if not isinstance(deleted_values, dict):
                break
            deleted_times = list(deleted_values.values())
            all_in_past = all(iter(datetime.datetime.fromisoformat(x) < datetime.datetime.now(
                tz=datetime.timezone.utc) for x in deleted_times))
            if all_in_past:
                past_deleted_sessions += [idx]
        except Exception:
            break
    for idx in past_deleted_sessions:
        name = new_schedule['races'][idx].get('name', '')
        print(f'Ignoring past deleted session(s) for race {idx=} {name=}')
        new_schedule['races'][idx] = old_schedule['races'][idx]
    return new_schedule


def update_schedule(output_path: str | Path, year: int = CURRENT_YEAR) -> Path | None:
    if isinstance(output_path, str):
        output_path = Path(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(f'Unable to find existing file to update {output_path!r}')
    backup_file(output_path)
    old_schedule = read(output_path)

    print(f'Getting Indycar schedule for {year=}')
    if DEBUG_NEW_SCHEDULE_PATH:
        new_schedule = read(DEBUG_NEW_SCHEDULE_PATH)
    else:
        if year == CURRENT_YEAR:
            new_schedule = get_indycar_schedule()
        else:
            new_schedule = get_indycar_schedule_old(year)

    # Handle case where Indycar has deleted past sessions from their website
    new_schedule = retain_past_deleted_sessions(old_schedule, new_schedule)

    if not diff(old_schedule, new_schedule):
        print('No changes found')
        return

    print('Changes found: Updating schedule')
    write(new_schedule, output_path)
    return output_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--year', type=int, default=CURRENT_YEAR, help='schedule year')
    parser.add_argument('--output_path', type=Path, default=DEFAULT_OUTPUT_PATH_FORMAT, help='json file to update')
    args = parser.parse_args()
    if args.output_path == Path(DEFAULT_OUTPUT_PATH_FORMAT):
        args.output_path = Path(DEFAULT_OUTPUT_PATH_FORMAT.format(year=args.year))
    update_schedule(**vars(args))
