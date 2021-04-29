from flask import Flask, request, g, redirect, url_for, render_template, flash, send_file,Markup, Response, current_app, send_from_directory
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_
from sqlalchemy import func
import os
import json 
from PIL import Image
import gdal
from osgeo import ogr
from osgeo import osr
import uuid 
import math

################################
###########FUNCTIONS############
################################


#allowed_extensions.
#NOTE: This could be extended to any readable PIL format such as 'ppm' or 'wal'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tiff', 'bmp', 'gif', 'tif'}

#Used in upload handeler.
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


#Currently unused, but can be used for metadata creation.
def getbboxfromimage(imagepath):
    ds = gdal.Open(imagepath)
    upx, xres, xskew, upy, yskew, yres = ds.GetGeoTransform()
    cols = ds.RasterXSize
    rows = ds.RasterYSize
    ulx = upx + 0*xres + 0*xskew
    uly = upy + 0*yskew + 0*yres
    llx = upx + 0*xres + rows*xskew
    lly = upy + 0*yskew + rows*yres
    lrx = upx + cols*xres + rows*xskew
    lry = upy + cols*yskew + rows*yres
    urx = upx + cols*xres + 0*xskew
    ury = upy + cols*yskew + 0*yres
    if ulx<llx:
        wbc=ulx
    else:
        wbc=llx
    if urx>lrx:
        ebc=urx
    else:
        ebc=lrx
    if ury>uly:
        nbc=ury
    else:
        nbc=uly
    if lry<lly:
        sbc=lry
    else:
        sbc=lly

    return [wbc, ebc, sbc, nbc]






#create app
app = Flask(__name__)

# load dev config
app.config.from_object('config.DevConfig')

# Load config secret
app.secret_key = app.config['SECRETKEY']

# Set up Database
db = SQLAlchemy(app)


#  Create Jobs class for the jobs table.
class Jobs(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    json = db.Column(db.String(6553))
    uuid = db.Column(db.String(36))
    status = db.Column(db.String(20))
    imagename = db.Column(db.String(150))
    original_imagename = db.Column(db.String(150))
    height=db.Column(db.Integer)
    width=db.Column(db.Integer)
    gcps=db.Column(db.String(10000))


# create the jobs table if it does not exist.
db.create_all()


############################
##########Handelers#########
############################

#basic index for root.
def index():
        return render_template('index.html')

# Render contribute template
def upload():
    if request.method == 'POST':


        file = request.files['file']
 
        # check if the post request has the file part
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)

        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            data = dict(request.files)
            filename = secure_filename(file.filename)
            this_uuid=str(uuid.uuid4())
            folder=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],this_uuid)
            os.mkdir(folder)
            fpath=os.path.join(folder, filename)
            file.save(fpath)
            raw_image=this_uuid+"_raw.tiff"
            tiff_path=os.path.join(folder, raw_image)
            img = Image.open(fpath)
            img.save(tiff_path)
            im = Image.open(tiff_path) # open image 
            os.remove(fpath)
            width, height = im.size #set image dimensions
            fgdc_json=json.loads('{}')
            fgdc_json['abstract']='public'
            fgdc_json['purpose']='public'
            fgdc_json['keywords']='public'
            fgdc_json_string = json.dumps(fgdc_json)
            newjob=Jobs(json=fgdc_json_string,uuid=this_uuid,status="STAGED",imagename=raw_image,original_imagename=filename,width=width,height=height,gcps='{}')
            db.session.add(newjob) # issue insert for new job
            print(newjob)
            wms=request.url_root+'api/ogc/'+this_uuid+'/wms'
            try:
                db.session.commit()
                return ({"status":True,"message":None,"uuid":this_uuid,"width":width, "height":height,"wms":wms,"layer":"RAW"})
            except Exception as e:
                return ({"status":"error","message":e,"uuid":"None"})





