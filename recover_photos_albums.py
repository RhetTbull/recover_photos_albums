"""Recover deleted albums from a Photos library"""

# uv dependencies
# run this with uv: `uv run recover_photos_albums.py`
# /// script
# dependencies = [
#   "click>=8.1.3",
#   "more-itertools>=8.8.0",
#   "osxphotos>=0.69.2",
#   "photoscript>=0.4.0",
#   "questionary>=2.1.0",
#   "rich>=13.5.2",
# ]
# ///

import dataclasses
import datetime
import os
import re
import sqlite3
from functools import cache

import click
import photoscript
import questionary
from click import echo
from more_itertools import chunked
from rich.console import Console
from rich.progress import Progress

from osxphotos.photos_datetime import photos_datetime_local
from osxphotos.sqlite_utils import sqlite_open_ro
from osxphotos.utils import get_last_library_path


@dataclasses.dataclass
class Album:
    """Holds information about an album."""

    pk: int
    uuid: str
    title: str
    parent: str
    kind: int
    trashed: int
    trashed_date: datetime.datetime | None
    photo_count: int


@cache
def get_album_table_columns(db_path) -> tuple[str, str, str, str]:
    """Retrieve album titles, trashed status, trashed date, and number of assets in each album dynamically.

    Args:
        db_path: path to Photos database

    Returns: tuple of: albums_assets_table, album_join, album_column, sort_column

    Note: This function is required to handle the fact that the table and column names
    change between Photos versions. This function dynamically determines the correct
    table and column names to use for queries related to albums and assets in those albums.

    It has been tested on databases from Photos 5.0 (macos 10.15/Catalina) to 10.0 (macOS 15/Sequoia)
    """

    # TODO: retry if locked
    conn, cursor = sqlite_open_ro(db_path)
    # Find the correct album-to-assets mapping table
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z_%ASSETS'"
    )
    assets_tables = (
        cursor.fetchall()
    )  # Example: Z_28ASSETS but there are tables like Z_3SUGGESTIONSBEINGREPRESENTATIVEASSETS we're not looking for
    albums_assets_table = None
    for table in assets_tables:
        if re.match(r"^Z_\d+ASSETS$", table[0]):
            albums_assets_table = table[0]
            break
    if not albums_assets_table:
        raise Exception(f"Could not find albums-to-assets table: {assets_tables}")

    # Find the column linking this table to albums, e.g. Z_28ASSETS.Z_28ALBUMS
    # and the column with sort order, e.g. Z_28ASSETS.Z_FOK_3ASSETS
    # and the album to asset join column, e.g. Z_28ASSETS.Z_3ASSETS
    cursor.execute(f"PRAGMA table_info({albums_assets_table})")
    columns = cursor.fetchall()

    album_column = None
    sort_column = None
    album_join = None

    for col in columns:
        if re.match(r"^Z_\d+ALBUMS$", col[1]):
            album_column = col[1]
            continue
        if re.match(r"^Z_\d+ASSETS$", col[1]):
            album_join = col[1]
            continue
        if re.match(r"^Z_FOK_\d+ASSETS$", col[1]):
            sort_column = col[1]

    conn.close()

    if not album_column or not sort_column or not album_join:
        raise Exception(
            f"Could not determine album and sort order columns: {columns} {album_column} {sort_column} {album_join}"
        )

    return albums_assets_table, album_join, album_column, sort_column


def get_albums_info(db_path: str) -> list[Album]:
    """Get info on albums"""

    albums_assets_table, album_join, album_column, sort_column = (
        get_album_table_columns(db_path)
    )

    query = f"""
    SELECT
        ZG.Z_PK as AlbumPK,
        ZG.ZUUID AS AlbumUUID,
        ZG.ZTITLE AS AlbumTitle,
        ZG.ZPARENTFOLDER AS ParentFolder,
        ZG.ZKIND AS AlbumKind,
        ZG.ZTRASHEDSTATE AS TrashedState,
        ZG.ZTRASHEDDATE AS TrashedDate,
        COUNT(ZAL.{album_join}) AS PhotoCount
    FROM
        ZGENERICALBUM ZG
    LEFT JOIN
        {albums_assets_table} ZAL ON ZG.Z_PK = ZAL.{album_column}
    LEFT JOIN
        ZGENERICALBUM ZP ON ZG.ZPARENTFOLDER = ZP.Z_PK
    WHERE
       ZP.ZKIND = 3999 AND -- top-level library
	   ZG.ZKIND = 2 -- regular albums
    GROUP BY
        ZG.Z_PK
    ORDER BY
        ZG.ZTRASHEDDATE;
    """

    conn, cursor = sqlite_open_ro(db_path)
    cursor.execute(query)
    albums = cursor.fetchall()
    conn.close()

    album_list = []
    for album in albums:
        album_data = list(album)
        if trashed_date := album_data[6]:
            album_data[6] = photos_datetime_local(trashed_date)
        album_list.append(Album(*album_data))
    return album_list


