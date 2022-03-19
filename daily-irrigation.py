#!/usr/bin/env python
#
# Irrigation system
# Release 2020-07-05 First version
# Release 2021-02-09 Added queries
# Release 2021-02-22 Added meteolib and caluclation of Evaporation via Makkink formula
# Release 2021-02-24 Changed days to 28, as too early suggesting to add water
# Release 2021-03-01 Added irrigation table with historical amount of water sprinkled
# Release 2021-03-02 Added Watering: define zone, set RPi on/off
# Release 2021-03-08 Changed from daily figures to per 5m (raw data). Changed to 35 days
# Release 2021-03-14 Updated what it prints without logging
# Release 2021-04-08 Fixed stability for NULL entries in database (happens if temporarily disconnected from weather station)
# Release 2021-04-11 Updated driving the sprinkler system with new motorized ball valves instead of solenoid valves
#                    Added front garden watering, optimized per minute flow measurement
# Release 2021-04-17 Updated for actual working on Raspberry Pi (relay PINs, Keyboard interrupts)
# Release 2021-04-21 Forked for fixed watering during X minuten
# Release 2021-04-22 Adding more structures with classes and files
# Release 2021-04-29 Changed Zone no_barrel to required water pressure (minimal flow)
# Release 2021-05-13 Flipped flowmeter pins 11 and 16
# Release 2021-05-14 Debugging connection issues with fetching row by row
# Release 2021-05-25 Fixing RPi import error to run correctly (emulating) on development host
# Release 2021-06-04 Fixed some logging, added shadow factor (how much a zone is exposed to the sun)
# Release 2021-06-07 Added SigTerm to gracefully exit the process
# Release 2021-06-12 Renamed to 'irrigate', changed relay for grass to 4, and pin sprinkler to 15
# Release 2021-08-07 Added file logging also going to systemd log (journal), added better messages when no water sources
# Release 2021-09-12 Replaced relay board, re-distributed PINs
# Release 2021-09-18 Added MAX_IRRIGATION to limit daily amount of water
# Release 2022-03-13 Adapted EVAP_RANGE (how far looking back) to 21 days from winter/spring logging
#
# TODO
# - Issue with flow vs pressure: Sprinklers generate flow of only ~2, but pressure is good...
# - Fix multiple zone logging on command line (split the command line and find splitted in list)
# - Add expected flow rate to put max timer?
# - Somehow detect if flow meters are working...
# - Test MySQL connection parameters (e.g. if none provided, if -a provided and only writing)
# - Add Evaporation calculation-only mode (to run the script to predict/inform about the upcoming the irrigation)
# - Recover nicely if cannot connect to database (network down)
#
# Author E Zuidema
#
# Although there is an explicit copyright on this sourcecode, anyone may use it freely under a 
# "Creative Commons Naamsvermelding-NietCommercieel-GeenAfgeleideWerken 3.0 Nederland" licentie.
# Please check http://creativecommons.org/licenses/by-nc-nd/3.0/nl/ for details
#
# This software is provided as is and comes with absolutely no warranty.
# The author is not responsible or liable (direct or indirect) to anyone for the use or misuse of this software.
# Any person using this software does so entirely at his/her own risk. 
# That person bears sole responsibility and liability for any claims or actions, legal or civil, arising from such use.
# If you believe this software is in breach of anyone's copyright you will inform the author immediately so the offending material 
# can be removed upon receipt of proof of copyright for that material.
#

progname='irrigate.py'
version = "2022-03-13"

import sys
import signal
import logging
from systemd import journal
import argparse
import time
from time import sleep
from datetime import datetime
import mysql.connector
import numpy
import math
import socket
import threading

# Trying to import Raspberry Pi
try:
  import RPi.GPIO as GPIO
except ImportError:
  # Just continue if does not work; later checking if running on RPi
  pass

import makkink_evaporation

# See also (Dutch) https://www.knmi.nl/kennis-en-datacentrum/achtergrond/verdamping-in-nederland
# And from page 22 of https://edepot.wur.nl/136999 it seems Makkink is indicating too much for grass by 0.88-0.92
# Typically the evaporation seems to be too high, so correcting with a factor
EVAP_FACTOR = 1.0
# How many days of evaporation to look back; should be aligned with how often to irrigate???
EVAP_RANGE = 21

# How much water maximally to irrigate per square meter
MAX_IRRIGATION = 10

# Water Source and Zone names
source_barrel_name   = "Barrel"
source_drinking_name = "Drinking"