def rmseGen(uuid):
    # get the current users job
    job=db.session.query(Jobs).filter(Jobs.uuid==uuid).first()
    if job: # user has a job
        gcpjson = request.get_json() # get gcps
        # update job in database with gcps
        res = db.session.query(Jobs).filter(Jobs.id==job.id).update(dict(gcps=gcpjson))
        # commit changes 
        db.session.commit()
        if gcpjson is not None: # request returned a gcp json
            if len(gcpjson.keys())>=3:
                gcpList = []
                for gcp in gcpjson:
                    # create GCP's and add to gcplist 
                    gcpList.append(gdal.GCP(gcpjson[gcp]['lat'],gcpjson[gcp]['lon'],0,gcpjson[gcp]['col'],gcpjson[gcp]['row']))
                # open job dataset
                folder=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],job.uuid)
                raw_file=os.path.join(folder,job.imagename) 
                ds = gdal.Open(raw_file)
                # set path and file for translation 
                translate_image=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],job.uuid,job.uuid+"_trans.tif")
                if os.path.exists(translate_image): # path exists 
                    # remove file path to translation
                    os.remove(translate_image)
                # translate dataset given spatial reference and new GCP's
                dsx = gdal.Translate(translate_image, ds,  GCPs = gcpList)
                rmsevals=[]

                # declare spatial references for transformations
                InSR = osr.SpatialReference()
                InSR.ImportFromEPSG(4326)       # WGS84/Geographic
                OutSR = osr.SpatialReference()
                OutSR.ImportFromEPSG(26913) 

                # reopen translated image
                translated_ds = gdal.Open(translate_image)
                tr = gdal.Transformer( translated_ds, None, [] ) 
                GT = translated_ds.GetGeoTransform()
                wktPoints="MULTIPOINT ("
                errorsum = 0
                errorcount = 0
                for gcp in gcpjson:
                    col=gcpjson[gcp]['col']
                    row=gcpjson[gcp]['row']
                    xp = GT[0] + col*GT[1] + row*GT[2]
                    yp = GT[3] + col*GT[4] + row*GT[5]
                    success,pnt = tr.TransformPoint(0, col,row ) 
                    # create point, for predicted point
                    predictedPoint = ogr.Geometry(ogr.wkbPoint)
                    predictedPoint.AddPoint(gcpjson[gcp]['lat'],gcpjson[gcp]['lon'])
                    predictedPoint.AssignSpatialReference(InSR) # assign srs epsg_4326
                    predictedPoint.TransformTo(OutSR) # transform to epsg_26913
                    # create point, for observed point
                    observedPoint = ogr.Geometry(ogr.wkbPoint)
                    observedPoint.AddPoint(pnt[0],pnt[1])
                    observedPoint.AssignSpatialReference(InSR) # assign srs epsg_4326
                    observedPoint.TransformTo(OutSR)  # transform to epsg_26913
                    x1=predictedPoint.GetX()
                    y1=predictedPoint.GetY()
                    x2=observedPoint.GetX()
                    y2=observedPoint.GetY()
                    a = x1 - x2
                    b = y1 - y2
                    c = math.sqrt(a * a + b * b)
                    errorsum = errorsum + c
                    errorcount = errorcount + 1
                    gcpobj={gcp:{"predicted":{"lat":predictedPoint.GetX(),"lon":predictedPoint.GetY()},"observed":{"lat":observedPoint.GetX(),"lon":observedPoint.GetY()}}}
                    wktPoints=wktPoints+"("+str(predictedPoint.GetX())+" "+str(predictedPoint.GetY())+"),("+str(observedPoint.GetX())+" "+str(observedPoint.GetY())+"),"
                    rmsevals.append(gcpobj)

                errormean = errorsum / errorcount
                finalrmse = math.sqrt(errormean)

                returnjson=json.loads('{"status":"success"}')
                returnjson['id']=job.id
                returnjson['rmsevals']=rmsevals
                returnjson['rmse']=finalrmse
            else:
                returnjson=json.loads('{"status":"success"}')
                returnjson['id']=job.id
                returnjson['rmsevals']={}

            return returnjson



def georeferencer(id):
    # get the users job
    job=db.session.query(Jobs).filter(Jobs.uuid==id).first()
   
    if job: #user has a georef job
        final_image=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],job.uuid,job.original_imagename+".tif")
        translate_image=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],job.uuid,job.uuid+"_trans.tif")
        ds2 = gdal.Warp(final_image,translate_image, dstAlpha=True,dstSRS="EPSG:4326")
        returnjson=json.loads('{"status":"success"}')
        return returnjson
    else:
        return "No job to geo-reference"





