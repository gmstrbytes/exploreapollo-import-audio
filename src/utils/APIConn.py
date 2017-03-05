import requests
import os.path
import subprocess
import sys

MISSION_API         = 'api/missions'
PEOPLE_API          = 'api/people'
TRANSCRIPT_ITEM_API = 'api/transcript_items'
AUDIO_SEGMENT_API   = 'api/audio_segments'
MEDIA_API           = 'api/media'

#### Exceptions ####
class APIFatalException(Exception):
	def __init__(self,reason):
		self.reason = reason
	
	def __str__(self):
		return str(self.reason)

class APIWarningException(Exception):
	def __init__(self,reason):
		self.reason = reason
	
	def __str__(self):
		return str(self.reason)



def _raiseUploadException(response,location):
	'''raise the appropriate exception for an error code'''
	if response.ok:
		return
	if response.status_code == 401:
		raise APIFatalException(
			"%s - 401 Authorization failed, check API server token" % \
			location)
	else:
		raise APIWarningException("%s - %d %s" % (location,
			response.status_code,response.text)) 


def _constructURL(server,path):
	'''construct a complete URL from a server location and request path'''
	if server[-1] == '/' and path[0] != '/':
		return server + path
	elif server[-1] == '/' and path[0] == '/':
		return server + path[1:]
	elif server[-1] != '/' and path[0] != '/':
		return server + '/' + path
	else:
		return server + path


def _extractNumber(strnum):
	'''obtain the first integer found in strnum'''
	stidx = 0
	while stidx < len(strnum) and strnum[stidx] not in '0123456789':
		stidx+=1
	endidx = stidx
	while endidx < len(strnum) and strnum[endidx] in '0123456789':
		endidx+=1
	if endidx>stidx:
		return int(strnum[stidx:endidx])
	else:
		return None


_missionIndex = None
def getMission(name,server):
	'''get the ID of the mission'''
	global _missionIndex
	if _missionIndex is None:
		try:
			response = requests.get(_constructURL(server,MISSION_API))
		except requests.exceptions.ConnectionError:
			raise APIFatalException("Failed to connect to server at %s" % server)
		if response.ok:
			_missionIndex = {_extractNumber(i['name']):i['id'] for \
				i in response.json()}
		else:
			raise APIFatalException("Failed to collect existing mission IDs")
	
	missionNo = _extractNumber(name)
	if missionNo in _missionIndex:
		return _missionIndex[missionNo]
	else:
		raise APIWarningException("No matching mission found for %s" % name)
		##TODO - mission upload, or user intervention.


_personIndex = None
def getPerson(name,server,token):
	'''get the ID of the referenced name
	returns None if not found.'''
	global _personIndex
	if _personIndex is None:
		try:
			response = requests.get(_constructURL(server,PEOPLE_API))
		except requests.exceptions.ConnectionError:
			raise APIFatalException("Failed to connect to server at %s" % server)
		
		if response.ok:
			_personIndex = {item['name']:item['id'] for item \
				in response.json()}
		else:
			raise APIFatalException("Failed to collect existing person IDs")
			
	if name in _personIndex:
		return _personIndex[name]
	else:
		_personIndex[name] = personUpload(name,server,token)
		return _personIndex[name]


def personUpload(name,server,token):
	'''put a new dummy person in the API server, return ID'''
	headers = {'Authorization':"Token token=%s" % token,
		'content-type':'application/json'}
	
	json = {
		"name" : name,
		"title" : "%s-dummy" % name,
		"photo_url" : "exploreapollo.com",
	}
	try:
		response = requests.post(_constructURL(server,PEOPLE_API),
			json=json,headers=headers)
	except requests.exceptions.ConnectionError:
		raise APIFatalException("Failed to connect to server at %s" % server)
	
	if response.ok:
		return response.json()['id']
	else:
		_raiseUploadException(response,"Person upload")


def _sToMs(s):
	'''convert seconds into ms'''
	return int(float(s)*1000)


def _isNum(numCandidate):
	try:
		float(numCandidate)
		return True
	except ValueError:
		return False


def _getFileLengthMs(filepath):
	'''get the length of a wav file in ms'''
	#note - builtin wave library is not sufficient because it is unable
	#to handle certain wave formats.
	res = subprocess.run(['ffmpeg','-i',filepath],stderr=subprocess.PIPE,
		universal_newlines=True)
	for line in res.stderr.split('\n'):
		if "Duration" in line:
			timestr = line.split()[1]
			hour, minute, sec = map(lambda s:float(s.strip(',')),timestr.split(':'))
			durationSec = sec + 60*(minute + 60*hour)
			
		
	return int(durationSec * 1000)