zone_grass_name     = "Grass (sweat)"
zone_grass_area     = 10 * 8
zone_grass_shadow   = 0.9 # Almost all day in the sun
zone_grass_min_flow = 0.5 # sweating / soaker hose does not need a lot of pressure / flow
zone_front_name     = "Front (drip)"
zone_front_area     = 12 * 4 + 8 * 4
zone_front_shadow   = 0.7 # Almost all day in the sun, but well vegetated
zone_front_min_flow = 0.5 # dripping hose does not need a lot of pressure / flow
zone_side_name      = "Side (sprinkler)"
zone_side_area      = 10 * 4
zone_side_shadow    = 0.7 # Morning shadows
zone_side_min_flow  = 5.0 # sprinklers need quite some of pressure / flow

# Settings for Relay board 2 (water source ball valves)
valve_drinking_PIN  = 31
valve_barrel_PIN    = 32

# Settings for Relay board 4 (solenoids for up to 4 irrigation areas)
valve_grass_PIN     = 35
valve_front_PIN     = 36
valve_sprinkler_PIN = 37
#valve_SPARE_PIN = 38

# Settings for Flow meter GPIO pins
flow_grass_PIN      = 7  # Yellow wire
flow_front_PIN      = 11 # Green wire
flow_sprinkler_PIN  = 15 # Purple wire

def parse_arguments(logger):
  ################################################################################################################################################
  #Commandline arguments parsing
  ################################################################################################################################################    
  parser = argparse.ArgumentParser(prog=progname, description='Automatic Irrigation script', epilog="Copyright (c) E. Zuidema")
  parser.add_argument("-l", "--log", help="Logging level, can be 'none', 'info', 'warning', 'debug', default='none'", default='none')
  parser.add_argument("-f", "--logfile", help="Logging output, can be 'stdout', or filename with path, default='stdout'", default='stdout')
  parser.add_argument("-d", "--days", help="How many days to look back, default %d (exclusive with amount)" % EVAP_RANGE, default=EVAP_RANGE)
  parser.add_argument("-a", "--amount", help="How many liters per m2 to irrigate (exclusive with days)", default = '0')
  parser.add_argument("-z", "--zones", help="Zone(s) to irrigate, can be 'grass', 'sprinkler', 'front' or multiple. Default is all", default='all', nargs='*')
  parser.add_argument("-i", "--info", help="Do not actually irrigate, just show what it would have done", default=False, action="store_true")
  parser.add_argument("-e", "--emulate", help="Do not actually open/close valves or store data", default=False, action="store_true")
  parser.add_argument("-s", "--server", help="MySQL server or socket path, default='localhost'", default='localhost')
  parser.add_argument("-u", "--user", help="MySQL user, default='root'", default='root')
  parser.add_argument("-p", "--password", help="MySQL user password, default='password'", default='password')
  args = parser.parse_args()

  # Handle debugging messages
  if (args.logfile == 'stdout'):
    if (args.log == 'info'):
      # info logging to systemd which already lists timestamp
      logging.basicConfig(format='%(asctime)s - %(name)s - %(message)s')
    else:
      logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(lineno)d - %(message)s')
  else:
    logging.basicConfig(filename=args.logfile,format='%(asctime)s - %(levelname)s - %(lineno)d - %(message)s')
    # Also log to systemd
#    logger.addHandler(journal.JournalHandler())

  # Setting loop duration; default 60s
  loop_seconds = 60

  if (args.log == 'debug'):
    logger.setLevel(logging.DEBUG)
    loop_seconds = 10
  if (args.log == 'warning'):
    logger.setLevel(logging.WARNING)
  if (args.log == 'info'):
    logger.setLevel(logging.INFO)
    loop_seconds = 30
  if (args.log == 'error'):
    logger.setLevel(logging.ERROR)

  if (float(args.amount) != 0):
    # If amount is specified, ignore days
    days = 0
    amount = float(args.amount)
  else:
    days = int(args.days)
    amount = 0

  if args.emulate:
    emulating = True
  else:
    emulating = False

  if args.info:
    info = True
    emulating = True
  else:
    info = False

  zones = args.zones
  
  mysql_host=args.server
  mysql_user=args.user
  mysql_passwd=args.password

  # return parsed values
  return (loop_seconds, days, amount, zones, info, emulating, mysql_host, mysql_user, mysql_passwd)

def handle_sigterm(sig, frame):
  print("SigTerm received, raising SystemExit")
  raise(SystemExit)

