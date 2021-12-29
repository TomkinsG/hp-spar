from gpiozero import Button, DigitalOutputDevice, PiBoardInfo, HeaderInfo
from time import sleep
from datetime import datetime, timedelta, time
from os import popen
from subprocess import check_output
from threading import Thread
from adafruit_ads1x15.analog_in import AnalogIn
import paho.mqtt.client as mqtt
import json
import signal
import sys
import board
import busio
import adafruit_ads1x15.ads1015 as ADS

#
# read the config file
#

try:
    with open('sparconf.json',"r") as conf_file:
        config = json.load(conf_file)
except:
    config = {}

#
# set the defaults
#
config.setdefault("MQTTusername", "spar")
config.setdefault("MQTTpassword", "")
config.setdefault("MQTThostname", "localhost")
config.setdefault("MQTTport", 1883)
config.setdefault("MQTTidentity", "spar")       # MQTT logging to spar
config.setdefault("sparExtendAlarm", 30)        # default - run teh pump for 30 seconds after teh alarm pump turns off
config.setdefault("sparHeartbeat", 60)          # default heartbeat every minute
config.setdefault("sparWeeklyTestDay", 1)       # default monday
config.setdefault("sparWeeklyTestTimeHH", "09") # default 09:00
config.setdefault("sparWeeklyTestTimeMM", "00") #
config.setdefault("sparDebug", False)           # default off
config.setdefault("sparCurrentDelay", )         # default - log current every 1/2 second

sparWeeklyTestTime = time(int(config["sparWeeklyTestTimeHH"]),int(config["sparWeeklyTestTimeMM"]),0,0)

#
# Prime the I2C interface to monitor the current
#

# Create the I2C bus to track current
i2c = busio.I2C(board.SCL, board.SDA)
# Create the ADC object using the I2C bus to track current
ads = ADS.ADS1015(i2c)
# Create single-ended input on channel 0
pumpCurrent = AnalogIn(ads, ADS.P0)

#
# initiate the connection to the MQTT broker
#
def on_connect(client, userdata, flags, rc):
    debug("Connected with result code " + rc)

client = mqtt.Client(config["MQTTidentity"])
client.on_connect = on_connect
client.username_pw_set(username=config["MQTTusername"],password=config["MQTTpassword"])
client.connect(config["MQTThostname"], config["MQTTport"], 60)

#
#  MQTT topics and payloads
#
#  EVENTS:
#     Topic: <MQTTidentity>/event/<eventname>
#     Payload:  JSON encoded:  {
#                                "status": true or false
#
#                              }
#                   eventname is one of: monitoring
#                                        current logging
#                                        pump|manual|float|alarm|pi
#
#  PUMP:
#     topic: <MQTTidentity>/pump
#     payload: JSON encoded:  {
#                                "on": true or false,
#                                "onTime": datetime,
#                                "offTime": datetime,
#                                "duration": real
#                             }
#     when status is on, offTime and duration are null
#
#  SWITCHES:
#     topics: <MQTTidentity>/switch/<manual|float|alarm| pi>
#     payload is the same as spar/pump but relates to the specific switch
#
#  PUMP/CURRENT:
#     topic: <MQTTidentity>/pump/current
#     payload: JSON encoded: {
#
#                            }
#
#  HEARTBEAT:
#     topic: <MQTTidentity>/heartbeat
#     payload: JSON encoded: {
#                               "CPUTemp": temp
#                            }
#
onKey             = "on"
onTimeKey         = "onTime"
offTimeKey        = "offTime"
durationKey       = "duration"

CPUTempKey        = "CPUTemp"

eventKey          = "status"
reasonKey         = "reason"

testKey           = "testAlarm"

currentValueKey   = "value"
currentVoltageKey = "voltage"

#
# HEARTBEAT
#
# the heartbeat simply publishes a JSON encoded cpu temperature periodically and then does it all over again. .
# this can be used by anything subscribing to test that the spar is still alive.
#
# it is not recommended to test every heartbest but to respond is several eg 5 consecutive heartbeats are skipped.
#
heartattack = False  # setting to true will stop the heartbeat

