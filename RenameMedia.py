import os
import sys
import fnmatch
import re
import datetime
import exifread


#For C:\\111\\222\\333.444
#DirName = C:\\111\\222\\
#FileName = 333.444
#BaseName = 333
#ExtName = .444



#2015:12:31 00:12:34
g_reExifTime = re.compile( r"^([0-9]{4}):([0-9]{2}):([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$" )

#My-Description-Screenshot_2016-07-31-20-50-59-My-Description, My-Description-C360_2016-07-31-20-50-59-123-My-Description
g_reDateTime = re.compile( r"^(.*?)-?(Screenshot|C360)[-_]?([0-9]{4})[ -_:]([0-9]{2})[ -_:]([0-9]{2})[ -_:]([0-9]{2})[ -_:]([0-9]{2})[ -_:]([0-9]{2})([ -_:][0-9]{3})?-?(.*)$" )

#My-Description-1472373079120-My-Description, My-Description-FB_IMG_1469184029530-My-Description
g_reTimeStamp = re.compile( r"^(.*?)-?(FB_IMG)[-_]?([0-9]{10,13})-?(.*)$" )



#Our goal is to rename file to the format of YYMMDD_HHMMSS-Description
#My-Description-20120131_112233-My-Description
g_reAlreadyRenamed = re.compile( r"^(.*?-)?[0-9]{8}_[0-9]{6}(-.*)?$" )



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



def TryRenameFile( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    aryAlreadyRenamed = g_reAlreadyRenamed.match( aBaseName )
    if ( aryAlreadyRenamed ) :
        return True
    else :
        return RenameByExif( strPath , strDir , strFileName , strBaseName , strExt ) or \
               RenameByFileName( strPath , strDir , strFileName , strBaseName , strExt )



def RenameByExif( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    bRet = False
    
    #Open image file for reading (binary mode)
    with open( aFilePath , "rb" ) as f :
        tags = exifread.process_file( f )
        #for tag in sorted( tags.keys() ):
        #    if tag not in ('JPEGThumbnail', 'TIFFThumbnail', 'Filename', 'EXIF MakerNote'):
        #        print( "Key: {}, value {}".format(tag, tags[tag]) )

    #Check tags has "EXIF DateTimeOriginal" attribute
    if "EXIF DateTimeOriginal" not in tags :
        print( "EXIF not found. aFilePath={}".format(aFilePath) )
    else :
        print( "EXIF: {}".format(tags["EXIF DateTimeOriginal"].printable) )
        
        #2015:12:31 00:12:34
        aryExifTime = g_reExifTime.match( tags["EXIF DateTimeOriginal"].printable )
        if ( aryExifTime ) :
            strNewBaseName = "{}{}{}_{}{}{}".format( aryExifTime.group(1) , aryExifTime.group(2) , aryExifTime.group(3) ,
                                                     aryExifTime.group(4) , aryExifTime.group(5) , aryExifTime.group(6) )
            
            #Merge original file name description
            for count in range( 1 ) :
                aryDateTime = g_reDateTime.match( aBaseName )
                if ( aryDateTime ) :
                    if aryDateTime.group(10) and len( aryDateTime.group(10) ) > 0 :
                        strNewBaseName += "-" + aryDateTime.group( 10 )
                    if aryDateTime.group(1) and len( aryDateTime.group(1) ) > 0 :
                        strNewBaseName += "-" + aryDateTime.group( 1 )
                    break

                aryTimeStamp = g_reTimeStamp.match( aBaseName )
                if ( aryTimeStamp ) :
                    if aryTimeStamp.group(4) and len( aryTimeStamp.group(4) ) > 0 :
                        strNewBaseName += "-" + aryTimeStamp.group( 4 )
                    if aryTimeStamp.group(1) and len( aryTimeStamp.group(1) ) > 0 :
                        strNewBaseName += "-" + aryTimeStamp.group( 1 )
                    break

            strNewFileName = GetNewFileName( aDirName , strNewBaseName , aExt )
            bRet = True
        else :
            print( "EXIF format error. aFilePath={}, EXIF={}".format( aFilePath , tags["EXIF DateTimeOriginal"].printable ) )

    if ( bRet ) :
        print( "{} => {}".format(aFileName , strNewFileName) )
        os.rename( aFilePath , aDirName + strNewFileName )
    return bRet



def RenameByFileName( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    bRet = False
    
    for count in range( 1 ) :
        #My-Description-C360_2016-07-31-20-50-59-123-My-Description
        aryDateTime = g_reDateTime.match( aBaseName )
        if ( aryDateTime ) :
            strNewBaseName = "{}{}{}_{}{}{}".format( aryDateTime.group(3) , aryDateTime.group(4) , aryDateTime.group(5) ,
                                                     aryDateTime.group(6) , aryDateTime.group(7) , aryDateTime.group(8) )
            #Merge original file name description
            if aryDateTime.group(10) and len( aryDateTime.group(10) ) > 0 :
                strNewBaseName += "-" + aryDateTime.group( 10 )
            if aryDateTime.group(1) and len( aryDateTime.group(1) ) > 0 :
                strNewBaseName += "-" + aryDateTime.group( 1 )
            strNewFileName = GetNewFileName( aDirName , strNewBaseName , aExt )
            bRet = True
            break



        #My-Description-FB_IMG_1469184029530-My-Description
        aryTimeStamp = g_reTimeStamp.match( aBaseName )
        if ( aryTimeStamp ) :
            #Convert timestamp to specific format
            strTime = aryTimeStamp.group( 3 )
            if ( len(strTime) > 10 ) :
                strTime = strTime[:10]
            strNewBaseName = datetime.datetime.fromtimestamp( int(strTime) ).strftime( "%Y%m%d_%H%M%S" )
            
            #Merge original file name description
            if aryTimeStamp.group(4) and len ( aryTimeStamp.group(4) ) > 0 :
                strNewBaseName += "-" + aryTimeStamp.group( 4 )
            if aryTimeStamp.group(1) and len ( aryTimeStamp.group(1) ) > 0 :
                strNewBaseName += "-" + aryTimeStamp.group( 1 )

            strNewFileName = GetNewFileName( aDirName , strNewBaseName , aExt )
            bRet = True
            break
    else :
        print( "Unknown filename format. aFilePath={}".format( aFilePath ) )

    if ( bRet ) :
        print( "{} => {}".format(aFileName , strNewFileName) )
        os.rename( aFilePath , aDirName + strNewFileName )
    return bRet



if __name__ == "__main__" :
    reFilter = re.compile( ".*\.(jpg|png)$" , re.IGNORECASE )
    strPath = os.getcwd()
    if ( 2 <= len(sys.argv) ) :
        strPath = sys.argv[1]

    try :
        print( "Search {} under \"{}\"".format(reFilter,strPath) )
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
            print( "Usage: {} <file or directory path>".format( os.path.basename( sys.argv[0] ) ) )
    except Exception as ex :
        print( "Error: {}".format(ex) )

    print( "End of the program" )
    print( "Press any key to leave" )
    input()