def load_evaporation( logger, \
                      days, \
                      mysql_host, \
                      mysql_user, \
                      mysql_passwd  ):

  logger.info("Opening MySQL Database weewx on %s", mysql_host)
  db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='weewx')
  cursor = db.cursor()

  # Get the per 5m data from the past X days
  # mysql> select dateTime, FROM_UNIXTIME(dateTime), outHumidity, outTemp, pressure, radiation, rain from archive where dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY) LIMIT 10;
  # +------------+-------------------------+-------------+-------------+----------+---------------------+------+
  # | dateTime   | FROM_UNIXTIME(dateTime) | outHumidity | outTemp     | pressure | radiation           | rain |
  # +------------+-------------------------+-------------+-------------+----------+---------------------+------+
  # | 1614630600 | 2021-03-01 21:30:00     |          82 |         4.7 |   1028.1 |                   0 |    0 |
  # | 1614630900 | 2021-03-01 21:35:00     |          82 |      4.6381 |  1028.08 |                   0 |    0 |
  # | 1614631200 | 2021-03-01 21:40:00     |          82 |     4.53333 |  1028.12 |                   0 |    0 |
  # ...
  # | 1614839700 | 2021-03-04 07:35:00     |          88 |         5.1 |   1018.3 |                   0 |  0.3 |
  # | 1614840000 | 2021-03-04 07:40:00     |          88 |         5.1 |  1018.26 |                   0 |    0 |
  # | 1614840300 | 2021-03-04 07:45:00     |          88 |         5.1 |  1018.12 |  0.0744857142857143 |  0.3 |
  # | 1614840600 | 2021-03-04 07:50:00     |          88 |         5.1 |  1018.27 |  0.0744857142857143 |    0 |
  # ...
  # | 1615209000 | 2021-03-08 14:10:00     |      44.619 |     11.0143 |  1019.96 |    631.790574761905 |    0 |
  # | 1615209300 | 2021-03-08 14:15:00     |     43.7143 |     11.6095 |  1019.89 |    614.716605714286 |    0 |
  # | 1615209600 | 2021-03-08 14:20:00     |       41.85 |       11.98 |  1019.88 |         551.6908515 |    0 |
  # | 1615209900 | 2021-03-08 14:25:00     |     40.8571 |      11.981 |  1019.76 |    400.116792380952 |    0 |
  # ...
  # | 1617792000 | 2021-04-07 12:40:00     |        NULL |        NULL |  1014.02 |           295.11714 |    0 |
  # | 1617792300 | 2021-04-07 12:45:00     |        NULL |        NULL |  1014.06 |           295.11714 |    0 |
  # | 1617792600 | 2021-04-07 12:50:00     |        NULL |        NULL |  1014.19 |                NULL |    0 |
  # | 1617792900 | 2021-04-07 12:55:00     |        NULL |        NULL |  1014.28 |                NULL |    0 |
  #
  query = "SELECT FROM_UNIXTIME(dateTime), outHumidity, outTemp, pressure, radiation, rain from archive " + \
          "WHERE dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL " + str(days) + " DAY)"
  logger.debug("Query: %s", query)
  cursor.execute(query)

  humidityDay = []
  tempDay = []
  pressureDay = []
  radiationDay = []
  rainDay = []

  for row in cursor:
    logger.debug("Time = %s", row[0])
    try:
      humidityDay.append(float(row[1]))
      tempDay.append(float(row[2]))
      # Database is in HPa, need in Pa
      pressureDay.append(float(row[3]) * 100)
      # Database is Watt per second, and need Joules / m2
      # need to x 5 (datapoint per 5 minutes) x 60 (minutes to seconds)
      radiationDay.append(float(row[4]) * 5 * 60)
      rainDay.append(float(row[5]))
    except TypeError:
      # There was a NULL in the data, so skip this row: continue with next row (and overwrite filled values, as i is not increased)
      logger.debug("Row skipped due to incorrect data")
      continue
    logger.debug("Point %d: Humidity: %.0f %%, Temp: %.1f deg C, Pressure: %.0f Pa, Radiation: %.0f J/m2, Rain: %.1f mm", len(tempDay), humidityDay[-1], tempDay[-1], pressureDay[-1], radiationDay[-1], rainDay[-1])

  # Close weewx database
  if (db.is_connected()):
    db.close()
    cursor.close()
    logger.info("MySQL connection is closed")

  # return the collected values turned into numpy arrays
  return numpy.array(tempDay), numpy.array(humidityDay), numpy.array(pressureDay), numpy.array(radiationDay), numpy.array(rainDay)

