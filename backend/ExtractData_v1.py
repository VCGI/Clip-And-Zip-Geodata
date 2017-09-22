import arcgisscripting
import os
import sys
import traceback
import zipfile
import re

gp = arcgisscripting.create(10.1)

#**********************************************************************
# Description:
#   Extracts requested data to specified file format, coordinate system, and zips folder.
#
# Parameters:
##        layers = list of layers to extract
##        areaOfInterest = area of interest "featureset"
##        inputFeatureFormat = This is actually the output feature format requested
##        inputRasterFormat = This is actually the output feature format requested
##        coordinateSystem = Output coordinate system being requested
##        customCoordSystemFolder = Coordinate system config folder
##        outputZipFile = output zipfile o create
# Constants
##        maxarea_lookuptable = Lookup table containing list of layers/raster
##                              with Maximum Area "to be clipped" settings.
##        default_maxarea = Default max clip area for those not listed in lookup
#
# HISTORY:
#	4/15/2014	- Steve Sharp, VCGI	- Modified original ESRI code to handled Mosiac datasets
#                                   and handle maxarea limits, and other goodies
#   4/22/2014   - Steve Sharp, VCGI - Added geoprocessing log generation
#**********************************************************************

class LicenseError(Exception):
    pass

# Initialize processing log
def InitLog(header,outputlog):
    try:
        logfile = open(outputlog, 'w')
        writestatus = logfile.write(header)
        logfile.close()

    except: gp.AddMessage("ERROR: A problem was encountered while initializing the geoprocessing log!")
    
# Append to processing log
def Append2Log(message,outputlog):
    try:
        message = "<br>" + message + "<br>"
        logfile = open(outputlog, 'a')
        writestatus = logfile.write(message)
        logfile.close()

    except:
        gp.AddMessage(outputlog)
        gp.AddMessage("ERROR: A problem was encountered while writing " + message + " to the geoprocessing log!")


def setUpCoordSystemEnvironment(coordinateSystem, customCoordSystemFolder):
    # get the correct spatial reference and set it into the environment
    # so that the data will get projected when clip runs
    # if it is a number, assume we have a WKID and set it directly in
    # else, find the file in the Coordinate System directory
    if coordinateSystem.lower() == "same as input" or coordinateSystem == "":
        return "same as input"

    if coordinateSystem.strip().isalnum() and customCoordSystemFolder == "":
        try:
            gp.OutputCoordinateSystem = coordinateSystem.strip()
        except:
            #Message "Coordinate System WKID %s is not valid.  Output Coordinate System will be the same as the input layer's Coordinate System"
            gp.AddWarning(get_ID_message(86131) % (coordinateSystem))
            coordinateSystem = "same as input"
            gp.OutputCoordinateSystem = None
            pass
        return coordinateSystem

    found = False
    # Search custom folder if specified
    if customCoordSystemFolder != "":
        found, coordinateSystemPath = getPRJFile(coordinateSystem, customCoordSystemFolder)

    # Search to see if we can find the spatial reference
    if not found:
        srList = gp.ListSpatialReferences("*/%s" % coordinateSystem)
        if srList:
            coordinateSystemPath = os.path.join(os.path.join(gp.getinstallinfo()["InstallDir"], "Coordinate Systems"), srList[0]) + ".prj"
            found = True

    if found:
        gp.OutputCoordinateSystem = coordinateSystemPath
        return coordinateSystemPath
    else:
        #Message "Couldn't find the specified projection file %s.  Output Coordinate System will be the same as the input layer's Coordinate System."
        gp.AddWarning(get_ID_message(86132) % coordinateSystem)
        return "same as input"

def getPRJFile(inputCoordSysString, prjDir):
    inputCoordSysString += ".prj"
    # walk through the dirs to find the prj file
    if os.path.exists(prjDir):
        for x in os.walk(prjDir):
            if inputCoordSysString in x[2]:
                return True, os.path.join(x[0], inputCoordSysString)
    else:
        return False, ""

    # if we got to here then it didn't find the prj file
    return False, ""

