import os
import sys
import logging
import re
import datetime
from pathlib import Path



#Our goal is to rename file to the format of YYMMDD_HHMMSS-Description
#My-Description-20120131_112233-My-Description
g_reAlreadyRenamed = re.compile( r"^(.*?-)?([0-9]{8})_[0-9]{6}(-.*)?$" )



def GetDateByFileName( aFilePath ) :
    aryResult = g_reAlreadyRenamed.match(aFilePath.stem)
    if aryResult == None:
        raise ValueError( "File {} is not renamed to the format of My-Description-20120131_112233-My-Description yet".format(aFilePath) )
    else:
        #logging.info( "Path {} has date {}".format(aFilePath.stem, aryResult.group( 2 )) )
        return int( aryResult.group( 2 ) )



if __name__ == "__main__" :
    scriptPath = Path( __file__ )
    scriptDir = scriptPath.parent
    
    logDir = Path("{}/Logs".format( scriptDir ))
    logDir.mkdir( parents=True , exist_ok=True )

    currDir = Path( os.path.abspath(os.getcwd()) )
    if ( 2 <= len(sys.argv) ) :
        currDir = Path( os.path.abspath(sys.argv[1]) )
    if currDir.is_dir() == False:
        print( "Usage: {} <directory path>".format( scriptPath.stem ) )
        raise ValueError( "Current path is not directory, currDir={}".format(currDir) )

    logger = logging.getLogger()
    logger.setLevel( logging.INFO )

    fmtConsole = logging.Formatter( "[%(asctime)s][%(levelname)s]: %(message)s" )
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter( fmtConsole )
    logger.addHandler( consoleHandler )

    fmtFile = logging.Formatter( "[%(asctime)s][%(levelname)s][%(process)04X:%(thread)04X][%(filename)s][%(funcName)s_%(lineno)d]: %(message)s" )
    fileHandler = logging.FileHandler( "{}\\{}-{}.txt".format(logDir , currDir.name , datetime.datetime.now().strftime("%Y%m%d_%H%M%S")) )
    fileHandler.setFormatter( fmtFile )
    logger.addHandler( fileHandler )



    filters = {"bmp", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".mov", ".m4a", ".avi", ".amr", ".aac", ".flac"}
    try :
        logging.info( "Search {} under \"{}\"".format(filters,currDir) )
        lsPaths = [path for path in currDir.glob("**/*") if path.suffix in filters]
        lsPaths.sort(key=lambda x: GetDateByFileName(x))

        lastDate = None
        for path in lsPaths:
            currDate = GetDateByFileName(path)
            if currDate != lastDate:
                currDateDir = Path( "{}/{}".format(currDir , currDate) )
                logging.info( "Creating folder {} for file {}".format(currDateDir, path.name) )
                currDateDir.mkdir( parents=True , exist_ok=True )
                lastDate = currDate
            logging.info( "Moving file {} to folder {}".format(path.name, currDateDir) )
            path.rename( "{}/{}".format(currDateDir, path.name) )
    except Exception as ex :
        logging.exception( "currDir={}".format(currDir) )

    logging.info( "End of the program" )
    logging.shutdown()
    print( "Press any key to leave" )
    input()