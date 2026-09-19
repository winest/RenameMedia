# RenameMedia

Rename media using photo metadata, filename, then MediaInfo.

## Setup
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-transcription.txt # Optional
```

## Usage
```powershell
python RenameMedia.py "C:\Media" --dry-run
python CreateFoldersByDate.py "C:\Media" --dry-run
python TranscribeMedia.py inventory "C:\Media" --output "inventory.json"
python TranscribeMedia.py samples "selection.json" --speech-config "speech.json" --text-config "title_config.py" --text-tenant "<tenant-id>" --cache "Logs\Samples"
python TranscribeMedia.py run "inventory.json" --speech-config "speech.json" --text-config "title_config.py" --text-tenant "<tenant-id>" --cache "Logs\Transcription"
```

`RenameMedia.py` reads `contact.vcf` from the target root (`C:\Media\contact.vcf` above), or beside a single input file. Override with `--vcf "path\contacts.vcf"`.

`selection.json` contains 1-5 records from `inventory.json`'s `files` array (10 minutes each, 30 total). Supply your own service configuration files. `samples` and `run` incur charges; only `run` renames files.

## Naming
- Photos: `YYYYMMDD_HHMMSS.jpg`; existing descriptions keep their position before or after the timestamp.
- Recordings: `YYYYMMDD_HHMMSS-place-summary.mp3`.
- Calls: `YYYYMMDD_HHMMSS-phone(contact)-IN-summary.m4a` (`IN` = incoming, `OUT` = outgoing).

Extensions and existing timestamps are preserved. Transcription adds a summary when available; unknown place, phone, contact, or direction fields are omitted, not invented. Subtitles share the media basename with `.srt`; media without speech gets no `.srt` and keeps its original name.

[Build](GenerateExe.bat) | [Design notes](.github/copilot-instructions.md)

## Author
[ChienWei Hung](https://www.linkedin.com/profile/view?id=351402223)