def oneStepGeoreference():
    if request.method == 'POST':
            file = request.files['document']
            data = dict(request.files)
            filename = secure_filename(file.filename)
            fpath=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],"onestep", filename)
            file.save(fpath)            
            # outpaths
            translate_image=os.path.join(os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],"onestep",  "temp"+file.filename))
            warped_image=os.path.join(os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],"onestep",  "final.tif"))
            gcpList=[]
            #We sould allow for multidimensional arrays ('gcps') as well as API based GCPs ('api_gcps') 
            if 'gcps' in data:
                posted_data = json.load(request.files['gcps'])
                for gcp in posted_data['gcps']:
                    #Normalize GCPs and append them to gcpList as gdal.GCP
                    if gcp[2]<0:
                        row=gcp[2]*-1
                    else:
                        row=gcp[2]
                    if gcp[3]<0:
                        col=gcp[3]*-1
                    else:
                        col=gcp[3]                
                    gcpList.append(gdal.GCP(gcp[0],gcp[1],0,row,col))
            
            elif 'api_gcps' in data:
                #Append GCPs to gcpList as gdal.GCP
                posted_data = json.load(request.files['api_gcps'])
                for gcp in posted_data:
                    gcpList.append(gdal.GCP(posted_data[gcp]['lat'],posted_data[gcp]['lon'],0,posted_data[gcp]['col'],posted_data[gcp]['row']))
                
            else:
                return"No GCPs found."
            #Translate using GCPs
            ds = gdal.Open(fpath)
            #Translate the image by adding the translation coefficients to the image headers. 
            dsx = gdal.Translate(translate_image, ds, outputSRS = 'EPSG:4326', GCPs = gcpList)

            #Use the coefficients to warp pixel locations.
            ds2 = gdal.Warp(warped_image,translate_image, dstAlpha=True,dstSRS="EPSG:4326")
            #cleanup
            del ds
            del dsx
            del ds2
            #Return the warped image.
            response = send_file(warped_image,as_attachment=True, attachment_filename='server.tif')
            return response
        


def download(uid):
    jobs=db.session.query(Jobs).filter(Jobs.uuid==uid).first()
    if jobs:
        folder=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],jobs.uuid)
        final_file=os.path.join(folder,jobs.original_imagename+".tif")
        return send_from_directory(directory=folder, filename=jobs.original_imagename+".tif" , as_attachment=True) 
    return"JOB NOT FOUND"

