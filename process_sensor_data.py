# process_sensor_data.py
# process direct calls to esdr as well as aggregates
# returns json as string, doesn't output
# only works for SO2 and PM025 rn(no other scale)

import requests
import time
import json
import datetime
import sys

DEBUG = False

# functions

def dbprint(s):
	if DEBUG:
		print(s)

# get input from cmdline
def get_input():
    if(len(sys.argv) == 4):
        return sys.argv[1:4]
    else:
        print("usage: python3 process_ramps.py <start_date> <end_date> <channel>")
        print("ex: python3 process_ramps.py 2019-05-01 2019-05-31 PM025")
        print("output: smell_reports_<channel>-sensors_<start_date>_<end_date>.geojson")
        exit()

# get json data from url
def request_url(url):
	#get data and coordinates
	dbprint("requesting " + url)
	resp = requests.get(url)
	if resp.status_code != 200:
		# raise ApiError('GET ' + url + ' {}'.format(resp.status_code))
		print("error {}".format(resp.status_code))
		return None
	return resp.json();

# convert sensor val to smell val
def get_smell_value(sensor_value, channel="PM025"):
	if (channel == "PM025"):
		scale = [12, 35.4, 55.4, 150.4]
	elif (channel == "VOC"):
		scale = [400, 600, 800, 1000]
	elif (channel == "SO2"):
		scale = [5,15,25,50]
	else:
		dbprint("no scale for " + channel)
		exit()

	if (sensor_value==None):
		# print("no sensor value")
		return 0
	elif sensor_value < 0:
		# print("bad sensor val="+str(sensor_value))
		return 0
	elif (sensor_value >= 0 and sensor_value <= scale[0]):
		return 1
	elif (sensor_value > scale[0] and sensor_value <= scale[1]):
		return 2
	elif (sensor_value > scale[1] and sensor_value <= scale[2]):
		return 3
	elif (sensor_value > scale[2] and sensor_value <= scale[3]):
		return 4
	else:
		return 5

# make geojson feature
def make_feature(lat, lon, e0, e1, smell_val):
	feature = {"type":"Feature",
		"geometry" : {"type" : "Point", "coordinates" : [lon,lat]}, 
		"properties": {
			"PackedColor":1000,
			"Size" : 15,
	        "StartEpochTime": e0,
	        "EndEpochTime": e1,
	        "GlyphIndex": smell_val,
	        "SmellValue": smell_val
	        }
		}
	return feature

# given start and end date strings, returns list of strings for all dates in btwn (inclusive)
def get_date_range(start_date, end_date):
	start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
	end = datetime.datetime.strptime(end_date, "%Y-%m-%d")
	date_generated = [start + datetime.timedelta(days=x) for x in range(0, (end-start).days)]
	return [date.strftime("%Y-%m-%d") for date in date_generated]

# convert date (string) fromm YY-mm-dd EST to epoch time
def dt_to_epoch(date):
	return int(datetime.datetime.strptime(date, "%Y-%m-%d").timestamp())

# epoch time to local time
def epoch_to_est(epoch):
	return datetime.datetime.fromtimestamp(epoch).strftime('%m/%d/%Y %H:%M:%S')
	# return datetime.datetime.utcfromtimestamp(epoch).strftime('%m/%d/%Y %H:%M:%S')

# for one day, generate array of "features" for geojson
# given sensor_data json, start_epoch for day, channel
def process_day(sensor_data, start_epoch, channel):
	features = []
	sums = [0,0,0,0,0,0]

	# each row 98 elem long, 96 sensor vals
	for sensor in sensor_data:
		lat = sensor[0]
		lon = sensor[1]
		for i in range(0,len(sensor)-2):
			val = sensor[2+i]
			e0 = start_epoch + i*900
			e1 = e0 + 900

			smell_val = get_smell_value(val, channel)
			sums[smell_val] += 1

			feature = make_feature(lat,lon, e0, e1, smell_val)
			features.append(feature)

	dbprint("smell val sums = " + str(sums))
	return features

