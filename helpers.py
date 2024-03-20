import datetime
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any, Callable

import requests


def read(file_path: str | Path) -> dict:
    if isinstance(file_path, str):
        file_path = Path(file_path)
    print(f'Reading {file_path.as_posix()!r}')
    with open(file_path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'File must parse as a dict {file_path=}')
    return data


def write(data: dict, output_path: str | Path) -> None:
    if isinstance(output_path, str):
        output_path = Path(output_path)
    print(f'Writing to {output_path.as_posix()!r}')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='\n') as f:  # Use LF
        json.dump(data, f, indent=2)


def get(url: str) -> str:
    response = requests.get(url)
    response.raise_for_status()
    return response.text


def utc_dt_to_str(utc_dt: datetime.datetime) -> str:
    return utc_dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def delete(path: Path):
    def handler(function: Callable, path: str | Path, _excinfo: Any) -> None:
        os.chmod(path, stat.S_IWUSR)
        function(path)

    if path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path, onexc=handler)
