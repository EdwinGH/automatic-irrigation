#!/usr/bin/env python
#
# Sprinkler system
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
#
# TODO
# - Issue with flow vs pressure: Sprinklers generate flow of only ~2, but pressure is good...
# - Store irrigation per zone, and calculate required irrigation per zone
# - Log to e-mail?
# - Fix multiple zone logging on command line (split the command line and find splitted in list)
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

progname='Sprinkler.py'
version = "2021-05-14"

import sys
import logging
import argparse
import time
from time import sleep
from datetime import datetime
import mysql.connector
import numpy
import math
import socket
import threading

import RPi.GPIO as GPIO
import smbus

import makkink_evaporation

# Water Source and Zone names
source_barrel_name   = "Barrel"
source_drinking_name = "Drinking"

zone_grass_name     = "Grass (sweat)"
zone_front_name     = "Front (drip)"
zone_sprinkler_name = "Side (sprinkler)"

# Settings for Relay board 2 (water source ball valves)
valve_drinking_PIN = 35
valve_barrel_PIN = 36

# Settings for I2C HATS Relay board 1 (solenoids for up to 4 irrigation areas)
Relay_1_BUS = 1
Relay_1_ADDR = 0x10
Relay_1_ON = 0xFF
Relay_1_OFF = 0x00
valve_grass = 1
valve_front = 2
valve_sprinkler = 3

# Settings for Flow meter GPIO pins
flow_grass_PIN     = 7  # Yellow wire
flow_front_PIN     = 16 # Purple wire
flow_sprinkler_PIN = 11 # Green wire

# Generic repeating timer class for emulating callbacks
class RepeatedTimer(object):
  def __init__(self, interval, function, *args, **kwargs):
    self._timer = None
    self.interval = interval
    self.function = function
    self.args = args
    self.kwargs = kwargs
    self.is_running = False
    self.next_call = time.time()
    # EZ Don't want to auto start
#    self.start()

  def _run(self):
    self.is_running = False
    self.start()
    self.function(*self.args, **self.kwargs)

  def start(self):
    if not self.is_running:
      self.next_call += self.interval
      self._timer = threading.Timer(self.next_call - time.time(), self._run)
      self._timer.start()
      self.is_running = True

  def stop(self):
    self._timer.cancel()
    self.is_running = False