def get_photos_in_album(db_path: str, uuid: str) -> list[str]:
    """Return a list of UUIDs of photos in an album.

    Args:
        db_path: The path to the Photos database.
        uuid: The UUID of the album.

    Returns: A list of UUIDs of photos in the album, sorted according to the album's sort order.
    """

    albums_assets_table, album_join, album_column, sort_column = (
        get_album_table_columns(db_path)
    )

    query = f"""
    SELECT
        ZA.ZUUID AS PhotoUUID
    FROM
        ZASSET ZA
    JOIN
        {albums_assets_table} ZAL ON ZA.Z_PK = ZAL.{album_join}
    JOIN
        ZGENERICALBUM ZG ON ZAL.{album_column} = ZG.Z_PK
    LEFT JOIN
        ZADDITIONALASSETATTRIBUTES ZAA ON ZA.ZADDITIONALATTRIBUTES = ZAA.Z_PK
    WHERE
        ZG.ZUUID = ?
    ORDER BY
        -- Sorting by title (if applicable)
        CASE WHEN ZG.ZCUSTOMSORTKEY = 5 THEN ZAA.ZTITLE END COLLATE NOCASE,

        -- Sorting manually (if applicable)
        CASE WHEN ZG.ZCUSTOMSORTKEY = 0 THEN ZAL.{sort_column} END,

        -- Sorting by date (conditionally applying ASC or DESC)
        CASE
            WHEN ZG.ZCUSTOMSORTKEY = 1 AND ZG.ZCUSTOMSORTASCENDING = 1 THEN ZA.ZDATECREATED
        END ASC,
        CASE
            WHEN ZG.ZCUSTOMSORTKEY = 1 AND ZG.ZCUSTOMSORTASCENDING = 0 THEN ZA.ZDATECREATED
        END DESC;
    """

    conn, cursor = sqlite_open_ro(db_path)
    cursor.execute(query, (uuid,))
    photos = cursor.fetchall()
    conn.close()
    return [photo[0] for photo in photos]


def get_db_path(library: os.PathLike | None) -> str:
    """Get the path to the Photos database for a library."""
    # Use the provided library path, or get the last used one.
    library = str(library) if library else get_last_library_path()
    if not library:
        raise FileNotFoundError("Could not find Photos library")

    # Ensure the library path exists.
    if not os.path.exists(library):
        raise FileNotFoundError(f"Could not find Photos library at {library}")

    # If a directory is provided, assume it's a photoslibrary and append the database path.
    if os.path.isdir(library):
        db_path = os.path.join(library, "database/photos.sqlite")
    else:
        # Otherwise, ensure the provided file ends with 'photos.sqlite'.
        if not library.lower().endswith("photos.sqlite"):
            raise ValueError(f"Could not find Photos database in {library}")
        db_path = library

    # Confirm the database file exists.
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Could not find Photos database in {db_path}")

    return db_path


def create_and_populate_album(title: str, uuids: list[str]) -> photoscript.Album:
    """Create and populate an album with the given photo UUIDs.

    Args:
        title: The title of the album.
        uuids: The UUIDs of the photos to add to the album.

    Returns: The created album.

    Note: If an album of the same name already exists, a new album will still be created.
    Photos allows multiple albums with the same name.
    """
    library = photoscript.PhotosLibrary()
    album = library.create_album(title)
    uuid_count = len(uuids)
    with Progress() as progress:
        task = progress.add_task(
            f"Adding {uuid_count} photo{'s' if uuid_count != 1 else ''} to album '{title}'",
            total=uuid_count,
        )
        for uuid_chunk in chunked(uuids, 10):
            photos = uuids_to_photos(uuid_chunk, progress.console)
            album.add(photos)
            progress.update(task, advance=len(uuid_chunk))
    return album


def uuids_to_photos(uuids: list[str], console: Console) -> list[photoscript.Photo]:
    """Convert a list of photo UUIDs to a list of Photo objects. Ignore invalid UUIDs.

    Args:
        uuids: The UUIDs of the photos.
        console: The console to write messages to.

    Returns: A list of Photo objects.
    """
    library = photoscript.PhotosLibrary()
    photos = []
    for uuid in uuids:
        try:
            photo = photoscript.Photo(uuid)
            photos.append(photo)
        except ValueError:
            console.log(
                f"Skipping invalid photo UUID: {uuid} (the associated photo may have been deleted)"
            )
    return photos


def select_album_or_exit(albums: list[Album]) -> Album:
    album_choices = {
        f"{album.title} ({album.uuid}), "
        + f"deleted on {album.trashed_date.strftime('%Y-%m-%d')}, "
        + f"contains {album.photo_count} photo{'s' if album.photo_count != 1 else ''}": album
        for album in albums
    }
    choices = list(album_choices.keys())
    choices.append("Exit")
    choice = questionary.select("Select an album to restore", choices=choices).ask()
    if choice == "Exit":
        exit()
    return album_choices[choice]


@click.command(name="recover_photos_albums")
@click.option("--library", type=click.Path(exists=True))
def main(library: str | None):
    """Recover deleted albums from a Photos library"""

    # list all deleted albums and ask user to select one
    library = get_db_path(library)
    albums = [album for album in get_albums_info(library) if album.trashed]
    album = select_album_or_exit(albums)

    confirm = questionary.confirm(
        f"Are you sure you want to restore the album '{album.title}'?"
    ).ask()
    if not confirm:
        exit()

    # get the photos in the album and create a new album with them
    photo_uuids = get_photos_in_album(library, album.uuid)
    create_and_populate_album(album.title, photo_uuids)


if __name__ == "__main__":
    main()
