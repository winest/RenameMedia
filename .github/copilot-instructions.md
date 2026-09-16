# Notes for contributors

Windows only. These scripts rename and delete real files, and one of them spends money.

Three independent scripts, each self-contained:

- `RenameMedia.py` - rename by date, add contact labels, move the matching `.srt`
- `CreateFoldersByDate.py` - move renamed media into `YYYYMMDD` folders
- `TranscribeMedia.py` - `inventory`, `samples`, `run`

## Design decisions

- One file per tool, and one test file per tool. `ContactNames.py`, `MediaRename.py`, and `BulkTranscribe.py` were merged back because the split added confusion, not value. Do not split them out again.
- Date priority is EXIF, filename, then MediaInfo. MediaInfo prefers `encoded_date` because iPhone sets wrong creation and modification dates.
- A description must be separated by `-`. Prefixes glued with `_`, such as `IMG_` or `SKY_`, are camera prefixes and get dropped.
- Contact lookup is local only. Match complete normalized numbers, never a suffix. Two names on one number means skip it. Never add a web lookup.
- Renames never overwrite. Media and its `.srt` move as a pair, journaled under `Logs\RenameTransactions`. Rerun on the same folder to finish an interrupted pair.
- Folders are one per day and never merged. Folders named with a year, a date, or Chinese text are already organized, so they and everything under them are skipped.
- Empty-folder cleanup deletes only `.nomedia`, `Thumbs.db`, and `.DS_Store`. Anything else keeps the folder.
- Transcription cache keys include the endpoint, model, locale, and prompt, so changing a request setting invalidates the cache. Do not change them casually.
- `run` charges money and renames files. It has no preview mode; use `samples` first. Requests with an unknown billing outcome pause instead of retrying.
- Logs, JSON, and subtitles are UTF-8. The console may still be cp1252, so keep the safe stream handling.
- `GenerateExe.bat` uses `--onedir`. `--onefile` unpacks DLLs into `%TEMP%`, which Smart App Control blocks.

## Working here

- Code and comments in simple English. PEP 8 for new code, existing style in old code.
- Never commit media, contacts, transcripts, caches, or build output.
- Tests use temporary files and mock anything paid. Run them after activating the environment:

```powershell
python -m unittest -q test_media test_contacts test_transcribe
```