def heartbeat(seconds):
   global heartattack  # switch to kill the heartbeat
   while True:
       # grab the temperature, remove the temp= and the new line and the 'C!
       cpu_temp = check_output(['vcgencmd','measure_temp']).decode("utf-8").replace("temp=","").replace("'C\n","")

       # publish the heartbeat
       client.publish(config["MQTTidentity"] + '/heartbeat', payload=json.dumps({CPUTempKey: cpu_temp}, sort_keys=False), qos=0, retain=False)
       debug("heartbeat")

       # handle the weekly test alarm
       now = datetime.now()
       if (config["sparWeeklyTestDay"] == now.weekday()):
           if (((now - timedelta(seconds=config["sparHeartbeat"])).time() < sparWeeklyTestTime) and (sparWeeklyTestTime <= now.time())):
               debug("test alarm")
               client.publish(config["MQTTidentity"] + '/testalarm', payload=json.dumps({testKey:True},sort_keys=False), qos=0, retain=False)

       sleep(seconds)  # wait before we do it all over again!

       if heartattack:
           break

#
# define the 4 switches that can turn on the pump
#
pi_switch       = DigitalOutputDevice(5, False, True) # pi's software controlled switch
alarm_switch    = Button(6,  False)  # the hi switch is the alarm switch
float_switch    = Button(13, False)  # the lo switch is the regular float switch.
manual_switch   = Button(19, False)  # the manual switch on the cct board
pi_pump_current = Button(26, False)  # dout from the current sensor

#
# prime the switch states (globals)
#
manual = False
alarm  = False
float  = False
pi     = False
pump   = False

floatOnTime   = None
floatOffTime  = None
alarmOnTime   = None
alarmOffTime  = None
piOnTime      = None
piOffTime     = None
manualOnTime  = None
manualOffTime = None
pumpOnTime    = None
pumpOffTime   = None

def debug(message):
    global debug
    if config["sparDebug"]:
        print(datetime.now().isoformat() + ": " + message)

#
# Pump routines
#

#
# currentLogger - this logs the current to MQTT.
# It is started when the pump runs (pump_on()) and terminates when the pump turns off (pump_off)
#
def currentLogger(delay):
    global pump
    debug("in current logger.  pump on:"+str(pump))

    # Create the I2C bus to track current
    i2c = busio.I2C(board.SCL, board.SDA)
    # Create the ADC object using the I2C bus to track current
    ads = ADS.ADS1015(i2c)
    # Create single-ended input on channel 0
    pumpCurrent = AnalogIn(ads, ADS.P0)

    while True:
        client.publish(config["MQTTidentity"] + '/pump/current', payload=json.dumps({currentValueKey: pumpCurrent.value, currentVoltageKey: pumpCurrent.voltage}, sort_keys=False), qos=0, retain=False)
        debug("Current: " + str(pumpCurrent.value) + " " + str(pumpCurrent.voltage))

        if delay > 0:
            sleep(delay)

        if not(pump):
            break

    debug("ending current monitor")