def load_irrigated( logger, \
                    zone, \
                    days, \
                    mysql_host, \
                    mysql_user, \
                    mysql_passwd  ):

  # Open irrigation database
  logger.info("Opening MySQL Database irrigation on %s for loading data", mysql_host)

  db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='irrigation')
  cursor = db.cursor()

  # NEED TO ADD EXAMPLE WITH zone FIELD
  #
  # Get the irrigation from the past X days, watered in liters per m2 = mm
  # mysql> select dateTime, watered, UNIX_TIMESTAMP(NOW()), UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY) from irrigated where dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY);
  # +------------+---------+-----------------------+----------------------------------------+
  # | dateTime   | watered | UNIX_TIMESTAMP(NOW()) | UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY) |
  # +------------+---------+-----------------------+----------------------------------------+
  # | 1614553200 |       0 |            1614673885 |                             1614501085 |
  # | 1614636558 | 1.05394 |            1614673885 |                             1614501085 |
  # +------------+---------+-----------------------+----------------------------------------+
  #
  query = "SELECT FROM_UNIXTIME(dateTime), watered from irrigated " + \
          "WHERE dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL " + str(days) + " DAY) AND " + \
          "zone LIKE '%%" + zone + "%%'"
  logger.debug("Query: %s", query)
  cursor.execute(query)
  records = cursor.fetchall()
  amount = cursor.rowcount
  waterDay = numpy.zeros(amount)
  waterSum = 0
  i = 0
  for row in records:
    waterDay[i] = float(row[1])
    logger.debug("Point %d: Time: %s Irrigation: %.1f liters per m2", i, row[0], waterDay[i])
    i = i + 1

  # Close irrigation database
  if (db.is_connected()):
    db.close()
    cursor.close()
    logger.info("MySQL connection is closed")

  # Return the collected values
  return waterDay


def save_irrigated( logger, \
                    zone, \
                    watering_mm, \
                    mysql_host, \
                    mysql_user, \
                    mysql_passwd ):

  # First make sure there is some irrigation to write
  if (watering_mm > 0.0):
    # Open irrigation database
    logger.info("Opening MySQL Database irrigation on %s for writing data", mysql_host)

    db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='irrigation')
    cursor = db.cursor()

    # Add irrigation amount (mm) to database
    query = "INSERT INTO irrigated (dateTime, zone, watered) VALUES (%s, %s, %s)"
    insert_time = time.time()
    insert_zone = zone
    insert_water = round(watering_mm, 1)
    values = (insert_time, insert_zone, insert_water)
    logger.debug("Query: %s", query)
    logger.debug("Values: %d, %s, %f", insert_time, insert_zone, insert_water)
    cursor.execute(query, values)
    db.commit()
    logger.info("Added irrigation of %0.1f mm on %s to database", watering_mm, zone)

    # Close irrigation database
    if (db.is_connected()):
      db.close()
      cursor.close()
      logger.info("MySQL connection is closed")

  # return

# Generic repeating timer class for emulating callbacks
class RepeatedTimer():
  def __init__(self, logger, interval, function, *args, **kwargs):
    self.logger = logger
    self.logger.debug("RepeatedTimer init")
    self._timer = None
    self.interval = interval
    self.function = function
    self.args = args
    self.kwargs = kwargs
    self.is_running = False

  def _run(self):
#    self.logger.debug("RT_run %d:\tSetting running to False, call cont again, and run callback" % threading.get_ident())
    self.is_running = False
    self.cont()
    self.function(*self.args, **self.kwargs)

  def start(self):
#    self.logger.debug("RT start")
    self.next_call = time.time()
    self.cont()

  def cont(self):
#    self.logger.debug("RT cont")
    if not self.is_running:
      self.next_call += self.interval
      delta = self.next_call - time.time()
#      self.logger.debug("RT cont - starting next thead in %.3f s" % delta)
      self._timer = threading.Timer(delta, self._run)
      self._timer.start()
      self.is_running = True
    # if running do nothing

  def stop(self):
    self._timer.cancel()
    self.is_running = False

class WaterSource():
  
  def __init__(self, logger, name, relay_pin):
    self.logger = logger
    self.logger.debug("WaterSource init for %s", name)
    self.name = name
    self.relay_pin = relay_pin

  def get_name(self):
    return self.name

  def open_valve(self):
    self.logger.info("Setting %s water ON", self.name)
    # Note: Takes 10-15 seconds to fully open
    GPIO.output(self.relay_pin, GPIO.HIGH)

  def close_valve(self):
    self.logger.info("Setting %s water OFF", self.name)
    # Note: Takes 10-15 seconds to fully close
    GPIO.output(self.relay_pin, GPIO.LOW)