def zipUpFolder(folder, outZipFile):
    # zip the data
    try:
        zip = zipfile.ZipFile(outZipFile, 'w', zipfile.ZIP_DEFLATED)
        zipws(str(folder), zip, "CONTENTS_ONLY")
        zip.close()
    except RuntimeError:
        # Delete zip file if exists
        if os.path.exists(outZipFile):
            os.unlink(outZipFile)
        zip = zipfile.ZipFile(outZipFile, 'w', zipfile.ZIP_STORED)
        zipws(str(folder), zip, "CONTENTS_ONLY")
        zip.close()
        #Message"  Unable to compress zip file contents."
        msg = get_ID_message(86133)
        gp.AddWarning(msg)
        Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        
        

def zipws(path, zip, keep):
    path = os.path.normpath(path)
    # os.walk visits every subdirectory, returning a 3-tuple
    #  of directory name, subdirectories in it, and filenames
    #  in it.
    for (dirpath, dirnames, filenames) in os.walk(path):
        # Iterate over every filename
        for file in filenames:
            # Ignore .lock files
            if not file.endswith('.lock'):
                #gp.AddMessage("Adding %s..." % os.path.join(path, dirpath, file))
                try:
                    if keep:
                        zip.write(os.path.join(dirpath, file),
                        os.path.join(os.path.basename(path), os.path.join(dirpath, file)[len(path)+len(os.sep):]))
                    else:
                        zip.write(os.path.join(dirpath, file),
                        os.path.join(dirpath[len(path):], file))

                except Exception as e:
                    #Message "    Error adding %s: %s"
                    msg = get_ID_message(86134) % (file, e[0])
                    gp.AddWarning(msg)
                    Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
    return None

def createFolderInScratch(folderName):
    # create the folders necessary for the job
    folderPath = gp.CreateUniqueName(folderName, gp.scratchworkspace)
    gp.CreateFolder_management(gp.scratchworkspace, os.path.basename(folderPath))
    return folderPath

def getTempLocationPath(folderPath, format):
    # make sure there is a location to write to for gdb and mdb
    if format == "mdb":
        MDBPath = os.path.join(folderPath, "data.mdb")
        if not gp.exists(MDBPath):
            gp.CreatePersonalGDB_management(folderPath, "data")
        return MDBPath
    elif format == "gdb":
        GDBPath = os.path.join(folderPath, "data.gdb")
        if not gp.exists(GDBPath):
            gp.CreateFileGDB_management(folderPath, "data")
        return GDBPath
    else:
        return folderPath

def makeOutputPath(raster, inLayerName, convert, formatList, zipFolderPath, scratchFolderPath):
    outFormat = formatList[1].lower()

    # if we are going to convert to an esri format on the clip, put the output in the zipfolder
    # else put it in the scratch folder in a gdb
    if convert:
        outwkspc = getTempLocationPath(zipFolderPath, outFormat)
    else:
        outwkspc = getTempLocationPath(scratchFolderPath, "gdb")

    if inLayerName.find("\\"):
        inLayerName = inLayerName.split("\\")[-1]

    # make sure there are no spaces in the out raster name and make sure its less than 13 chars
    if outFormat == "grid":
        if len(inLayerName) > 12:
            inLayerName = inLayerName[:12]
        if inLayerName.find(" ") > -1:
            inLayerName = inLayerName.replace(" ", "_")

    # make the output path
    tmpName = os.path.basename(gp.createuniquename(inLayerName, outwkspc))
    tmpName = gp.validatetablename(tmpName, outwkspc)

    # do some extension housekeeping.
    # Raster formats and shp always need to put the extension at the end
    if raster or outFormat == "shp":
        if outFormat != "gdb" and outFormat != "mdb" and outFormat != "grid":
            tmpName = tmpName + formatList[2].lower()

    outputpath = os.path.join(outwkspc, tmpName)

    return tmpName, outputpath

def clipRaster(lyr, aoi, rasterFormat, zipFolderPath, scratchFolderPath, dataType, aoi_area, aoi_ext):
    # get the path and a validated name for the output
    layerName, outputpath = makeOutputPath(True, lyr, True, rasterFormat, zipFolderPath, scratchFolderPath)
    outputFormat = rasterFormat[1].upper()
    msg = "-> Running clip operation on " + lyr + "...."
    Append2Log(msg,TargetLogFile)
    gp.AddMessage(msg)
    # set raster storage environment settings
    gp.pyramid = "NONE"
    gp.rasterStatistics = "NONE"
