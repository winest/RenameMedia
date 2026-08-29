import os
import sys
import logging
import fnmatch
import re
import datetime
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
g_reCallLogInfo = re.compile( g_strLeadComment + r"call_([0-9]{2})-([0-9]{2})-([0-9]{2})_(IN|OUT)_([0-9]{7,})" + g_strTailComment )

#My-Description-00000PORTRAIT_00000_BURST20180219112226674-My-Description
#My-Description-00100dPORTRAIT_00100_BURST20180219112230359_COVER-My-Description
g_reProtrait = re.compile( g_strLeadComment + r"([0-9]+)?[a-z]?PORTRAIT_([0-9]+)?_BURST([0-9]+)?(_COVER)?" + g_strTailComment )

#My-Description-Screenshot_2016-07-31-20-50-59-My-Description, My-Description-C360_2016-07-31-20-50-59-123-My-Description
g_reDateTime = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{4})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})[ _:-]([0-9]{2})([ _:-][0-9]{3})?" + g_strTailComment )

#My-Description-SKY_20201103_052334_-My-Description, My-Description-SKY_20200819_000334_3083294833422349157-My-Description
g_reDateTimeBetter = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{8}_[0-9]{6})_?([0-9]+)?" + g_strTailComment )

#My-Description-1472373079120-My-Description, My-Description-FB_IMG_1469184029530-My-Description
g_reTimeStamp = re.compile( g_strLeadComment + g_strPrefix + r"([0-9]{10,13})" + g_strTailComment )



#Get a filename that doesn't exists in aDirName
def GetNewFileName( aDirName , aBaseName , aExt ) :
    if ( os.path.isfile(aDirName + aBaseName + aExt) ) :
        num = 1
        strTmpName = "{}-{}{}".format( aBaseName , num , aExt )
        while ( os.path.isfile(aDirName + strTmpName) ) :
            num += 1
            strTmpName = "{}-{}{}".format( aBaseName , num , aExt )
        strNewFileName = strTmpName
    else :
        strNewFileName = aBaseName + aExt
    return strNewFileName



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



def TryRenameFile( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    aryAlreadyRenamed = g_reAlreadyRenamed.match( aBaseName )
    if ( aryAlreadyRenamed ) :
        return True
    else :
        dateFinal = GetTimeByExif( strPath , strDir , strFileName , strBaseName , strExt ) or \
                    GetTimeByFileName( strPath , strDir , strFileName , strBaseName , strExt ) or \
                    GetTimeByMediaInfo( strPath , strDir , strFileName , strBaseName , strExt )
        strNewBaseName = dateFinal.strftime( "%Y%m%d_%H%M%S" )

        strLeadComment , strTailComment = GetComments( strPath , strDir , strFileName , strBaseName , strExt )
        if strTailComment :
            strNewBaseName = strNewBaseName + "-" + strTailComment
        if strLeadComment :
            strNewBaseName = strLeadComment + "-" + strNewBaseName

        strNewFileName = GetNewFileName( aDirName , strNewBaseName , aExt )
        logging.info( "{} => {}".format(aFileName , strNewFileName) )
        os.rename( aFilePath , aDirName + strNewFileName )
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




if __name__ == "__main__" :
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

    strPath = os.path.realpath( os.getcwd() )
    if ( 2 <= len(sys.argv) ) :
        strPath = os.path.realpath( sys.argv[1] )

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



    reFilter = re.compile( r".*\.(bmp|jpg|jpeg|png|gif|heic|mp3|mp4|mov|m4a|avi|amr|aac|flac)$" , re.IGNORECASE )
    try :
        #Add _Tools directory to %PATH%
        if os.environ["PATH"].find( "MediaInfo" ) == -1 :
            strToolDir = "{}\\_Tools\\x86\\".format( strDataDir )
            os.environ["PATH"] = strToolDir + ";" + os.environ["PATH"]

        logging.info( "Search {} under \"{}\"".format(reFilter,strPath) )
        if ( os.path.isfile(strPath) ) :
            if reFilter.match( strPath ) :
                strDir , strFileName = os.path.split( strPath )
                strBaseName , strExt = os.path.splitext( strFileName )
                if ( 0 < len(strDir) and '\\' != strDir[-1] ) :
                    strDir += "\\"
                TryRenameFile( strPath , strDir , strFileName , strBaseName , strExt )
        elif ( os.path.isdir(strPath) ) :
            for strDir , lsDirNames , lsFileNames in os.walk( strPath ) :
                for strFileName in lsFileNames :
                    if reFilter.match( strFileName ) :
                        strBaseName , strExt = os.path.splitext( strFileName )
                        strPath = os.path.join( strDir , strFileName )
                        if ( 0 < len(strDir) and '\\' != strDir[-1] ) :
                            strDir += "\\"
                        TryRenameFile( strPath , strDir , strFileName , strBaseName , strExt )
        else :
            logging.info( "Usage: {} <file or directory path>".format( os.path.basename(__file__) ) )
    except Exception as ex :
        logging.exception( "strPath={}".format(strPath) )

    logging.info( "End of the program" )
    logging.shutdown()
    print( "Press any key to leave" )
    input()