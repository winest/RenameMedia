import os
import sys
import logging
import fnmatch
import re
import datetime
import argparse
import hashlib
import json
import stat
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import ExitStack, contextmanager
import exifread
from pymediainfo import MediaInfo


#For C:\\111\\222\\333.444
#DirName = C:\\111\\222\\
#FileName = 333.444
#BaseName = 333
#ExtName = .444

#Ignore if the time we parsed is earlier than this
g_dateEarliest = datetime.datetime.strptime( "20041231_235959.999" , "%Y%m%d_%H%M%S.%f" )

#Our goal is to rename file to the format of YYMMDD_HHMMSS-Description
#My-Description-20120131_112233-My-Description
g_reAlreadyRenamed = re.compile( r"^(.*?-)?[0-9]{8}_[0-9]{6}(-.*)?$" )



#A description is only a description when it is separated by "-". Without the mandatory
#"-", "(.*?)-?" also swallows a camera prefix, so IMG_20260829_152440.jpg ends up with
#the comment "-IMG_" instead of no comment at all.
g_strLeadComment = r"^(?:(.*?)-)?"
g_strTailComment = r"(?:-(.*))?$"

#Anything glued to the front with "_" is a camera or app prefix such as IMG_, MVIMG_,
#SKY_, Screenshot_, C360_ or FB_IMG_, never a description. Every segment must start with
#a letter, so the prefix cannot eat the leading digits of the timestamp that follows, and
#the trailing "_" is mandatory so it cannot eat a "-" separated description either.
g_strPrefix = r"(?:([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z][A-Za-z0-9]*)*)_)?"

#My-Description-call_17-25-55_IN_0934023893-My-Description
g_reCallLogInfo = re.compile( g_strLeadComment + r"call_([0-9]{2})-([0-9]{2})-([0-9]{2})_(IN|OUT)_(\+?[0-9]{7,}(?:\([^)]*\))?)" + g_strTailComment )

#My-Description-00000PORTRAIT_00000_BURST20180219112226674-My-Description
#My-Description-00100dPORTRAIT_00100_BURST20180219112230359_COVER-My-Description
g_reProtrait = re.compile( g_strLeadComment + r"([0-9]+)?[a-z]?PORTRAIT_([0-9]+)?_BURST([0-9]+)?(_COVER)?" + g_strTailComment )

#My-Description-Screenshot_2016-07-31-20-50-59-My-Description, My-Description-C360_2016-07-31-20-50-59-123-My-Description
g_reDateTime = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{4})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})([ _:-][0-9]{3})?" + g_strTailComment )

#My-Description-SKY_20201103_052334_-My-Description, My-Description-SKY_20200819_000334_3083294833422349157-My-Description
g_reDateTimeBetter = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{8}_[0-9]{6})_?([0-9]+)?" + g_strTailComment )

#My-Description-1472373079120-My-Description, My-Description-FB_IMG_1469184029530-My-Description
g_reTimeStamp = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{10,13})" + g_strTailComment )

g_reRecording = re.compile(r"^(?:line|phone)_([0-9]{8})-([0-9]{6})(?:_(.+))?$", re.IGNORECASE)

RECORDING_EXTENSIONS = frozenset({
    ".aac", ".aiff", ".amr", ".avi", ".flac", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".ogg", ".opus", ".wav", ".webm", ".wma", ".3gp",
})
PHONE_FIELD = r"(?P<phone>\+?[0-9]{7,15})(?=$|[-_(])"
PHONE_PATTERNS = (
    re.compile(r"^(?:.*?-)?[0-9]{8}_[0-9]{6}[-_]" + PHONE_FIELD),
    re.compile(r"^(?:.*-)?call_[0-9]{2}-[0-9]{2}-[0-9]{2}_(?:IN|OUT)_" + PHONE_FIELD, re.I),
    re.compile(r"^(?:line|phone)_[0-9]{8}-[0-9]{6}_" + PHONE_FIELD, re.I),
    re.compile(r"^(?P<phone>\+?[0-9]{7,15})(?=-(?:[0-9]{8}_[0-9]{6})(?:-|$))"),
)


