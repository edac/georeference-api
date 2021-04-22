#!/usr/bin/python

import requests
import json
import os
import xlrd

url = "https://api.historicalaerialphotos.org/api/georeference"
output_folder="output"

if not os.path.isdir(output_folder):
  os.mkdir(output_folder) 

# Open the workbook
wb = xlrd.open_workbook('./gcps.xls')
sheet = wb.sheet_by_index(0)
 
#For each row in the workbook
for row in range(sheet.nrows):
      #Get the filename and gcps
      filename=sheet.cell_value(row, 0)
      gcps=sheet.cell_value(row, 1)
      #Build an array of tuples to upload the image and gcps at the same time.
      files = [
      ('document', open(filename,'rb')),
      ('api_gcps', ('api_gcps', gcps, 'application/json'))
      ]
      #Upload the image and gcps.
      response = requests.request("POST", url, files = files , stream=True)
      if response.status_code == 200: #If the API returns 200
        #Then save the response to a file.
        with open(output_folder+"/"+filename+".tif", 'wb') as fd:
          for chunk in response.iter_content(chunk_size=128):
            fd.write(chunk)