##    gp.compression = "LZW"
##    gp.AddMessage(" rasterFormat = " + outputFormat)
##    if outputFormat == ".JPEG 2000" or outputFormat == "GDB":
##        gp.compression = "JPEG2000 75"
##    if outputFormat == "IMG":
##        gp.compression = "RLE"
    # do the clip
    try:
        if dataType == "mosaiclayer":
            msg = "-> this is a mosaic layer...."
            gp.AddMessage(msg)
            Append2Log(msg,TargetLogFile)
            msg = "-> running clip_management(" + lyr + "," + str(aoi_ext) + "," + outputpath + ")"
            gp.AddMessage(msg)
            Append2Log(msg,TargetLogFile)
            gp.clip_management(lyr, str(aoi_ext), outputpath)
            #Message "  clipped %s..."
            gp.AddIDMessage("INFORMATIVE", 86135, lyr)
            msg = "-> clipped " + lyr
            Append2Log(msg,TargetLogFile)

        else:
            msg = "-> this is a raster layer...."
            gp.AddMessage(msg)
            Append2Log(msg,TargetLogFile)
            msg = "-> running clip_management(" + lyr + "," + str(aoi_ext) + "," + outputpath + ")"
            gp.AddMessage(msg)
            Append2Log(msg,TargetLogFile)
            gp.clip_management(lyr, str(aoi_ext), outputpath)
            #gp.clip_management(lyr, "0 0 1 1", outputpath, aoi, "#", "ClippingGeometry")
            #Message "  clipped %s..."
            gp.AddIDMessage("INFORMATIVE", 86135, lyr)
            msg = "-> clipped " + lyr
            Append2Log(msg,TargetLogFile)
                
    except:
        errmsg = gp.getmessages(2)
        #Message "  failed to clip layer %s..."
        msg = get_ID_message(86136) % lyr
        gp.AddWarning(msg)
        Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        if errmsg.lower().find("error 000446") > -1:
        #Message"  Output file format with specified pixel type or number of bands or colormap is not supported.\n  Refer to the 'Technical specifications for raster dataset formats' help section in Desktop Help.\n  http://webhelp.esri.com/arcgisdesktop/9.3/index.cfm?TopicName=Technical_specifications_for_raster_dataset_formats"
        #Shorted as "Output file format with specified pixel type or number of bands or colormap is not supported"
            msg = get_ID_message(86137)
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)

        elif errmsg.lower().find("error 000445"):
            msg = gp.GetMessages(2)
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
            #Message "  Extension is invalid for the output raster format.  Please verify that the format you have specified is valid."
            msg = get_ID_message(86138)
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        else:
            msg = gp.GetMessages(2)
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        pass

def clipFeatures(lyr, aoi, featureFormat, zipFolderPath, scratchFolderPath, convertFeaturesDuringClip, aoi_area):
    global haveDataInterop
    try:
        # get the path and a validated name for the output
        layerName, outputpath = makeOutputPath(False, lyr, convertFeaturesDuringClip, featureFormat, zipFolderPath, scratchFolderPath)
        msg = "-> Running clip operation on " + lyr + "...."
        Append2Log(msg,TargetLogFile)
        gp.AddMessage(msg)
        # do the clip
        msg = "-> running clip_analysis(" + lyr + "," + str(aoi) + "," + outputpath + ")"
        gp.AddMessage(msg)
        Append2Log(msg,TargetLogFile)
        gp.clip_analysis(lyr, aoi, outputpath)
        #Message "  clipped %s..."
        msg = "-> Successfully clipped " + lyr
        Append2Log(msg,TargetLogFile)
        gp.AddIDMessage("INFORMATIVE", 86135, lyr)

        # if format needs data interop, convert with data interop
        if not convertFeaturesDuringClip:
            # get path to zip
            outputinzip = os.path.join(zipFolderPath, layerName + featureFormat[2])
            if featureFormat[2].lower() in [".dxf", ".dwg", ".dgn"]:
                #Message "..using export to cad.."
                gp.AddWarning(get_ID_message(86139))
                msg = "-> converting to " + featureFormat[1] + " using export to cad..."
                Append2Log(msg,TargetLogFile)
                gp.ExportCAD_conversion(outputpath, featureFormat[1], outputinzip)
            else:
                if not haveDataInterop:
                    raise LicenseError
                diFormatString = "%s,%s" % (featureFormat[1], outputinzip)
                # run quick export
                msg = "-> converting to " + featureFormat[1] + " using quickexport..."
                Append2Log(msg,TargetLogFile)
                gp.quickexport_interop(outputpath, diFormatString)

    except LicenseError:
        #Message "  failed to export to %s.  The requested formats require the Data Interoperability extension.  This extension is currently unavailable."
        msg = get_ID_message(86140) % featureFormat[1]
        gp.AddWarning(msg)
        Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        pass

    except:
        errorstring = gp.GetMessages(2)
        if errorstring.lower().find("failed to execute (quickexport)") > -1:
            #Message "  failed to export layer %s with Quick Export.  Please verify that the format you have specified is valid."
            msg = get_ID_message(86141) % lyr
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)

        elif errorstring.lower().find("failed to execute (clip)") > -1:
            #Message "  failed to clip layer %s...
            gp.AddWarning(get_ID_message(86142) % lyr)
        else:
            msg = get_ID_message(86142) % lyr
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
            msg = gp.GetMessages(2)
            gp.AddWarning(msg)
            Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
        pass