#
# pump_on() - performs the actions when the pump activates.  it is called whenever one of the switch states moves from off to on.
#
def pump_on():
    global pump
    global pumpOnTime
    global currentThread

    # only do these actions when the pump turns on - another switch may already have activated the pump
    if not(pump):
        pump = True

        pumpOnTime = datetime.now()
        debug("currentThread active: "+str(currentThread.is_alive()))
        # start logging the current immediately, but only run it once.
        if not(currentThread.is_alive()):
            debug("start current monitor")
            currentThread.start()

        # publish the event
        debug("pump on")

        client.publish(config["MQTTidentity"] + '/event/pump', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
        client.publish(config["MQTTidentity"] + '/pump', payload=json.dumps({onKey:True, onTimeKey: pumpOnTime.isoformat(), offTimeKey:None, durationKey: None},sort_keys=False), qos=1, retain=False)

#
# pump_off() actions taken when the pump turns off
#
def pump_off():
    # this routine is called when one of the switches is turned off, but one of the other switches may still be on!
    global pump
    global pumpOffTime
    global pumpDuration
    global currentThread

    if not(manual or alarm or float or pi):
        pumpOffTime = datetime.now()

        pump = False   # note this also stops the currentLogger so we have to create a new thread for the monitor
        currentThread = Thread(target=currentLogger, args=(config["sparCurrentDelay"],),daemon=True)   # defining the new thread to monitor the current ready for next time.

        pumpDuration = pumpOffTime - pumpOnTime
        debug("pump off")

        client.publish(config["MQTTidentity"] + '/event/pump', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)
        client.publish(config["MQTTidentity"] + '/pump', payload=json.dumps({onKey:False, onTimeKey: pumpOnTime.isoformat(), offTimeKey: pumpOffTime.isoformat(), durationKey: (pumpOffTime - pumpOnTime).total_seconds()},sort_keys=False), qos=1, retain=False)

#
# Event handlers for the manual switch turning on and off.
#
def manual_on():
    global manual
    global manualOnTime

    manual = True
    manualOnTime = datetime.now()
    debug("manual on")

    client.publish(config["MQTTidentity"] + '/event/manual', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/manual', payload=json.dumps({onKey:True, onTimeKey: manualOnTime.isoformat(), offTimeKey: None, durationKey: None}, sort_keys=False), qos=1, retain=False)

    pump_on()

def manual_off():
    global manual
    global manualOffTime

    manual = False
    manualOffTime = datetime.now()
    debug("manual off")

    client.publish(config["MQTTidentity"] + '/event/manual', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/manual', payload=json.dumps({onKey:False, onTimeKey: manualOnTime.isoformat(), offTimeKey: manualOffTime.isoformat(), durationKey: (manualOffTime - manualOnTime).total_seconds()}, sort_keys=False), qos=1, retain=False)

    pump_off()

#
# Event handlers for the float switch turning on and off
#
def float_on():
    global float
    global floatOnTime

    float = True
    floatOnTime = datetime.now()
    debug("float on")

    client.publish(config["MQTTidentity"] + '/event/float', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/float', payload=json.dumps({onKey:True, onTimeKey: floatOnTime.isoformat(), offTimeKey: None, durationKey: None}, sort_keys=False), qos=1, retain=False)

    pump_on()

def float_off():
    global float
    global floatOffTime

    float = False
    floatOffTime = datetime.now()
    debug("float off")

    client.publish(config["MQTTidentity"] + '/event/float', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/float', payload=json.dumps({onKey:False, onTimeKey: floatOnTime.isoformat(), offTimeKey: floatOffTime.isoformat(), durationKey: (floatOffTime - floatOnTime).total_seconds()}, sort_keys=False), qos=1, retain=False)

    pump_off()


#
#  extendAlarm - this thread is started by alarm_off to keep the pump pumping for however many seconds after the alarm turns off
#
def extendAlarm(seconds):
        debug("keeping the pump on for " +  str(config["sparExtendAlarm"]) + " seconds")
        sleep(seconds)
        pi_off()

#
# Event handlers for the alarm switch turning on and off
#
def alarm_on():
    global alarm
    global alarmOnTime

    alarm = True
    alarmOnTime = datetime.now()
    debug("alarm on")

    client.publish(config["MQTTidentity"] + '/event/alarm', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/alarm', payload=json.dumps({onKey:True, onTimeKey: alarmOnTime.isoformat(), offTimeKey: None, durationKey: None}, sort_keys=False), qos=1, retain=False)

    #
    # if we are not extending the alarm pump on duration, simply turn the pump on, otherwise, turn on the pi switch and let it turn the pump on.
    #
    if (config["sparExtendAlarm"] == 0) :
        pump_on()
    else:
        pi_on()

def alarm_off():
    global alarm
    global alarmOffTime
    global ExtendAlarm
    global ExtendAlarmThread

    alarm = False
    alarmOffTime = datetime.now()
    debug("alarm off")

    client.publish(config["MQTTidentity"] + '/event/alarm', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/alarm', payload=json.dumps({onKey:False, onTimeKey: alarmOnTime.isoformat(), offTimeKey: alarmOffTime.isoformat(), durationKey: (alarmOffTime - alarmOnTime).total_seconds()}, sort_keys=False), qos=1, retain=False)

    # if we're not extending just turn off the pump otherwise in a non-blocking manner, pause, turn off the pi switch and let it turn off the pump
    if (config["sparExtendAlarm"] == 0):
        pump_off()
    else:
        # in a non blocking way, pause and then do a pi_off(), which will turn off the pump
        # we first check to see if the extend has already been started - this is where the alarm turns off and then n and off again whilst teh extendion is running
        if not extendAlarmThread.is_alive():
            extendAlarmThread.start()

#
# Event handlers for the pi switch turning on and off.
#
def pi_on():
    global pi
    global piOnTime

    # turn on the switch
    pi_switch.on()
    pi = True
    piOnTime = datetime.now()

    debug("pi on")

    client.publish(config["MQTTidentity"] + '/event/pi', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
    client.publish(config["MQTTidentity"] + '/switch/pi', payload=json.dumps({onKey:True, onTimeKey: piOnTime.isoformat(), offTimeKey: None, durationKey: None}, sort_keys=False), qos=1, retain=False)

    pump_on()

def pi_off():
    global pi
    global piOffTime
    global extendAlarmThread

    # turn off the switch using GPIO
    pi_switch.off()
    pi = False
    piOffTime = datetime.now()

    debug("pi off")
    client.publish(config["MQTTidentity"] + '/event/pi', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)

    client.publish(config["MQTTidentity"] + '/switch/pi', payload=json.dumps({onKey:False, onTimeKey: piOnTime.isoformat(), offTimeKey: piOffTime.isoformat(), durationKey: (piOffTime - piOnTime).total_seconds()}, sort_keys=False), qos=1, retain=False)
    pump_off()
    if (config["sparExtendAlarm"] > 0):
        extendAlarmThread = Thread(target=extendAlarm, args=(config["sparExtendAlarm"],),daemon=True)    # defining the new thread to extend the pump run time next time around

#
# test if started with manual button depressed
#
if manual_switch.is_pressed:
    debug("manual on at start")
    manual_on()
    #
    # add special actions here
    # some extra spar behaviour could be programmed in here.
    #

#
# test if started with float float switch active
#
if float_switch.is_pressed:
    debug("float on at start")
    float_on()

#
# test if started with alarm float switch active
#
if alarm_switch.is_pressed:
    debug("alarm on at start")
    alarm_on()

#
# set up the event handlers
#
float_switch.when_pressed  = float_on
float_switch.when_released = float_off

alarm_switch.when_pressed  = alarm_on
alarm_switch.when_released = alarm_off

manual_switch.when_pressed = manual_on
manual_switch.when_released = manual_off

def keybInterruptHandler(signal, frame):
    heatattack = True # stops the heart beat
    # current and extend threads are daemons and will stop
    client.publish(config["MQTTidentity"] + '/event/monitor', payload=json.dumps({eventKey: False}, sort_keys=False), qos=0, retain=True)
    exit(0)

signal.signal(signal.SIGINT, keybInterruptHandler)
#
#  OK - we're off!  Let's start monitoring
#
currentThread = Thread(target=currentLogger, args=(config["sparCurrentDelay"],),daemon=True)         # defining the thread to monitor the current- but not starting it yet!

if (config["sparExtendAlarm"] > 0):
    extendAlarmThread = Thread(target=extendAlarm, args=(config["sparExtendAlarm"],),daemon=True)    # defining the thread to extend the pump run time after teh alarm switch deactivates

pulseThread = Thread(target=heartbeat, args=(config["sparHeartbeat"],))                              # defining the heartbeat thread 
pulseThread.start()                                                                        # note the heartbeat keeps the spar alive - this is why it is not defined as a daemon

client.publish(config["MQTTidentity"] + '/event/monitor', payload=json.dumps({eventKey: True}, sort_keys=False), qos=0, retain=True)