def ogc(uid,servicetype):
    wms_public='/tmp/'+uid+'wms_pub.png'
    path=""
    layers=[]
    jobs=db.session.query(Jobs).filter(Jobs.uuid==uid).first()
    if jobs:
        folder=os.path.join(app.config['PUBLIC_UPLOAD_FOLDER'],jobs.uuid)
        raw_file=os.path.join(folder,jobs.imagename) 
        final_file=os.path.join(folder,jobs.original_imagename+".tif")
        if os.path.isfile(raw_file):
            layers.append({'layerhome':folder,'filename':jobs.imagename,'layername':"RAW"})
        if os.path.isfile(final_file):
            layers.append({'layerhome':folder,'filename':jobs.original_imagename+".tif",'layername':"PREVIEW"})

    if servicetype=='wms': # if service type is web maps service
            if request.args.get('REQUEST')=='GetCapabilities':
                json={"layers":[]}
                for layer in layers:
                    innerjson={}
                    # get path to file
                    path=os.path.join(layer["layerhome"],layer["filename"])
                    print(path)
                    #open the file
                    ds = gdal.Open(path)
                    # get the dimesions of the dataset
                    width = ds.RasterXSize
                    height = ds.RasterYSize
                    # set the coordinates for the bounding box
                    gt = ds.GetGeoTransform()
                    innerjson['wbc'] = gt[0]
                    innerjson['sbc'] = gt[3] + width*gt[4] + height*gt[5]
                    innerjson['ebc'] = gt[0] + width*gt[1] + height*gt[2]
                    innerjson['nbc'] = gt[3]
                    # set layer name 
                    innerjson['name']=layer["layername"]
                    # nest innerjson in json
                    json["layers"].append(innerjson)
                json['url']=request.base_url
                xml=render_template('GetCapabilities.xml',json=json)
                return Response(xml, mimetype='text/xml')
                
            elif request.args.get('REQUEST')=='GetMap':
                # get parameters
                version=request.args.get('VERSION')
                req=request.args.get('REQUEST')
                frmt=request.args.get('FORMAT')
                tranparent=request.args.get('TRANSPARENT')
                wmslayers=request.args.get('LAYERS')
                crs=request.args.get('CRS')
                styles=request.args.get('STYLES')
                width=request.args.get('WIDTH')
                height=request.args.get('HEIGHT')
                bbox=request.args.get('BBOX')
                for layer in layers:
                    if layer["layername"]==wmslayers:
                        # get path to file 
                        path=os.path.join(layer["layerhome"],layer["filename"])
                        # open the file 
                        ds = gdal.Open(path)
                        # split bounding box coordinates into array
                        bboxarray=bbox.split(",")
                        if os.path.exists(wms_public):
                            # remove temporary file to make space for rewrites
                            os.remove(wms_public)
                        if wmslayers=="RAW":
                            if os.path.exists(wms_public):
                                # remove temporary file to make space for rewrites
                                os.remove(wms_public)
                            # set coordinates for projection window
                            projWin=[float(bboxarray[0]), -1*float(bboxarray[3]), float(bboxarray[2]), -1*float(bboxarray[1])]
                            # translate and write image to temporary file
                            gdalout=gdal.Translate(wms_public, ds, format="PNG", width=int(width), height=int(height), resampleAlg="average", projWin = projWin)
                            return send_file(wms_public)
                        if wmslayers=="PREVIEW":
                            if os.path.exists('/tmp/'+uid+'wms2.png'):
                                # remove temporary file to make space for rewrites
                                os.remove('/tmp/'+uid+'wms2.png')
                            # set projection window bounds
                            projwin=[float(bboxarray[1]), float(bboxarray[2]), float(bboxarray[3]), float(bboxarray[0])]
                            
                            gdalout=gdal.Translate('/tmp/wms2.png', ds, format="PNG", width=int(width), height=int(height), resampleAlg="average", projWin = projwin)
                            return send_file('/tmp/wms2.png')
                        else:
                            if os.path.exists('/tmp/'+uid+'wmsother.png'):
                                # remove temporary file to make space for rewrites
                                os.remove('/tmp/'+uid+'wmsother.png')
                            # translate and write image to temporary file
                            gdalout=gdal.Translate('/tmp/'+uid+'wmsother.png', ds, format="PNG", width=int(width), height=int(height), resampleAlg="average", projWin = [float(bboxarray[1]), float(bboxarray[2]), float(bboxarray[3]), float(bboxarray[0])])
                            return send_file('/tmp/'+uid+'wmsother.png')
                        return send_file(wms_public)
                    if "REFERENCE"==wmslayers:
                        reference=db.session.query(Projects.reference_file).filter(Jobs.projectid==Projects.id).filter(Jobs.id==id).first()
                        ref_path = os.path.join(app.config["PROJECT_REF"],reference[0])
                        # open the file 
                        ds = gdal.Open(ref_path)
                        # split bounding box coordinates into array
                        bboxarray=bbox.split(",")
                        if os.path.exists('/tmp/'+uid+'wmsother.png'):
                                # remove temporary file to make space for rewrites
                            os.remove('/tmp/'+uid+'wmsother.png')
                            # translate and write image to temporary file
                        gdalout=gdal.Translate('/tmp/'+uid+'wmsother.png', ds, format="PNG", width=int(width), height=int(height), resampleAlg="average", projWin = [float(bboxarray[1]), float(bboxarray[2]), float(bboxarray[3]), float(bboxarray[0])])
                        return send_file('/tmp/'+uid+'wmsother.png')
    else:
        return "ONLY WMS IS SUPPORTED."



########################################
#################ROUTES#################
########################################


#Set up routes
app.add_url_rule('/', 'index', index)

# Interactive Routes
#Step 1
app.add_url_rule('/api/upload', 'upload', upload, methods=['POST'])
app.add_url_rule('/api/ogc/<uid>/<servicetype>', 'ogc', ogc)
#step 2
app.add_url_rule('/api/rmse/<uuid>','rmseGen',rmseGen, methods=['POST'])
app.add_url_rule('/api/georeference/<id>','georeferencer',georeferencer, methods=['GET', 'PUT' ,'POST'])
#Step 3
app.add_url_rule('/api/download/<uid>', 'download', download)

#One Step Route
app.add_url_rule('/api/georeference', 'oneStepGeoreference', oneStepGeoreference, methods=['GET','POST'])




#Start the app
if __name__ == '__main__':
    app.run(use_reloader=False, debug=True)