def parse_arguments(logger):
  ################################################################################################################################################
  #Commandline arguments parsing
  ################################################################################################################################################    
  parser = argparse.ArgumentParser(prog=progname, description='Sprinkler', epilog="Copyright (c) E. Zuidema")
  parser.add_argument("-l", "--log", help="Logging level, can be 'none', 'info', 'warning', 'debug', default='none'", default='none')
  parser.add_argument("-f", "--logfile", help="Logging output, can be 'stdout', or filename with path, default='stdout'", default='stdout')
  parser.add_argument("-d", "--days", help="How many days to look back, default 14 (exclusive with amount)", default='14')
  parser.add_argument("-a", "--amount", help="How many liters per m2 to irrigate (exclusive with days)", default = '0')
  parser.add_argument("-z", "--zones", help="Zone(s) to irrigate, can be 'grass', 'sprinkler', 'front' or multiple. Default is all", default='all', nargs='*')
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
    logger.info("Irrigating %.2f mm", amount)
  else:
    days = int(args.days)
    logger.info("Looking back: %d days", days)
    amount = 0

  if args.emulate:
    emulating = True
    logger.debug("Emulating only...")
  else:
    emulating = False

  zones = args.zones
  logger.debug("Zones: %s", zones)
  
  mysql_host=args.server
  mysql_user=args.user
  mysql_passwd=args.password
  logger.debug("MySQL Server  : %s", mysql_host)
  logger.debug("MySQL User    : %s", mysql_user)
  logger.debug("MySQL Password: %s", mysql_passwd)

  # return parsed values
  return (loop_seconds, days, amount, zones, emulating, mysql_host, mysql_user, mysql_passwd)

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
  logger.info("Opening MySQL Database irrigation on %s", mysql_host)

  db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='irrigation')
  # Catch MySQL warnings if level is warnings
  if logger.isEnabledFor(logging.WARNING):
    logger.debug("Setting MySQL warnings to True")
    db.get_warnings = True
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

  # Open irrigation database
  logger.info("Opening MySQL Database irrigation on %s", mysql_host)

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
  
  def __init__(self, logger, name, relay_bus, relay_pin, area, flow_pin, flow_required = -1):
    self.logger = logger
    self.logger.debug("IrrigationZone init for %s", name)
    self.name = name
    self.area = area
    self.irrigated_liters = 0
    self.relay_bus = relay_bus
    self.relay_pin = relay_pin
    self.flow_pin = flow_pin
    self.flow_required = flow_required

    # Start a flowmeter associated with this zone
    self.flow_meter = FlowMeter(self.logger, self.name)

    # Prepare for emulated callback: Calling every 50 times per second
    self.timer = RepeatedTimer(0.02, self.flow_meter.pulseCallback)

  def get_name(self):
    return self.name
    
  def get_area(self):
    return self.area

  def open_valve(self):
    self.logger.info("Setting %s zone ON", self.name)
    self.relay_bus.write_byte_data(Relay_1_ADDR, self.relay_pin, Relay_1_ON)

  def close_valve(self):
    self.logger.info("Setting %s zone OFF", self.name)
    self.relay_bus.write_byte_data(Relay_1_ADDR, self.relay_pin, Relay_1_OFF)

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
    self.logger.debug("%s\tpulseCallback: Array size %d" % (self.name, numpy.size(self.last_flow_rates)))

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
  #print("Control-C to abort")

  logger = logging.getLogger(progname)

  (loop_seconds, days, amount, zones_to_water, emulating, mysql_host, mysql_user, mysql_passwd) = parse_arguments(logger)
  logger.info("Started program %s, version %s", progname, version)

  host_name = socket.gethostname()
  if (emulating or "raspberrypi" not in host_name):
    logger.info("Running on %s, emulating RPi behaviour", host_name)
    emulating = True
  else:
    logger.info("Running on %s, running real RPi GPIO", host_name)
    emulating = False

  # Setting Raspberry Pi IO
  if (not emulating):
    # Set reference to PIN numbers
    GPIO.setmode(GPIO.BOARD)

    # Settings for Relay board 2 (water source ball valves to LOW = Closed)
    GPIO.setup(valve_barrel_PIN,   GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(valve_drinking_PIN, GPIO.OUT, initial=GPIO.LOW)

    # Settings for Relay board 1 (solenoids for up to 4 irrigation areas)
    bus = smbus.SMBus(Relay_1_BUS)

    # Settings for flow meters
    GPIO.setup(flow_grass_PIN,     GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(flow_front_PIN,     GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(flow_sprinkler_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
  else:
    bus = 0

  # Done setting up, now starting main program

  # Load evaporation history if days is specficied (alternative is irrigating fixed amount)
  if (days > 0):
    (tempDay, humidityDay, pressureDay, radiationDay, rainDay) = load_evaporation(logger, days, mysql_host, mysql_user, mysql_passwd)
    
    evap = makkink_evaporation.Em(logger, tempDay, humidityDay, pressureDay, radiationDay)

    evapSum = numpy.sum(evap)
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
  # Init sources, start with most durable one (will start with source 0), until empty (no flow)
  sources = []
  sources.append(WaterSource(logger, source_barrel_name, valve_barrel_PIN))
  sources.append(WaterSource(logger, source_drinking_name, valve_drinking_PIN))

  # Init zones
  zones = []
  # Normally 0.5 l/m is enough flow for sweating (grass) or dripping (front)
  zones.append(IrrigationZone(logger, zone_grass_name, bus, valve_grass, 10 * 8, flow_grass_PIN, 0.5))
  zones.append(IrrigationZone(logger, zone_front_name, bus, valve_front, 12 * 4 + 8 * 4, flow_front_PIN, 0.5))
  # For sprinklers require high flow rate, like 5 l/m
  zones.append(IrrigationZone(logger, zone_sprinkler_name, bus, valve_sprinkler, 10 * 4, flow_sprinkler_PIN, 5.0))

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
      logger.info("Watering %.1f mm in last %d days", waterSum, days)
      # Now calculate shortage = evaporation - rain - watering
      net_evap = evapSum - rainSum - waterSum
      print("Zone %s net Evaporation = %.1f mm in last %d days" % (zone.get_name(), net_evap, days))

      if net_evap <= 1:
        print("No need for irrigation")
        continue # next zone in zones
      else:
        liters_per_m2 = net_evap
    else:
      liters_per_m2 = amount

    # Translate to liters for this zone
    liters = zone.get_area() * liters_per_m2
    print("Starting irrigating zone %s with source %s" % (zone.get_name(), source.get_name()))
    print("Need to put %.0f liters on the %d m2 area" % (liters, zone.get_area()))

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

    # Wait for some flow to start, get current timestamp and first flow meter reading
    sleep(10)
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
        # Monitor every 60 seconds, or remaining duration if smaller
        sleep(min(loop_seconds, duration))
      except KeyboardInterrupt:
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
          exit(-1)
        # Continue with next source
        source = sources[source_index]
        if (not emulating):
          # Open source valve
          source.open_valve()
        # Wait for some flow to start, get current timestamp and first flow meter reading
        sleep(10)
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

  # Done iterating over all zones
  actual_liters = 0
  actual_liters_per_m2 = 0
  for zone in zones:
    actual_liters += zone.get_irrigated_liters()
    actual_liters_per_m2 += zone.get_irrigated_liters() / zone.get_area()
  print("Ended irrigation having watered %.1f liters" % actual_liters)

  if (not emulating):
    # Clean GPIO settings
    GPIO.cleanup()
  
  print("%s %s Done." % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), progname))

if __name__ == '__main__':
   main()
