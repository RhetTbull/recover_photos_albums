# Recover Deleted Apple Photos Albums

A Python script to recover deleted albums from your Apple Photos library on macOS.
This script identifies deleted albums by querying the Photos database
and lets you select an album to restore.

## Usage

I recommend running this script with [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv run recover_photos_albums.py
```

Alternatively, you can run the script directly. Create a virtual environment and install the dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 recover_photos_albums.py
```

## How it works

When you delete an album in the Photos app, it is not normally possible to recover the album.
(If you have not yet quit Photos since deleting the album, you can undo the delete operation with `Edit > Undo`.)
However, the album is not immediately removed from the Photos database.
Photos stores data about the Photos library in a SQLite database nominally located at `~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite`.
The script queries this database to find albums that have been deleted and allows you to select an album to restore.
The photos associated with the album are retrieved from the database and the Photos AppleScript interface is used
to create a new album with the same name as the deleted album and add the photos to the album.

## Limitations

This script only works on macOS.
It uses the Photos AppleScript interface to create the album and add the photos.
If the album is very large (1000s of photos), the script may take a long time to run.

The album sort order will not be preserved when restoring the album. The restored album will
use the default sort order for albums in the Photos app. (Sorted by date, oldest first.)

Only top-level albums can be restored. The script cannot restore albums that are contained within folders.
(It is technically feasible to restore albums within folders, but this is not currently implemented.)
The script cannot restore any photos that have been permanently deleted from the Photos library.

The script cannot restore Smart Albums or Shared Albums.

This has been tested on macOS Ventura 13.7.1 but it should work on any version of macOS since Catalina, 10.15.
It will not work on earlier versions of macOS as the Photos database schema was different before Catalina.

## See Also

- [osxphotos](https://github.com/RhetTbull/osxphotos)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