class IrrigationZone():
  
  def __init__(self, logger, name, relay_pin, area, shadow, flow_pin, flow_required = -1):
    self.logger = logger
    self.logger.debug("IrrigationZone init for %s", name)
    self.name = name
    self.area = area
    self.shadow = shadow
    self.irrigated_liters = 0
    self.relay_pin = relay_pin
    self.flow_pin = flow_pin
    self.flow_required = flow_required

    # Start a flowmeter associated with this zone
    self.flow_meter = FlowMeter(self.logger, self.name)

    # Prepare for emulated callback: Calling every 50 times per second
    self.timer = RepeatedTimer(self.logger, 0.02, self.flow_meter.pulseCallback)

  def get_name(self):
    return self.name
    
  def get_area(self):
    return self.area

  def get_shadow(self):
    return self.shadow

  def open_valve(self):
    self.logger.info("Setting %s zone ON", self.name)
    GPIO.output(self.relay_pin, GPIO.LOW)

  def close_valve(self):
    self.logger.info("Setting %s zone OFF", self.name)
    GPIO.output(self.relay_pin, GPIO.HIGH)

  def get_flow_pin(self):
    return self.flow_pin

  def set_pulse_callback(self):
    self.logger.debug("%s: set_pulse_callback:", self.name)
    # EZ lowered bouncetime from 20 to 1 ms, as pulse callbacks coming in faster (0.006 s!)
    GPIO.add_event_detect(self.flow_pin, GPIO.RISING, callback=self.flow_meter.pulseCallback, bouncetime=1)

  def set_emulated_pulse_callback(self):
    self.logger.debug("%s: set_emulated_pulse_callback:", self.name)
    self.timer.start()

  def clear_pulse_callback(self):
    self.logger.debug("%s: clear_pulse_callback:", self.name)
    GPIO.remove_event_detect(self.flow_pin)

  def clear_emulated_pulse_callback(self):
    self.logger.debug("%s: clear_emulated_pulse_callback:", self.name)
    self.timer.stop()

  def get_flow_rate(self):
    self.logger.debug("%s: get_flow_rate:", self.name)
    return self.flow_meter.getFlowRate()

  def get_flow_required(self):
    self.logger.debug("%s: get_flow_required:", self.name)
    return self.flow_required

  def get_irrigated_liters(self):
    return self.irrigated_liters

  def set_irrigated_liters(self, actual):
    self.irrigated_liters = actual

class FlowMeter():
  ''' Class representing the flow meter sensor which handles input pulses
      and calculates current flow rate (L/min) measurement
  '''

  def __init__(self, logger, name):
    self.logger = logger
    self.logger.debug("Flow init for %s, setting last_time to now, and rate to 0", name)
    self.name = name
    self.average_flow_rate = 0.0
    self.last_flow_rates = numpy.array([])
    self.last_flow_rate = 0.0
    self.last_time = datetime.now()

  def pulseCallback(self, pin=0):
    ''' Callback that is executed with each pulse
        received from the sensor
    '''
    self.logger.debug("%s: pulseCallback: Flowing! (pin %d)", self.name, pin)
    # Calculate the time difference since last pulse received
    current_time = datetime.now()
    diff = (current_time - self.last_time).total_seconds()
    if(diff < 2):
      # Calculate current flow rate
      hertz = 1.0 / diff
      self.last_flow_rate = hertz / 7.5
      self.last_flow_rates = numpy.append(self.last_flow_rates, self.last_flow_rate)
      self.logger.debug("%s: pulseCallback: Rate %.1f (diff %.3f s from last_time %s)" % (self.name, self.last_flow_rate, diff, self.last_time))
    else:
      # Took too long, setting rates to 0
      self.flow_rate = 0.0
      self.logger.debug("%s: pulseCallback: Took too long (%.0f s from last_time %s), setting flow rate to 0, resetting array" % (self.name, diff, self.last_time))
      # Empty the array, as took too long
      self.last_flow_rates = numpy.array([])
    # Reset time of last pulse
    self.last_time = current_time
    self.logger.debug("%s: pulseCallback: Array size %d" % (self.name, numpy.size(self.last_flow_rates)))

  def getFlowRate(self):
    ''' Return the current flow rate measurement.
        If a pulse has not been received in last second,
        assume that flow has stopped and set flow rate to 0.0
    '''

    self.logger.debug("%s: getFlowRate:", self.name)

    self.logger.debug("%s: getFlowRate: Last flow rate %.1f" % (self.name, self.last_flow_rate))
    # Calculate average since last call
    stored_values = numpy.size(self.last_flow_rates)
    if (stored_values > 0):
      self.average_flow_rate = numpy.average(self.last_flow_rates)
    else:
      self.average_flow_rate = 0.0
    self.logger.debug("%s: getFlowRate: Average flow rate %.1f (from %d values)" % (self.name, self.average_flow_rate, stored_values))
    # Re-initialize the array
    self.last_flow_rates = numpy.array([])

    return self.average_flow_rate