def clipAndConvert(lyrs, aoi, featureFormat, rasterFormat, coordinateSystem):
    try:
        # for certain output formats we don't need to use Data Interop to do the conversion
        convertFeaturesDuringClip = False
        if featureFormat[1].lower() in ["gdb", "mdb", "shp"]:
            convertFeaturesDuringClip = True

        # get a scratch folder for temp data and a zip folder to hold
        # the final data we want to zip and send
        zipFolderPath = createFolderInScratch("zipfolder")
        scratchFolderPath = createFolderInScratch("scratchfolder")

        # Set TargetLogFile
        global TargetLogFile
        TargetLogFile = os.path.join(zipFolderPath, "_ExtractData_ProcessingLog.html")

        # Init geoprocessing log
        # log Header
        report_header = "<!DOCTYPE HTML PUBLIC '-//W3C//DTD HTML 4.01 Transitional//EN 'http://www.w3.org/TR/html4/loose.dtd'> \
        <html> \
        <head> <meta http-equiv='Content-Type' content='text/html; charset=iso-8859-1'> <title>VCGI Data Extract Processing Log</title> </head> \
        <body> \
        <hr> \
        <font face='Arial, Helvetica, sans-serif' size='-1'><strong><div align='center'> <h3>VCGI Data Extract Processing Log</h3></div></strong> \
        <hr> \
        <p> \
        <div align='center'><a href=\"javascript:if (window.print != null) { window.print(); } else { alert('Unfortunately, your browser does not support this shortcut.  Please select Print from the File menu.'); }\"><strong><h4>Click to Print</h4></strong></a></div>"
        
        # Add version number to the log
        msg = "Version of software: " + version
        report_header = report_header + "<br>" + msg + "<br>"

        # Add start date/time stamp to the log
        datetime_stamp_start = str(time.strftime("%m/%d/%Y %H:%M:%S", time.localtime()))
        msg = "Processing Start Time: " + datetime_stamp_start
        report_header = report_header + "<br>" + msg + "<br>"

        # Initialize the log file
        msg= "<strong><font color='blue'>Initiating VCGI Extract Data geoprocessing routine...</font></strong>"
        report_header = report_header + "<br>" + msg + "<br>"
        InitLog(report_header,TargetLogFile)
        
        # Make feature layer from feature set (aoi) to determine area of aoi for proper handling
        msg = "--> Evaluating AOI extent..."
        gp.AddMessage(msg)
        Append2Log("<strong>" + msg + "</strong>",TargetLogFile)
        Clip_FeatureLayer = "ClipLayer"
        gp.MakeFeatureLayer_management(aoi,Clip_FeatureLayer)
        desc = gp.describe(Clip_FeatureLayer)
        aoi_ext = desc.featureClass.extent
        msg = "-> AOI xmin/ymin/xmax/ymax = " + str(aoi_ext)
        gp.AddMessage(msg)
        Append2Log(msg,TargetLogFile)
##        msg = " calculating area of aoi...."
##        gp.AddMessage(msg)
##        Append2Log(msg,TargetLogFile)
        ext_split = aoi_ext.split()
        xmin = float(ext_split[0])
        ymin = float(ext_split[1])
        xmax = float(ext_split[2])
        ymax = float(ext_split[3])
        width = xmax - xmin
##        msg = " width = " + str(width)
##        gp.AddMessage(msg)
##        Append2Log(msg,TargetLogFile)
        height = ymax - ymin
