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


#20160521-MyDescription-MyDescription
g_reBaseNameDesc = re.compile( r"^[^\-]*?-(.+)$" )

#2015:12:31 00:12:34
g_reExifTime = re.compile( r"^([0-9]{4}):([0-9]{2}):([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$" )

#FB_IMG_1469184029530
g_reFbTime = re.compile( r"^FB_IMG_([0-9]{10,})$" )

#Screenshot_2016-07-31-20-50-59
g_reScreenshotTime = re.compile( r".*Screenshot_?([0-9]{4})[-_]([0-9]{2})[-_]([0-9]{2})[-_]([0-9]{2})[-_:]([0-9]{2})[-_:]([0-9]{2})" )



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
    return RenameByExif( strPath , strDir , strFileName , strBaseName , strExt ) or \
           RenameByFileName( strPath , strDir , strFileName , strBaseName , strExt )



def RenameByExif( aFilePath , aDirName , aFileName , aBaseName , aExt ) :
    bRet = False
    
    # Open image file for reading (binary mode)
    with open( aFilePath , "rb" ) as f :
        tags = exifread.process_file( f )
        #for tag in sorted( tags.keys() ):
        #    if tag not in ('JPEGThumbnail', 'TIFFThumbnail', 'Filename', 'EXIF MakerNote'):
        #        print( "Key: {}, value {}".format(tag, tags[tag]) )

    #Check tags has "EXIF DateTimeOriginal" attribute
    if "EXIF DateTimeOriginal" not in tags :
        print( "EXIF not found. aFilePath={}".format(aFilePath) )
    else :
        aryBaseNameDesc = g_reBaseNameDesc.match( aBaseName )
        
        #2015:12:31 00:12:34
        aryTime = g_reExifTime.match( tags["EXIF DateTimeOriginal"].printable )
        if ( aryTime ) :
            #Merge original file name description
            if ( aryBaseNameDesc ) :
                strNewBaseName = "{}{}{}_{}{}{}-{}".format( aryTime.group(1) , aryTime.group(2) , aryTime.group(3) ,
                                                            aryTime.group(4) , aryTime.group(5) , aryTime.group(6) , 
                                                            aryBaseNameDesc.group(1) )
            else :
                strNewBaseName = "{}{}{}_{}{}{}".format( aryTime.group(1) , aryTime.group(2) , aryTime.group(3) ,
                                                         aryTime.group(4) , aryTime.group(5) , aryTime.group(6) )

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
        #FB_IMG_1469184029530
        aryFbTime = g_reFbTime.match( aBaseName )
        if ( aryFbTime ) :
            strTime = aryFbTime.group( 1 )
            if ( len(strTime) > 10 ) :
                strTime = strTime[:10]
            strNewBaseName = datetime.datetime.fromtimestamp( int(strTime) ).strftime( "%Y%m%d_%H%M%S" )
            strNewFileName = GetNewFileName( aDirName , strNewBaseName , aExt )
            bRet = True
            break
        
        #Screenshot_2016-07-31-20-50-59
        aryScreenshotTime = g_reScreenshotTime.match( aBaseName )
        if ( aryScreenshotTime ) :
            strNewBaseName = "{}{}{}_{}{}{}".format( aryScreenshotTime.group(1) , aryScreenshotTime.group(2) , aryScreenshotTime.group(3) ,
                                                     aryScreenshotTime.group(4) , aryScreenshotTime.group(5) , aryScreenshotTime.group(6) )
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