def ParseRecording(base_name, extension):
    if extension.lower() != ".amr":
        return None
    match = g_reRecording.fullmatch(base_name)
    if match is None:
        return None
    date = datetime.datetime.strptime(
        match.group(1) + "_" + match.group(2), "%Y%m%d_%H%M%S"
    )
    return date, match.group(3) or ""



#Get a filename that doesn't exists in aDirName
def GetNewFileName( aDirName , aBaseName , aExt ) :
    return choose_destination(aDirName, aBaseName, aExt).name



#Append aText to aList when it carries something
def AppendComment( aList , aText ) :
    if aText and 0 < len( aText ) :
        aList.append( aText )



#Get the comments from the original file name, split into the part that was written in
#front of the timestamp and the part that followed it, so that a comment keeps its
#original side, e.g. My-Description-IMG_20260123_112233 => My-Description-20260123_112233
def GetComments( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    lsLead = []
    lsTail = []
    for count in range( 1 ) :
        #My-Description-call_17-25-55_IN_0934023893-My-Description
        aryCallLogInfo = g_reCallLogInfo.match( aBaseName )
        if ( aryCallLogInfo ) :
            AppendComment( lsLead , aryCallLogInfo.group(1) )
            AppendComment( lsTail , aryCallLogInfo.group(6) )
            AppendComment( lsTail , aryCallLogInfo.group(5) )
            AppendComment( lsTail , aryCallLogInfo.group(7) )
            break

        #My-Description-00000PORTRAIT_00000_BURST20180219112226674-My-Description
        #My-Description-00100dPORTRAIT_00100_BURST20180219112230359_COVER-My-Description
        aryCallPortrait = g_reProtrait.match( aBaseName )
        if ( aryCallPortrait ) :
            AppendComment( lsLead , aryCallPortrait.group(1) )
            AppendComment( lsTail , aryCallPortrait.group(6) )
            break

        #My-Description-C360_2016-07-31-20-50-59-123-My-Description
        aryDateTime = g_reDateTime.match( aBaseName )
        if ( aryDateTime ) :
            AppendComment( lsLead , aryDateTime.group(1) )
            AppendComment( lsTail , aryDateTime.group(10) )
            break

        #My-Description-SKY_20201103_052334_-My-Description
        #My-Description-SKY_20200819_000334_3083294833422349157-My-Description
        aryDateTimeBetter = g_reDateTimeBetter.match( aBaseName )
        if ( aryDateTimeBetter ) :
            AppendComment( lsLead , aryDateTimeBetter.group(1) )
            AppendComment( lsTail , aryDateTimeBetter.group(5) )
            break

        #My-Description-FB_IMG_1469184029530-My-Description
        aryTimeStamp = g_reTimeStamp.match( aBaseName )
        if ( aryTimeStamp ) :
            AppendComment( lsLead , aryTimeStamp.group(1) )
            AppendComment( lsTail , aryTimeStamp.group(4) )
            break

    return "-".join( lsLead ) , "-".join( lsTail )



def TryRenameFile(
    aFilePath, aDirName, aFileName, aBaseName, aExt, aRootDir=None,
    contacts=None, contacts_only=False, dry_run=False, journal_dir=None,
):
    aryAlreadyRenamed = g_reAlreadyRenamed.match( aBaseName )
    if aryAlreadyRenamed or contacts_only:
        strNewBaseName = aBaseName
    else :
        recording = ParseRecording(aBaseName, aExt)
        if recording:
            dateFinal, strTailComment = recording
            strLeadComment = ""
        else:
            dateFinal = GetTimeByExif( aFilePath , aDirName , aFileName , aBaseName , aExt ) or \
                        GetTimeByFileName( aFilePath , aDirName , aFileName , aBaseName , aExt ) or \
                        GetTimeByMediaInfo( aFilePath , aDirName , aFileName , aBaseName , aExt )
            strLeadComment , strTailComment = GetComments( aFilePath , aDirName , aFileName , aBaseName , aExt )
        strNewBaseName = dateFinal.strftime( "%Y%m%d_%H%M%S" )

        if strTailComment :
            strNewBaseName = strNewBaseName + "-" + strTailComment
        if strLeadComment :
            strNewBaseName = strLeadComment + "-" + strNewBaseName

    strNewBaseName = annotate_contact(strNewBaseName, aExt, contacts)
    if strNewBaseName == aBaseName:
        return True
    strNewFileName = GetNewFileName(aDirName, strNewBaseName, aExt)
    destination = Path(aDirName) / strNewFileName
    rename_pair(Path(aFilePath), destination, journal_dir=journal_dir, dry_run=dry_run)
    relative_dir = os.path.relpath(aDirName, aRootDir or aDirName)
    logging.info("%s%s: %s => %s", "Would rename " if dry_run else "", relative_dir, aFileName, strNewFileName)
    return True






def GetTimeByExif( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    dateFinal = None

    #Open image file for reading (binary mode)
    with open( aFilePath , "rb" ) as f :
        tags = exifread.process_file( f )
        #for tag in sorted( tags.keys() ):
        #    if tag not in ('JPEGThumbnail', 'TIFFThumbnail', 'Filename', 'EXIF MakerNote'):
        #        logging.debug( "Key: {}, value {}".format(tag, tags[tag]) )

    for count in range( 1 ) :
        if "EXIF DateTimeOriginal" in tags :
            #2015:12:31 00:12:34
            dateExif = datetime.datetime.strptime( tags["EXIF DateTimeOriginal"].printable , "%Y:%m:%d %H:%M:%S" )
            if g_dateEarliest < dateExif and dateExif < datetime.datetime.now() :
                dateFinal = dateExif
                break
    else :
        logging.debug( "EXIF not found. aFilePath={}".format(aFilePath) )

    if dateFinal :
        logging.info( "GetTimeByExif() succeed" )
    return dateFinal



def GetTimeByFileName( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    dateFinal = None

    for count in range( 1 ) :
        #My-Description-C360_2016-07-31-20-50-59-123-My-Description
        aryDateTime = g_reDateTime.match( aBaseName )
        if ( aryDateTime ) :
            dateDateTime = datetime.datetime( year=(int)(aryDateTime.group(3)) , month=(int)(aryDateTime.group(4)) , day=(int)(aryDateTime.group(5)) ,
                                              hour=(int)(aryDateTime.group(6)) , minute=(int)(aryDateTime.group(7)) , second=(int)(aryDateTime.group(8)) )
            if g_dateEarliest < dateDateTime and dateDateTime < datetime.datetime.now() :
                dateFinal = dateDateTime
                break



        #My-Description-SKY_20201103_052334_-My-Description
        #My-Description-SKY_20200819_000334_3083294833422349157-My-Description
        aryDateTimeBetter = g_reDateTimeBetter.match( aBaseName )
        if ( aryDateTimeBetter ) :
            dateDateTimeBetter = datetime.datetime.strptime( aryDateTimeBetter.group(3) , "%Y%m%d_%H%M%S" )
            if g_dateEarliest < dateDateTimeBetter and dateDateTimeBetter < datetime.datetime.now() :
                dateFinal = dateDateTimeBetter
                break



        #My-Description-FB_IMG_1469184029530-My-Description
        aryTimeStamp = g_reTimeStamp.match( aBaseName )
        if ( aryTimeStamp ) :
            #Convert timestamp to specific format
            strTime = aryTimeStamp.group( 3 )
            if ( len(strTime) > 10 ) :
                strTime = strTime[:10]
            dateTimeStamp = datetime.datetime.fromtimestamp( int(strTime) )
            if g_dateEarliest < dateTimeStamp and dateTimeStamp < datetime.datetime.now() :
                dateFinal = dateTimeStamp
                break
    else :
        logging.debug( "Time not found in filename. aFilePath={}".format(aFilePath) )

    if dateFinal :
        logging.info( "GetTimeByFileName() succeed" )
    return dateFinal



def GetTimeByMediaInfo( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    dateNow = datetime.datetime.now()
    dateFinal = dateNow

    info = MediaInfo.parse( aFilePath )
    logging.debug( info.to_json() )

    for track in info.tracks :
        if track.track_type == "General" :
            dateEncoded = datetime.datetime.now()
            dateCreation = datetime.datetime.now()
            dateModification = datetime.datetime.now()
            if track.encoded_date :
                try:
                    dateEncoded = datetime.datetime.strptime( track.encoded_date , "UTC %Y-%m-%d %H:%M:%S" ) + ( datetime.datetime.now() - datetime.datetime.utcnow() )
                except ValueError as err:
                    dateEncoded = datetime.datetime.strptime( track.encoded_date , "%Y-%m-%d %H:%M:%S UTC" ) + ( datetime.datetime.now() - datetime.datetime.utcnow() )
                if track.duration :
                    dateEncoded = dateEncoded - datetime.timedelta( milliseconds=track.duration )
                if g_dateEarliest < dateEncoded and dateEncoded < dateFinal :
                    dateFinal = dateEncoded
                # iPhone might set incorrect creation/modification time, so break here if encoded date exists
                break
            if track.file_creation_date__local :
                dateCreation = datetime.datetime.strptime( track.file_creation_date__local , "%Y-%m-%d %H:%M:%S.%f" )
                if g_dateEarliest < dateCreation and dateCreation < dateFinal :
                    dateFinal = dateCreation
            if track.file_last_modification_date__local :
                dateModification = datetime.datetime.strptime( track.file_last_modification_date__local , "%Y-%m-%d %H:%M:%S.%f" )
                if g_dateEarliest < dateModification and dateModification < dateFinal :
                    dateFinal = dateModification

            if dateFinal != dateNow :
                break
    else :
        logging.warning( "MediaInfo not found. aFilePath={}".format(aFilePath) )

    if dateFinal :
        logging.info( "GetTimeByMediaInfo() succeed" )
    return dateFinal




def normalize_phone(value):
    value = unicodedata.normalize("NFKC", str(value)).strip()
    value = re.sub(r"^tel:", "", value, flags=re.I)
    value = re.split(r"(?:;ext=|[,;#]|(?:ext\.?|extension|x)\s*[:=]?)", value, maxsplit=1, flags=re.I)[0]
    if not re.fullmatch(r"\+?[0-9().\s-]+", value):
        return None
    number = re.sub(r"[().\s-]", "", value)
    if number.startswith("00"):
        number = "+" + number[2:]
    elif number.startswith("886"):
        number = "+" + number
    elif number.startswith("0"):
        if not 8 <= len(number) <= 11:
            return None
        number = "+886" + number[1:]
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        return None
    return number


def phone_match(stem):
    for pattern in PHONE_PATTERNS:
        match = pattern.match(stem)
        if match and normalize_phone(match["phone"]):
            return match
    return None


def safe_contact_name(name):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', " ", name)
    name = " ".join(name.split()).strip(" .")
    return name.replace("(", "\uff08").replace(")", "\uff09")


@dataclass
class ContactBook:
    names: dict[str, set[str]] = field(default_factory=dict)

    def lookup(self, phone):
        names = self.names.get(normalize_phone(phone), set())
        if len(names) > 1:
            logging.warning("Conflicting contact names for %s; leaving it unchanged", phone)
            return None
        return safe_contact_name(next(iter(names))) if names else None


def load_contacts(root, vcf=None, disabled=False):
    book = ContactBook()
    if disabled:
        return book
    path = Path(vcf) if vcf is not None else Path(root) / "contact.vcf"
    if vcf is None and not path.exists():
        logging.info("No default contact file: %s", path)
        return book

    import vobject

    records = unnamed = ignored_numbers = 0
    with path.open(encoding="utf-8-sig") as file:
        for card in vobject.readComponents(file, allowQP=True):
            if card.name != "VCARD":
                raise ValueError(f"Expected a vCard in {path}")
            records += 1
            full_names = card.contents.get("fn", [])
            name = str(full_names[0].value).strip() if full_names else ""
            if not name and card.contents.get("n"):
                structured = card.n.value
                if re.search(r"[\u3400-\u9fff]", structured.family + structured.given):
                    name = " ".join(part for part in (
                        structured.prefix, structured.family + structured.given,
                        structured.additional, structured.suffix,
                    ) if part)
                else:
                    name = str(structured).strip()
            if not name and card.contents.get("org"):
                name = " ".join(card.org.value).strip()
            if not safe_contact_name(name):
                unnamed += 1
                continue
            for telephone in card.contents.get("tel", []):
                number = normalize_phone(telephone.value)
                if number:
                    book.names.setdefault(number, set()).add(name)
                else:
                    ignored_numbers += 1
    if records == 0:
        raise ValueError(f"No vCards found in {path}")
    logging.info(
        "Contacts: %d cards, %d phone numbers, %d conflicting numbers, "
        "%d unnamed cards, %d unusable phone fields",
        records, len(book.names), sum(len(names) > 1 for names in book.names.values()),
        unnamed, ignored_numbers,
    )
    return book


def annotate_contact(stem, extension, contacts):
    if extension.lower() not in RECORDING_EXTENSIONS or contacts is None:
        return stem
    match = phone_match(stem)
    if match is None or stem[match.end("phone"):].startswith("("):
        return stem
    name = contacts.lookup(match["phone"])
    if not name:
        return stem
    end = match.end("phone")
    return stem[:end] + "(" + name + ")" + stem[end:]


@contextmanager
def exclusive_renames(directory):
    if os.name != "nt":
        raise RuntimeError("Journaled renames require Windows non-overwriting rename semantics")
    import msvcrt

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another media rename or recovery is running") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def fingerprint(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError(f"Expected a regular file: {path}")
    return [info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev]


def verify(path, expected):
    if fingerprint(path) != expected:
        raise ValueError(f"File changed during rename: {path}")


def choose_destination(directory, stem, extension):
    directory = Path(directory)
    for number in range(10000):
        candidate = stem if number == 0 else f"{stem}-{number}"
        name = candidate + extension
        if len(name.encode("utf-16-le")) // 2 > 255:
            raise ValueError("Contact or description makes the filename too long")
        path = directory / name
        if not os.path.lexists(path) and not os.path.lexists(path.with_suffix(".srt")):
            return path
    raise FileExistsError(f"No available filename in {directory}")


def pair_state(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.parent != destination.parent or source == destination:
        raise ValueError("A rename must use a different name in the same directory")
    sidecar = source.with_suffix(".srt")
    new_sidecar = destination.with_suffix(".srt")
    if os.path.lexists(destination) or os.path.lexists(new_sidecar):
        raise FileExistsError(f"Rename destination is occupied: {destination}")
    has_sidecar = os.path.lexists(sidecar)
    if has_sidecar:
        for sibling in source.parent.iterdir():
            if (
                sibling != source and sibling.stem.casefold() == source.stem.casefold()
                and sibling.suffix.lower() in RECORDING_EXTENSIONS
            ):
                raise ValueError(f"Subtitle ownership is ambiguous: {sidecar}")
    return {
        "source": str(source), "destination": str(destination),
        "media_fingerprint": fingerprint(source),
        "sidecar": str(sidecar) if has_sidecar else None,
        "new_sidecar": str(new_sidecar) if has_sidecar else None,
        "subtitle_fingerprint": fingerprint(sidecar) if has_sidecar else None,
        "subtitle_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest() if has_sidecar else None,
    }


def write_journal(path, state):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def move_checked(source, destination, expected):
    if os.path.lexists(source):
        verify(source, expected)
        if os.path.lexists(destination):
            raise FileExistsError(f"Rename destination is occupied: {destination}")
        os.rename(source, destination)
    verify(destination, expected)


def finish_pair(state):
    source, destination = Path(state["source"]), Path(state["destination"])
    move_checked(source, destination, state["media_fingerprint"])
    if state["sidecar"]:
        sidecar, new_sidecar = Path(state["sidecar"]), Path(state["new_sidecar"])
        existing = sidecar if os.path.lexists(sidecar) else new_sidecar
        if hashlib.sha256(existing.read_bytes()).hexdigest() != state["subtitle_sha256"]:
            raise ValueError(f"Subtitle content changed: {existing}")
        move_checked(sidecar, new_sidecar, state["subtitle_fingerprint"])


def rename_pair(source, destination, journal_dir=None, dry_run=False):
    state = pair_state(source, destination)
    if dry_run:
        return state
    journal = None
    if journal_dir is not None:
        directory = Path(journal_dir)
        directory.mkdir(parents=True, exist_ok=True)
        journal = directory / (uuid.uuid4().hex + ".pending.json")
        write_journal(journal, state)
    try:
        finish_pair(state)
    except (OSError, ValueError):
        try:
            for old_key, new_key, fingerprint_key in (
                ("sidecar", "new_sidecar", "subtitle_fingerprint"),
                ("source", "destination", "media_fingerprint"),
            ):
                if state[old_key]:
                    old, new = Path(state[old_key]), Path(state[new_key])
                    if not os.path.lexists(old) and os.path.lexists(new):
                        move_checked(new, old, state[fingerprint_key])
        except (OSError, ValueError):
            logging.exception("Pair rollback failed; recover the pending journal: %s", journal)
            raise
        if journal:
            journal.rename(journal.with_name(journal.name.replace(".pending.", ".rolled-back.")))
        raise
    if journal:
        journal.rename(journal.with_name(journal.name.replace(".pending.", ".done.")))
    return state


def recover_pending(journal_dir, target, dry_run=False):
    target = Path(target).absolute()
    for journal in sorted(Path(journal_dir).glob("*.pending.json")):
        state = json.loads(journal.read_text(encoding="utf-8"))
        source, destination = Path(state["source"]), Path(state["destination"])
        if not (source == target or destination == target or target.is_dir() and source.is_relative_to(target)):
            continue
        if source.parent != destination.parent or (
            state["sidecar"] and (
                Path(state["sidecar"]) != source.with_suffix(".srt")
                or Path(state["new_sidecar"]) != destination.with_suffix(".srt")
            )
        ):
            raise ValueError(f"Invalid rename journal: {journal}")
        logging.warning("%s pending pair: %s", "Would recover" if dry_run else "Recovering", journal)
        if not dry_run:
            finish_pair(state)
            journal.rename(journal.with_name(journal.name.replace(".pending.", ".done.")))


def ParseArguments(argv=None):
    parser = argparse.ArgumentParser(description="Rename media by date and optional local contacts.")
    parser.add_argument("path", nargs="?", default=os.getcwd(), help="Input file or directory")
    contact_options = parser.add_mutually_exclusive_group()
    contact_options.add_argument("--vcf", help="Contact file (default: input root\\contact.vcf)")
    contact_options.add_argument("--no-contacts", action="store_true", help="Disable contact lookup")
    parser.add_argument("--contacts-only", action="store_true", help="Only add contact names; keep dates and descriptions")
    parser.add_argument("--dry-run", action="store_true", help="Preview without renaming files")
    args = parser.parse_args(argv)
    if args.contacts_only and args.no_contacts:
        parser.error("--contacts-only cannot be combined with --no-contacts")
    return args


if __name__ == "__main__" :
    args = ParseArguments()
    if getattr( sys , "frozen" , False ) :
        #Logs go next to the exe, while _Tools is unpacked by PyInstaller into sys._MEIPASS
        strScriptDir = os.path.dirname( os.path.realpath(sys.executable) )
        strDataDir = getattr( sys , "_MEIPASS" , strScriptDir )
    else :
        strScriptDir = os.path.dirname( os.path.realpath(__file__) )
        strDataDir = strScriptDir
    strLogDir = "{}\\Logs".format( strScriptDir )
    if not os.path.isdir( strLogDir ) :
        os.makedirs( strLogDir )

    strPath = os.path.realpath(args.path)
    strRootDir = strPath if os.path.isdir(strPath) else os.path.dirname(strPath)

    #The console uses the OS locale encoding (cp1252 on an English Windows), which cannot
    #encode paths containing characters outside it. Without this, logging a path such as
    #"C:\\Temp\\測試\\a.jpg" raises UnicodeEncodeError from the charmap codec and the whole
    #log record is dropped instead of being written.
    for stream in ( sys.stdout , sys.stderr ) :
        if stream is not None and hasattr( stream , "reconfigure" ) :
            stream.reconfigure( errors="backslashreplace" )

    logger = logging.getLogger()
    logger.setLevel( logging.INFO )

    fmtConsole = logging.Formatter( "[%(asctime)s][%(levelname)s]: %(message)s" )
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter( fmtConsole )
    logger.addHandler( consoleHandler )

    fmtFile = logging.Formatter( "[%(asctime)s][%(levelname)s][%(process)04X:%(thread)04X][%(filename)s][%(funcName)s_%(lineno)d]: %(message)s" )
    fileHandler = logging.FileHandler( "{}\\{}-{}.txt".format(strLogDir , os.path.basename(strPath) , datetime.datetime.now().strftime("%Y%m%d_%H%M%S")) , encoding="utf-8" )
    fileHandler.setFormatter( fmtFile )
    logger.addHandler( fileHandler )



    extensions = RECORDING_EXTENSIONS
    if not args.contacts_only:
        extensions = extensions | {".bmp", ".jpg", ".jpeg", ".png", ".gif", ".heic"}
    exit_code = 0
    locks = ExitStack()
    try :
        contacts = load_contacts(strRootDir, args.vcf, args.no_contacts)
        journal_dir = Path(strLogDir) / "RenameTransactions"
        locks.enter_context(exclusive_renames(journal_dir))
        recover_pending(journal_dir, strPath, dry_run=args.dry_run)
        #Add _Tools directory to %PATH%
        if os.environ["PATH"].find( "MediaInfo" ) == -1 :
            strToolDir = "{}\\_Tools\\x86\\".format( strDataDir )
            os.environ["PATH"] = strToolDir + ";" + os.environ["PATH"]

        logging.info("Search %s under \"%s\"", ", ".join(sorted(extensions)), strPath)
        if ( os.path.isfile(strPath) ) :
            if Path(strPath).suffix.lower() in extensions:
                strDir , strFileName = os.path.split( strPath )
                strBaseName , strExt = os.path.splitext( strFileName )
                if ( 0 < len(strDir) and '\\' != strDir[-1] ) :
                    strDir += "\\"
                TryRenameFile(
                    strPath, strDir, strFileName, strBaseName, strExt, strRootDir,
                    contacts, args.contacts_only, args.dry_run, journal_dir,
                )
        elif ( os.path.isdir(strPath) ) :
            for strDir , lsDirNames , lsFileNames in os.walk( strPath ) :
                lsDirNames[:] = [
                    name for name in lsDirNames
                    if not os.path.islink(os.path.join(strDir, name))
                    and not (
                        hasattr(os.path, "isjunction")
                        and os.path.isjunction(os.path.join(strDir, name))
                    )
                ]
                for strFileName in lsFileNames :
                    if Path(strFileName).suffix.lower() in extensions:
                        strBaseName , strExt = os.path.splitext( strFileName )
                        strPath = os.path.join( strDir , strFileName )
                        if ( 0 < len(strDir) and '\\' != strDir[-1] ) :
                            strDir += "\\"
                        if os.path.islink(strPath):
                            logging.warning("Skipping symbolic link: %s", strPath)
                            continue
                        TryRenameFile(
                            strPath, strDir, strFileName, strBaseName, strExt, strRootDir,
                            contacts, args.contacts_only, args.dry_run, journal_dir,
                        )
        else :
            raise FileNotFoundError(strPath)
    except Exception as ex :
        logging.exception( "strPath={}".format(strPath) )
        exit_code = 1
    finally:
        locks.close()

    logging.info( "End of the program" )
    logging.shutdown()
    if sys.stdin is not None and sys.stdin.isatty() and sys.stdout is not None and sys.stdout.isatty():
        try:
            input("Press Enter to leave")
        except EOFError:
            print("Input is closed; exiting.")
    sys.exit(exit_code)