##        msg = " height = " + str(height)
##        gp.AddMessage(msg)
##        Append2Log(msg,TargetLogFile)
        aoi_area = width * height
        msg = "-> AOI area to extract = " + str(aoi_area) + " sq meters"
        gp.AddMessage(msg)
        Append2Log(msg,TargetLogFile)
        
        # loop through the list of layers recieved
        for lyr in lyrs:
            # temporary stop gap measure to counteract bug  
            if lyr.find(" ") > -1:
                lyr = lyr.replace("'", "")
            describe = gp.describe(lyr)
            dataType = describe.DataType.lower()
            msg = "--> Processing " + lyr
            gp.AddMessage(msg)
            Append2Log("<strong>" + msg + "</strong>",TargetLogFile)
            #
            # - set extract_maxarea to default_maxarea parameter
            extract_maxarea = default_maxarea
            
            # See if the current lyr has a MaxArea defined in the maxarea_lookuptable
            # verify that table exists
            if gp.Exists(maxarea_lookuptable):
                # Determine if there is a slash deliminter ("\"), which happens when this runs on the AGS server, and strip out stuff before the "\" slash
                if lyr.find("\\"):
                    lyrNameOnly = lyr.split("\\")[-1]
                else:
                    lyrNameOnly = lyr
                whereclause = '"LAYERNAME" = ' + "'" + lyrNameOnly + "'"
                msg = "-> Checking maxarea lookup table where " + whereclause
                gp.AddMessage(msg)
                Append2Log(msg,TargetLogFile)
                cur = gp.searchcursor(maxarea_lookuptable,whereclause)
                # Iterate through the rows in the cursor
                #
                for row in cur:
                    extract_maxarea = row.maxarea

                del cur
            else:
                msg = "==> ERROR: " + maxarea_lookuptable + " does not exist!"
                gp.AddMessage(msg)
                Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)

            msg = "-> maximum area to extract for this layer is " + str(extract_maxarea) + " sq meters"
            gp.AddMessage(msg)
            Append2Log(msg,TargetLogFile)
            # make sure we are dealing with features or raster and not some other layer type (group, tin, etc)
            if dataType in ["featurelayer", "rasterlayer", "mosaiclayer"]:
                # if the coordinate system is the same as the input
                # set the environment to the coord sys of the layer being clipped
                # may not be necessary, but is a failsafe.
                if coordinateSystem.lower() == "same as input":
                    sr = describe.spatialreference
                    if sr != None:
                        gp.outputcoordinatesystem = sr

                # raster branch
                if dataType in ["rasterlayer", "mosaiclayer"]:
                    if aoi_area >= extract_maxarea:
                        msg = "==> WARNING: AOI area (" + str(aoi_area) + ") exceeds the maximum area to extract (" + str(extract_maxarea) + " sq meters). " + lyr + " WILL BE SKIPPED!"
                        arcpy.AddWarning(msg)
                        gp.AddWarning(get_ID_message(86136) % lyr)
                        Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
                    else:
                        clipRaster(lyr, aoi, rasterFormat, zipFolderPath, scratchFolderPath, dataType, aoi_area, aoi_ext)

                # feature branch
                else:
                    if aoi_area >= extract_maxarea:
                        msg = "==> WARNING: AOI area (" + str(aoi_area) + ") exceeds the maximum area to extract (" + str(extract_maxarea) + " sq meters). " + lyr + " WILL BE SKIPPED!"
                        arcpy.AddWarning(msg)
                        gp.AddWarning(get_ID_message(86136) % lyr)
                        Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
                    else:
                        clipFeatures(lyr, aoi, featureFormat, zipFolderPath, scratchFolderPath, convertFeaturesDuringClip, aoi_area)
            else:
                #Message "  Cannot clip layer: %s.  This tool does not clip layers of type: %s..."
                msg = get_ID_message(86143) % (lyr, dataType)
                gp.AddWarning(msg)
                Append2Log("<strong><font color='red'>" + msg + "</font></strong>",TargetLogFile)
                
        return zipFolderPath

    except:
        errstring = get_ID_message(86144)#"Failure in clipAndConvert..\n"
        tb = sys.exc_info()[2]
        tbinfo = traceback.format_tb(tb)[0]
        pymsg = "ERRORS:\nTraceback Info:\n" + tbinfo + "\nError Info:\n    " + \
                str(sys.exc_type)+ ": " + str(sys.exc_value) + "\n"
        errstring += pymsg
        Append2Log("<strong><font color='red'>" + errstring + "</font></strong>",TargetLogFile)
        raise Exception, errstring

