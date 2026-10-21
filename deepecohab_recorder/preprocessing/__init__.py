"""Offline preprocessing, independent of Qt and acquisition device workers."""

from deepecohab_recorder import __version__

VERSION = __version__
COLUMNS = ("antenna", "animal_id", "datetime", "time_spent", "internal_board_timestamp", "inserted")
PARQUET_COLUMNS = ('datetime', 'antenna', 'time_under', 'animal_id', 'internal_board_timestamp', 'inserted')