# Main
def main():
  ################################################################################################################################################
  #Main program
  ################################################################################################################################################
  print("%s %s (version %s)" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), progname, version))
  #print("Python version %s.%s.%s" % sys.version_info[:3])

  logger = logging.getLogger(progname)

  (loop_seconds, days, amount, zones_to_water, info, emulating, mysql_host, mysql_user, mysql_passwd) = parse_arguments(logger)
  logger.info("Started program %s, version %s", progname, version)

  if (days == 0):
    logger.info("Irrigating %.2f mm", amount)
  else:
    logger.info("Looking back: %d days", days)

  logger.debug("Zones to water: %s", zones_to_water)
  
  logger.debug("MySQL Server  : %s", mysql_host)
  logger.debug("MySQL User    : %s", mysql_user)
  logger.debug("MySQL Password: %s", mysql_passwd)

  host_name = socket.gethostname()
  if (emulating or "raspberrypi" not in host_name):
    logger.info("Running on %s, emulating RPi behaviour", host_name)
    emulating = True
  else:
    logger.info("Running on %s, running real RPi GPIO", host_name)
    emulating = False

    # Set reference to PIN numbers
    GPIO.setmode(GPIO.BOARD)

    # Settings for Relay board 2 (water source ball valves to LOW = Closed)
    GPIO.setup(valve_barrel_PIN,   GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(valve_drinking_PIN, GPIO.OUT, initial=GPIO.LOW)

    # Settings for Relay board 4, LOW active (solenoids for up to 4 irrigation areas)
    GPIO.setup(valve_grass_PIN,   GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(valve_front_PIN, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(valve_sprinkler_PIN, GPIO.OUT, initial=GPIO.HIGH)
#    GPIO.setup(valve_SPARE_PIN, GPIO.OUT, initial=GPIO.HIGH)

    # Settings for flow meters
    GPIO.setup(flow_grass_PIN,     GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(flow_front_PIN,     GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(flow_sprinkler_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

  # Start handling termination signal with Python Exception
  signal.signal(signal.SIGTERM, handle_sigterm)

  # Done setting up, now starting main program

  # Load evaporation history if days is specficied (alternative is irrigating fixed amount)
  if (days > 0):
    (tempDay, humidityDay, pressureDay, radiationDay, rainDay) = load_evaporation(logger, days, mysql_host, mysql_user, mysql_passwd)
    
    evap = makkink_evaporation.Em(logger, tempDay, humidityDay, pressureDay, radiationDay)

    # Typically the evaporation seems to be too high, so correcting with a factor
    evapSum = numpy.sum(evap) * EVAP_FACTOR
    rainSum = numpy.sum(rainDay)

    logger.info("Evaporation = %.1f mm in last %d days", evapSum, days)
    logger.debug("              (%s)", str(numpy.around(evap, 3)))
    logger.info("Rainfall    = %.1f mm in last %d days", rainSum, days)
    
    # If more rainfall than evaporation, no irrigation is needed
    if (rainSum >= evapSum):
      print("No irrigation needed (%.1f mm more rainfall than evaporation), exiting" % (rainSum - evapSum))
      if (not emulating):
        GPIO.cleanup()
      exit(0)

  # Possibly need to irrigate (depending on past irrigations), set up sources & zones

  # Init zones
  zones = []
  zones.append(IrrigationZone(logger, zone_grass_name, valve_grass_PIN,     zone_grass_area, zone_grass_shadow, flow_grass_PIN,     zone_grass_min_flow))
  zones.append(IrrigationZone(logger, zone_front_name, valve_front_PIN,     zone_front_area, zone_front_shadow, flow_front_PIN,     zone_front_min_flow))
  zones.append(IrrigationZone(logger, zone_side_name,  valve_sprinkler_PIN, zone_side_area,  zone_side_shadow,  flow_sprinkler_PIN, zone_side_min_flow ))

  # Skip if no need to water
  if (not info):
    # Init sources, start with most durable one (will start with source 0), until empty (no flow)
    sources = []
    sources.append(WaterSource(logger, source_barrel_name, valve_barrel_PIN))
    sources.append(WaterSource(logger, source_drinking_name, valve_drinking_PIN))

    # Start irrigation
    # start with first water source (most durable)
    source_index = 0
    source = sources[source_index]
  
  for zone in zones:
    if (zones_to_water != "all"):
      skip = False
      for zone_to_water in zones_to_water:
        if (zone_to_water not in zone.get_name().lower()):
          # Skip this zone
          logger.debug("Skipping zone %s, as %s not in %s", zone.get_name(), zone.get_name().lower(), zones_to_water)
          skip = True
          break
      if (skip): continue # next zone in zones

    # Load evaporation history if days is specficied (alternative is irrigating fixed amount)
    if (days > 0):
      waterDay = load_irrigated(logger, zone.get_name(), days, mysql_host, mysql_user, mysql_passwd)
      waterSum = numpy.sum(waterDay)
      logger.info("Zone %s Watering %.1f mm in last %d days", zone.get_name(), waterSum, days)
      # Now calculate shortage = evaporation - rain - watering
      net_evap = evapSum * zone.get_shadow() - rainSum - waterSum
      print("Zone %s Net Evaporation = %.1f mm in last %d days" % (zone.get_name(), net_evap, days))
      logger.info("Zone %s Net Evaporation = %.1f mm in last %d days" % (zone.get_name(), net_evap, days))

      if net_evap <= 1:
        print("No need for irrigation")
        logger.info("No need for irrigation")
        continue # next zone in zones
      else:
        if (net_evap > MAX_IRRIGATION):
          liters_per_m2 = MAX_IRRIGATION
        else:
          liters_per_m2 = net_evap
    else:
      liters_per_m2 = amount

    if (not info):
      # Translate to liters for this zone
      liters = zone.get_area() * liters_per_m2
    else:
      liters = zone.get_area() * net_evap
      print("Should irrigate zone %s with %.0f liters on the %d m2 area" % (zone.get_name(), liters, zone.get_area()))
      zone.set_irrigated_liters(liters)
      continue # to next zones in zone
        
    print("Starting irrigating zone %s with source %s" % (zone.get_name(), source.get_name()))
    print("Need to put %.0f liters on the %d m2 area" % (liters, zone.get_area()))
    logger.info("Starting irrigating zone %s with source %s" % (zone.get_name(), source.get_name()))
    logger.info("Need to put %.0f liters on the %d m2 area" % (liters, zone.get_area()))

    if (not emulating):
      # Init flowmeter callback
      zone.set_pulse_callback()    
      # Open zone valve
      zone.open_valve()
      # Open source valve
      source.open_valve()      
    else:
      # Init fake flowmeter callback
      zone.set_emulated_pulse_callback()    

    # Initialize timing
    start_time = datetime.now()
    actual_liters = 0.0

    # Wait for some flow to start, get current timestamp and first flow meter reading, while handling terminations
    try:
      sleep(10)
    # Also allow Keyboard interrupts for command line testing
    except (KeyboardInterrupt, SystemExit):
      # Close the valves and exit program
      logger.info("Interrupted; closing valves and exiting...")
      if (not emulating):
        zone.close_valve()
        source.close_valve()
        # Calculate liters per m2 irrigated
        zone.set_irrigated_liters(actual_liters)
        actual_liters_per_m2 = actual_liters / zone.get_area()
        # Store irrigation in database
        save_irrigated(logger, zone.get_name(), float(actual_liters_per_m2), mysql_host, mysql_user, mysql_passwd)
        GPIO.cleanup()
      else:
        # Remove fake flowmeter thread callback
        zone.clear_emulated_pulse_callback()
      exit(-1)
    flow_rate = zone.get_flow_rate()
    logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate)
    actual_liters += 10 / 60 * flow_rate
    # If flowrate is still zero, use 1 liter per minute to initiate
    duration = liters / max(flow_rate, 1) * 60
    logger.info("Stopping in about %d seconds", duration)
    previous_time = start_time
    previous_flow_rate = flow_rate

    while duration > 0:
      try:
        # Monitor every 60 seconds, or remaining duration if smaller (though always more than 5 seconds to measure a flow)
        sleep(min(loop_seconds, max(duration, 5)))
      except (KeyboardInterrupt, SystemExit):
        # Close the valves and exit program
        logger.info("Interrupted; closing valves and exiting...")
        if (not emulating):
          zone.close_valve()
          source.close_valve()
          # Calculate liters per m2 irrigated
          zone.set_irrigated_liters(actual_liters)
          actual_liters_per_m2 = actual_liters / zone.get_area()
          # Store irrigation in database
          save_irrigated(logger, zone.get_name(), float(actual_liters_per_m2), mysql_host, mysql_user, mysql_passwd)
          GPIO.cleanup()
        else:
          # Remove fake flowmeter thread callback
          zone.clear_emulated_pulse_callback()
        print("ERROR: Ended zone %s due to Interruption" % zone.get_name())
        if (actual_liters < liters):
          print("Having only watered %.1f liters of required %.1f" % (actual_liters, liters))
        logger.info("Ended zone %s having watered %.1f mm (%.1f liters)" % (zone.get_name(), actual_liters_per_m2, actual_liters))
        exit(-1)
      # Check flow and time
      current_time = datetime.now()
      current_seconds = (current_time - previous_time).total_seconds()
      flow_rate = zone.get_flow_rate()
      logger.debug("Flow rate: %.0f liter(s) per minute, during %d seconds", flow_rate, current_seconds)

      # See if source flow rate complies to requirement for zone
      if (flow_rate < zone.get_flow_required() and previous_flow_rate < zone.get_flow_required()):
        # Flow rate too low, switch to next source
        logger.info("Switching to next source, as flow rate too low (%.1f then %.1f, where %.1f required)", previous_flow_rate, flow_rate, zone.get_flow_required())
        if (not emulating):
          # Close source valve, make sure it is fully closed before switching to next source
          source.close_valve()
          sleep(15)
        if (source_index < len(sources)-1):
          # Next source
          source_index += 1
        else:
          # Last item in list, stop with error
          logger.info("No more sources, closing valves and exiting...")
          if (not emulating):
            zone.close_valve()
            # Calculate liters per m2 irrigated
            zone.set_irrigated_liters(actual_liters)
            actual_liters_per_m2 = actual_liters / zone.get_area()
            # Store irrigation in database
            save_irrigated(logger, zone.get_name(), float(actual_liters_per_m2), mysql_host, mysql_user, mysql_passwd)
            GPIO.cleanup()
          else:
            # Remove fake flowmeter thread callback
            zone.clear_emulated_pulse_callback()
          print("ERROR: Ended zone %s due to No More Sources (Is there a water flow issue?)" % zone.get_name())
          if (actual_liters < liters):
            print("Having only watered %.1f liters of required %.1f" % (actual_liters, liters))
          logger.info("Ended zone %s having watered %.1f mm (%.1f liters)" % (zone.get_name(), actual_liters_per_m2, actual_liters))
          exit(-1)
        # Continue with next source
        source = sources[source_index]
        print("Continuing irrigating zone %s with source %s" % (zone.get_name(), source.get_name()))
        print("Need to put %.0f liters on the %d m2 area" % (liters-actual_liters, zone.get_area()))
        logger.info("Continuing irrigating zone %s with source %s" % (zone.get_name(), source.get_name()))
        logger.info("Need to put %.0f liters on the %d m2 area" % (liters-actual_liters, zone.get_area()))
        if (not emulating):
          # Open source valve
          source.open_valve()
        # Wait for some flow to start, get current timestamp and first flow meter reading
        try:
          sleep(10)
        except (KeyboardInterrupt, SystemExit):
          # Close the valves and exit program
          logger.info("Interrupted; closing valves and exiting...")
          if (not emulating):
            zone.close_valve()
            source.close_valve()
            # Calculate liters per m2 irrigated
            zone.set_irrigated_liters(actual_liters)
            actual_liters_per_m2 = actual_liters / zone.get_area()
            # Store irrigation in database
            save_irrigated(logger, zone.get_name(), float(actual_liters_per_m2), mysql_host, mysql_user, mysql_passwd)
            GPIO.cleanup()
          else:
            # Remove fake flowmeter thread callback
            zone.clear_emulated_pulse_callback()
          exit(-1)
        flow_rate = zone.get_flow_rate()
        logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate)
        # If flowrate is still zero, use 1 liter per minute to initiate
        duration = (liters - actual_liters) / max(flow_rate, 1) * 60
        logger.info("Stopping in about %d seconds", duration)
      else: # Flow rate is fine, no switching
        # Calculate remaining duration 
        actual_liters += current_seconds / 60 * flow_rate
        duration = (liters - actual_liters) / max(flow_rate, 1) * 60 

      if duration > 0:
        logger.info("Watered %.0f liters from %s (%0.1f l/min), %.0f liters remaining (ready in about %d seconds)", \
                    actual_liters, source.get_name(), flow_rate, liters - actual_liters, duration)
        previous_time = current_time
        previous_flow_rate = flow_rate
    # back to while duration...

    # Done watering this zone, closing valve
    if (not emulating):
      zone.close_valve()
      # Also close source valve, as next zone may need different source
      source.close_valve()
      sleep(15)
      # Remove flowmeter callback
      zone.clear_pulse_callback()
    else:
      # Remove fake flowmeter thread callback
      zone.clear_emulated_pulse_callback()

    # Calculate liters per m2 irrigated
    zone.set_irrigated_liters(actual_liters)
    actual_liters_per_m2 = actual_liters / zone.get_area()

    # Store irrigation in database
    if (not emulating):
      save_irrigated(logger, zone.get_name(), float(actual_liters_per_m2), mysql_host, mysql_user, mysql_passwd)

    print("Ended zone %s having watered %.1f mm (%.1f liters)" % (zone.get_name(), actual_liters_per_m2, actual_liters))
    logger.info("Ended zone %s having watered %.1f mm (%.1f liters)" % (zone.get_name(), actual_liters_per_m2, actual_liters))

  # Done iterating over all zones
  actual_liters = 0
  actual_liters_per_m2 = 0
  for zone in zones:
    actual_liters += zone.get_irrigated_liters()
    actual_liters_per_m2 += zone.get_irrigated_liters() / zone.get_area()
  if (not info):
    print("Ended irrigation having watered %.0f liters" % actual_liters)
    logger.info("Ended irrigation having watered %.0f liters" % actual_liters)
  else:
    print("In total should water %.0f liters" % actual_liters)
    logger.info("In total should water %.0f liters" % actual_liters)
  

  if (not emulating):
    # Clean GPIO settings
    GPIO.cleanup()

  print("%s %s Done." % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), progname))
  logger.info("%s %s Done." % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), progname))

if __name__ == '__main__':
   main()