_FORM0 = 0 #stMEt, text, duration, speaker
_FORM1 = 1 #stMet, endMet, text, speaker
def _inferFormat(f):
	'''obtain the file format of fileobject f
	return -1 if cannot be inferred'''
	for line in f:
		toks = [t.strip() for t in line.split('\t')]
		form0 = _isNum(toks[3])
		form1 = _isNum(toks[2])
		
		if form0 and not form1:
			return _FORM0
		elif not form0 and form1:
			return _FORM1
	return -1


def _parseTranscriptLine(line,lineFormat,fileMetStart,server,token):
	'''infer the line format and return startMet, endMet, text, speaker
	return None on error'''
	tok = [t.strip() for t in line.split('\t')]
	if len(tok) != 5:
		return None
	elif lineFormat == _FORM0:
		stMet   = _sToMs(tok[1]) + fileMetStart
		endMet  = _sToMs(tok[3]) + stMet
		text    = tok[2]
		spkID   = getPerson(tok[4].strip('"'),server,token)
		return (stMet, endMet, text, spkID)
	elif lineFormat == _FORM1:
		stMet   = _sToMs(tok[1]) + fileMetStart
		endMet  = _sToMs(tok[2]) + fileMetStart
		text    = tok[3]
		spkID   = getPerson(tok[4].strip('"'),server,token)
		return (stMet, endMet, text, spkID)
	else:
		return None
	

def transcriptUpload(filepath,channel,fileMetStart,server,token,
		propName=None):
	'''Parse and upload the transcript items to API server
	When propname is not None, it is used in error output
	rather than filepath
	'''
	if propName is None:
		propName = filepath
	headers = {'Authorization':"Token token=%s" % token,
		'content-type':'application/json'}
	
	with open(filepath) as f:
		lineFormat = _inferFormat(f)
		f.seek(0)
		for lineNo, line in enumerate(f):
			try:
				items = _parseTranscriptLine(line,lineFormat,fileMetStart,
					server,token)
				if items is None and len(line.split('\t')) == 0:
					continue
				elif items is None:
					pass
					##TODO - log error.
				else:
					startMet, endMet, text, personID = items

				json = {
					"text"      : text,
					"met_start" : startMet,
					"met_end"   : endMet,
					"person_id" : personID,
					"channel_id": channel,
					}
				response = requests.post(_constructURL(server,
					TRANSCRIPT_ITEM_API),json=json,headers=headers)
				if not response.ok:
					_raiseUploadException(response, "Transcript item")
			
			except APIWarningException as e:
				print("ERROR - Transcript item, %s:%d  %s" % (
					propName,lineNo+1,e.reason),file=sys.stderr)
			except requests.exceptions.ConnectionError:
				raise APIFatalException("Failed to connect to server at %s" % \
					server)
			except APIFatalException as e:
				raise e


def audioDataUpload(filepath,s3URL,channel,fileMetStart,server,token):
	'''upload audio segment data to API server.  does NOT upload audio file'''
	fileMetEnd = fileMetStart + _getFileLengthMs(filepath)
	headers = {'Authorization':"Token token=%s" % token,
		'content-type':'application/json'}
	
	json = {
		"title"      : os.path.split(s3URL)[1],
		"url"        : s3URL,
		"met_start"  : fileMetStart,
		"met_end"    : fileMetEnd,
		"channel_id" : channel,
	}
	try:
		response = requests.post(_constructURL(server,AUDIO_SEGMENT_API),
			json=json,headers=headers)
	except requests.exceptions.ConnectionError:
		raise APIFatalException("Failed to connect to server at %s" % server)
	if not response.ok and response.status_code == 401:
		raise APIFatalException(
			"Audio data - 401 Authorization failed, check API server token")
	elif not response.ok:
		print("ERROR - Audio segment, %s  %d %s" % (
			s3URL,response.status_code,response.text),
			file=sys.stderr) 


def mediaDataUpload(url,title,mission,server,token,**kwargs):
	'''upload media data.  Does NOT upload media itself
	allowed kwargs - description,caption,alt_text,type'''
	headers = {'Authorization':"Token token=%s" % token,
		'content-type':'application/json'}
	
	json = {
		"url"        : url,
		"title"      : title,
		"mission_id" : mission, ###TODO - get mission ID
	}
	
	if kwargs is not None:
		for jhead, jval in  kwargs.items():
			json[jhead] = jval
	
	try:
		response = requests.post(_constructURL(server,MEDIA_API),
			json=json,headers=headers)
	except requests.exceptions.ConnectionError:
		raise APIFatalException("Failed to connect to server at %s" % server)
	if not response.ok and response.status_code == 401:
		raise APIFatalException(
			"Media data - 401 Authorization failed, check API server token")
	elif not response.ok:
		print("ERROR - Media data, %s  %d %s" % (
			url,response.status_code,response.text),
			file=sys.stderr) 
	
	