# process pm25 channels from direct esdr calls
def process_pm25_achd(start_date, end_date):
	# get feed id from api request
	def get_id(api_request):
		start = api_request.find("/feeds") + len("/feeds/")
		end = api_request.find("/channels")
		return api_request[start:end]

	# get latitude and longitude by making new api request
	# for pm25 achd
	def get_latlong(api_request):
		# that one sensor
		if (get_id(api_request) == "11067"):
			x = api_request.find("/feeds") + len("/feeds/")
			new_request = api_request[0:x] + "43"
		else:
			new_request = api_request[0:api_request.find("/channels")]
		resp = requests.get(new_request)
		if resp.status_code != 200:
			print("error")
			raise ApiError('error {}'.format(resp.status_code))
		return [resp.json()['data']['latitude'], resp.json()['data']['longitude']]

	# find indexes of channels w keyword
	def find_indexes(keyword, channels):
		ret = []
		for i in range(0,len(channels)):
			if channels[i].lower().find(keyword) >= 0:
				ret += [i+1]
		return ret

	# takes column indexes to merge and orig data, returns merged data w 2 columns
	# know there's > 1 column
	def merge_data(cols, data):
		def merge_cols(row):
			#remove None
			for i in range(0,len(row)):
				if row[i] == None:
					row[i] = -1

			max_val = max([row[c] for c in cols])
			return [row[0], max_val]

		num_cols = len(cols)
		merged = list(map(merge_cols, data))
		return merged

	def process_request(api_request):
		resp_json = request_url(api_request)
		channel_names = resp_json['channel_names']
		data = resp_json['data']

		dbprint("requesting lat long")
		coord = get_latlong(api_request)
		if (coord[0] == None or coord[1] == None):
			dbprint("bad coords")
			return []

		#filter out channels we don't want
		if (len(channel_names) > 1):
			indexes = find_indexes('pm25',channel_names)
			if len(indexes) == 0 :
				dbprint("no pm25 channels")
				return []
			else:
				data = merge_data(indexes,data)

		# create geojson features
		# maybe change to 15 minutes?
		features = []
		sums = [0,0,0,0,0,0]
		for i in range(0,len(data)):
			if (i == len(data)-1):
				end_epoch = data[i][0] + 3600 #1 hr
			else:
				end_epoch = data[i+1][0]

			smell_val = get_smell_value(data[i][1])
			feature = make_feature(coord[0], coord[1], data[i][0], end_epoch, smell_val)
			features.append(feature)
			sums[smell_val] += 1

		dbprint("smell val sums = " + str(sums))
		return features

	api_requests = ['https://esdr.cmucreatelab.org/api/v1/feeds/29/channels/PM25_UG_M3,PM25T_UG_M3/export?format=json',
	'https://esdr.cmucreatelab.org/api/v1/feeds/26/channels/SONICWS_MPH,SONICWD_DEG,PM25B_UG_M3/export?format=json',
	'https://esdr.cmucreatelab.org/api/v1/feeds/11067/channels/SONICWS_MPH,SONICWD_DEG,PM25T_UG_M3/export?format=json',
	'https://esdr.cmucreatelab.org/api/v1/feeds/1/channels/SONICWS_MPH,SONICWD_DEG,PM25B_UG_M3,PM25T_UG_M3/export?format=json',
	'https://esdr.cmucreatelab.org/api/v1/feeds/30/channels/PM25_UG_M3/export?format=json'
	]

	e0 = dt_to_epoch(start_date)
	e1 = dt_to_epoch(end_date) + 86400 #get data from last day

	# change time range
	for i in range(0,len(api_requests)):
		r = api_requests[i]
		api_requests[i] = r + '&from=' + str(e0) + '&to=' + str(e1)

	# loop through all sensors
	all_sensor_feats = []
	for r in api_requests:
		sensor_feats = process_request(r)
		if (len(sensor_feats) > 0):
			all_sensor_feats += sensor_feats

	return all_sensor_feats
	
def process_and_output(start_date, end_date, channel):
	date_range = get_date_range(start_date, end_date)

	ROOT = "https://data2.createlab.org/esdr-aggregates/"

	# for each day, get feature array, append to total 
	all_features = []
	for date in date_range:
		url = ROOT + channel + "_" + date + ".json"
		sensor_data = request_url(url)
		if (sensor_data == None):
			print ("request failed or bad data from request")
			continue
		day_features = process_day(sensor_data, dt_to_epoch(date), channel)
		all_features += day_features

	if channel == "PM025":
		dbprint("getting pm25 achd")
		all_features += process_pm25_achd(start_date, end_date)

	geojson_out = {"type":"FeatureCollection", "features": all_features}

	# filename = channel + "-sensors_" + start_date + "_" + end_date + "_all.geojson"
	# print("writing to " + filename)
	# with open(filename, 'w') as outfile:
	# 	json.dump(geojson_out,outfile)

	return str(geojson_out)

# main function
def main():
	# parameters
	# start_date = "2019-05-01"
	# end_date = "2019-05-31"
	# channel = "PM025"

	inputs = get_input()
	start_date = inputs[0]
	end_date = inputs[1]
	channel = inputs[2]

	print(process_and_output(start_date, end_date, channel))

# main()
# print("done")