import argparse
import datetime
import json
import os
import shutil
from pathlib import Path
from stat import S_IREAD

from jsondiff import diff

from indycar_schedule import CURRENT_YEAR, DEFAULT_OUTPUT_PATH_FORMAT, get_indycar_schedule, write


def read(file_path: str | Path) -> dict:
    if isinstance(file_path, str):
        file_path = Path(file_path)
    print(f'Reading {file_path.as_posix()!r}')
    with open(file_path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'File must parse as a dict {file_path=}')
    return data


def backup_file(source_path: Path) -> str:
    backup_dir = source_path.parent.joinpath('backup')
    last_modified_dt = datetime.datetime.fromtimestamp(source_path.stat().st_mtime)
    last_modified = last_modified_dt.isoformat(timespec='seconds').replace(':', '_')
    backup_file_name = f'{source_path.stem}-{last_modified}{source_path.suffix}'
    backup_path = backup_dir.joinpath(backup_file_name)
    os.makedirs(backup_path.parent, exist_ok=True)
    if backup_path.is_file and not os.access(backup_path, os.W_OK):
        return
    shutil.copy2(source_path, backup_path)
    backup_path.chmod(S_IREAD)  # Set as read-only
    backup_path_posix = backup_path.as_posix()
    print(f'Backed up existing data to {backup_path!r}')
    return backup_path_posix


def main(output_path: str | Path, year: int = CURRENT_YEAR):
    if isinstance(output_path, str):
        output_path = Path(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(f'Unable to find existing file to update {output_path!r}')
    backup_file(output_path)
    old_schedule = read(output_path)
    print(f'Getting Indycar schedule for {year=}')
    new_schedule = get_indycar_schedule(year)
    if diff(old_schedule, new_schedule):
        print('Changes found: Updating schedule')
        write(new_schedule, output_path)
    else:
        print('No changes found')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--year', type=int, default=CURRENT_YEAR, help='schedule year')
    parser.add_argument('--output_path', type=Path, default=DEFAULT_OUTPUT_PATH_FORMAT, help='json file to update')
    args = parser.parse_args()
    if args.output_path == Path(DEFAULT_OUTPUT_PATH_FORMAT):
        args.output_path = Path(DEFAULT_OUTPUT_PATH_FORMAT.format(year=args.year))
    main(**vars(args))