def get_ID_message(ID):
    return re.sub("%1|%2", "%s", gp.GetIDMessage(ID))

if __name__ == '__main__':
    try:
        # Get the Parameters
        layers = gp.getparameterastext(0).split(";")
        areaOfInterest = gp.getparameter(1)
        inputFeatureFormat = gp.getparameterastext(2)
        inputRasterFormat = gp.getparameterastext(3)
        coordinateSystem = gp.getparameterastext(4)
        customCoordSystemFolder = gp.getparameterastext(5)
        outputZipFile = gp.getparameterastext(6).replace("\\",os.sep)
        
        # Set constants
        # Version number of this program
        version = "1a"
        global TargetLogFile
        TargetLogFile = "none"
        
        # Max Area lookup table
        maxarea_lookuptable = r"\\ags\AGS_services\VCGI_services\ClipAndShip\ToolData\SDE.VCGI.ORG-GDB_VCGI_Web-as-VCGI_AGS_user.sde\GDB_VCGI_Web.VCGI_ADMIN.DWARE_Datasets_MaxSize"
        # Default max area to extract (state of VT and beyond)
        default_maxarea = 400000000000
        
        if gp.CheckExtension("DataInteroperability") == "Available":
            gp.CheckOutExtension("DataInteroperability")
            haveDataInterop = True
        else:
            haveDataInterop = False
        # Do a little internal validation.
        # Expecting "long name - short name - extension
        # If no format is specified, send features to GDB.
        if inputFeatureFormat == "":
            featureFormat = ["File Geodatabase", "GDB", ".gdb"]
        else:
            #featureFormat = inputFeatureFormat.split(" - ")
            featureFormat = map(lambda x: x.strip(), inputFeatureFormat.split("-"))
            if len(featureFormat) < 3:
                featureFormat.append("")

        # If no format is specified, send rasters to GRID.
        # Expecting "long name - short name - extension
        if inputRasterFormat == "":
            rasterFormat = ["ESRI GRID", "GRID", ""]
        else:
            #rasterFormat = inputRasterFormat.split(" - ")
            rasterFormat = map(lambda x: x.strip(), inputRasterFormat.split("-"))
            if len(rasterFormat) < 3:
                rasterFormat.append("")

        coordinateSystem = setUpCoordSystemEnvironment(coordinateSystem, customCoordSystemFolder)

        # Do this so the tool works even when the scratch isn't set or if it is set to gdb/mdb/sde
        if gp.scratchworkspace is None or os.path.exists(str(gp.scratchworkspace)) is False:
            gp.scratchworkspace = gp.getsystemenvironment("TEMP")
        else:
            swd = gp.describe(gp.scratchworkspace)
            wsid = swd.workspacefactoryprogid
            if wsid == 'esriDataSourcesGDB.FileGDBWorkspaceFactory.1' or\
               wsid == 'esriDataSourcesGDB.AccessWorkspaceFactory.1' or\
               wsid == 'esriDataSourcesGDB.SdeWorkspaceFactory.1':
                gp.scratchworkspace = gp.getsystemenvironment("TEMP")

        # clip and convert the layers and get the path to the folder we want to zip
        zipFolder = clipAndConvert(layers, areaOfInterest, featureFormat, rasterFormat, coordinateSystem)

        # zip the folder
        zipUpFolder(zipFolder, outputZipFile)

        # Processing complete notice
        msg = "Data extract processing complete!"
        gp.AddMessage(msg)
        Append2Log("<strong><font color='blue'>" + msg + "</font></strong>",TargetLogFile)
        
        # Add end date/time stamp to the log
        datetime_stamp_end = str(time.strftime("%m/%d/%Y %H:%M:%S", time.localtime()))
        msg = "Processing End Time: " + datetime_stamp_end
        Append2Log(msg,TargetLogFile)

    except:
        tb = sys.exc_info()[2]
        tbinfo = traceback.format_tb(tb)[0]
        pymsg = "ERRORS:\nTraceback Info:\n" + tbinfo + "\nError Info:\n    " + \
                str(sys.exc_type)+ ": " + str(sys.exc_value) + "\n"
        gp.AddError(pymsg)

