
##########rename to config.py#################
class DevConfig(object):
    DEBUG = True
    DEVELOPMENT = True
    SQLALCHEMY_DATABASE_URI= 'mysql://<mysql_user>:<mysql_password>@localhost:3306/<mysql_db_name>'
    SECRETKEY='Your_Secret_Key_Goes_Here'
    PUBLIC_UPLOAD_FOLDER = "/data/scratch/publicupload/"  #Set this to public storage location.
