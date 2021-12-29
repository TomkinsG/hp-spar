
import json
from datetime import time

MQTTusername = 'spar'
MQTTpassword = '########'
MQTThostname = 'localhost'
MQTTport     = 1883
MQTTidentity = 'spar'

sparExtendAlarm    = 30              # extend the alarm run duration by xx seconds; 0 means no extend
sparHeartbeat      = 60              # seconds between beats
sparWeeklyTestDay  = 1               # -1= no test, 1=Monday, 2=Tuesday, ..., 6=Sunday
sparWeeklyTestTime = time(9,0,0,0)   # minute of the day.  Note seconds and microseconds are ignored
sparDebug          = True
sparCurrentDelay   = 0.5             # delay between current observations


config = {
  "MQTTusername"         : MQTTusername,
  "MQTTpassword"         : MQTTpassword,
  "MQTThostname"         : MQTThostname,
  "MQTTport"             : MQTTport,
  "MQTTidentity"         : MQTTidentity,
  "sparExtendAlarm"      : sparExtendAlarm,
  "sparHeartbeat"        : sparHeartbeat,
  "sparWeeklyTestDay"    : sparWeeklyTestDay,
  "sparWeeklyTestTimeHH" : sparWeeklyTestTime.strftime("%H"),
  "sparWeeklyTestTimeMM" : sparWeeklyTestTime.strftime("%M"),
  "sparDebug"            : sparDebug,
  "sparCurrentDelay"     : sparCurrentDelay
}

myConfig = json.dumps(config,indent=4)


with open("sparconf.json","w") as jsonfile:
    jsonfile.write(myConfig)
    print("great success")


