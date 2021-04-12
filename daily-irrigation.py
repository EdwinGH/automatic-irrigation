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
version = "2021-04-11"

import sys
import logging
import argparse
import time
from time import sleep
import mysql.connector
import numpy
import math
import socket

################################################################################################################################################
#Main program
################################################################################################################################################
print("%s (version %s)" % (progname, version))
#print("Python version %s.%s.%s" % sys.version_info[:3])
#print("Control-C to abort")

################################################################################################################################################
#Commandline arguments parsing
################################################################################################################################################    
parser = argparse.ArgumentParser(prog=progname, description='Sprinkler', epilog="Copyright (c) E. Zuidema")
parser.add_argument("-l", "--log", help="Logging level, can be 'none', 'info', 'warning', 'debug', default='none'", default='none')
parser.add_argument("-f", "--logfile", help="Logging output, can be 'stdout', or filename with path, default='stdout'", default='stdout')
parser.add_argument("-s", "--server", help="MySQL server or socket path, default='localhost'", default='localhost')
parser.add_argument("-d", "--days", help="How many days to look back, default 28", default='35')
parser.add_argument("-u", "--user", help="MySQL user, default='root'", default='root')
parser.add_argument("-p", "--password", help="MySQL user password, default='password'", default='password')
args = parser.parse_args()

# Handle debugging messages
if (args.logfile == 'stdout'):
  if (args.log == 'info'):
    # info logging to systemd which already lists timestamp
    logging.basicConfig(format='%(name)s - %(levelname)s - %(message)s')
  else:
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(lineno)d - %(message)s')
else:
  logging.basicConfig(filename=args.logfile,format='%(asctime)s - %(levelname)s - %(lineno)d - %(message)s')

logger = logging.getLogger(progname)
if (args.log == 'debug'):
  logger.setLevel(logging.DEBUG)
if (args.log == 'warning'):
  logger.setLevel(logging.WARNING)
if (args.log == 'info'):
  logger.setLevel(logging.INFO)
if (args.log == 'error'):
  logger.setLevel(logging.ERROR)

logger.info("Started program %s, version %s", progname, version)

host_name = socket.gethostname()
if (host_name != "raspberrypi-irrigation"):
  logger.info("Running on %s, emulating RPi behaviour", host_name)
  emulating = 1
else:
  logger.info("Running on %s, running real RPi GPIO", host_name)
  emulating = 0

days = int(args.days)
logger.info("Looking back: %d days", days)

# Check and open MySQL connection
mysql_host=args.server
mysql_user=args.user
mysql_passwd=args.password
logger.debug("MySQL Server  : %s", mysql_host)
logger.debug("MySQL User    : %s", mysql_user)
logger.debug("MySQL Password: %s", mysql_passwd)

logger.info("MySQL Database: weewx")	
db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='weewx')
# Catch MySQL warnings if level is warnings
if logger.isEnabledFor(logging.WARNING):
  db.get_warnings = True
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
query = "SELECT FROM_UNIXTIME(dateTime), outHumidity, outTemp, pressure, radiation, rain from archive WHERE dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL " + str(days) + " DAY)"
logger.debug("Query: %s", query)
cursor.execute(query)
records = cursor.fetchall()
amount = cursor.rowcount
logger.debug("Amount of datapoints = %d", amount)

humidityDay = numpy.zeros(amount)
tempDay = numpy.zeros(amount)
pressureDay = numpy.zeros(amount)
radiationDay = numpy.zeros(amount)
rainDay = numpy.zeros(amount)
rainSum = 0

i = 0
for row in records:
  logger.debug("Time = %s", row[0])
  try:
    humidityDay[i] = float(row[1])
    tempDay[i] = float(row[2])
    # Database is in HPa, need in Pa
    pressureDay[i] = float(row[3]) * 100
    # Database is Watt per second, and need Joules / m2
    # need to x 5 (datapoint per 5 minutes) x 60 (minutes to seconds)
    radiationDay[i] = float(row[4]) * 5 * 60
    rainDay[i] = float(row[5])
  except TypeError:
    # There was a NULL in the data, so skip this row: continue with next row (and overwrite filled values, as i is not increased)
    logger.debug("Row skipped due to incorrect data")
    continue
  rainSum += rainDay[i]
  logger.debug("Point %d: Humidity: %.0f %%, Temp: %.1f deg C, Pressure: %.0f Pa, Radiation: %.0f J/m2, Rain: %.1f mm", i, humidityDay[i], tempDay[i], pressureDay[i], radiationDay[i], rainDay[i])
  i = i + 1

logger.info("Amount of datapoints used: %d", i)
logger.debug("Deleting %d elements from arrays", amount-i)
# Remove empty elements at the end if there were errors in the rows
# Apparently cannot prevent array copying in numpy...
humidityDay = humidityDay[:i] 
tempDay = tempDay[:i]
pressureDay = pressureDay[:i]
radiationDay = radiationDay[:i]
rainDay = rainDay[:i]

# Close weewx database
if (db.is_connected()):
  db.close()
  cursor.close()
  logger.info("MySQL connection is closed")

# Open irrigation database
logger.info("MySQL Database: irrigation")	

db = mysql.connector.connect(user=mysql_user, password=mysql_passwd, host=mysql_host, database='irrigation')    
# Catch MySQL warnings if level is warnings
if logger.isEnabledFor(logging.WARNING):
  db.get_warnings = True
cursor = db.cursor()

# Get the irrigation from the past X days, watered in liters per m2 = mm
# mysql> select dateTime, watered, UNIX_TIMESTAMP(NOW()), UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY) from irrigated where dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY);
# +------------+---------+-----------------------+----------------------------------------+
# | dateTime   | watered | UNIX_TIMESTAMP(NOW()) | UNIX_TIMESTAMP(NOW() - INTERVAL 2 DAY) |
# +------------+---------+-----------------------+----------------------------------------+
# | 1614553200 |       0 |            1614673885 |                             1614501085 |
# | 1614636558 | 1.05394 |            1614673885 |                             1614501085 |
# +------------+---------+-----------------------+----------------------------------------+
#
query = "SELECT FROM_UNIXTIME(dateTime), watered from irrigated WHERE dateTime >= UNIX_TIMESTAMP(NOW() - INTERVAL " + str(days) + " DAY)"
logger.debug("Query: %s", query)
cursor.execute(query)
records = cursor.fetchall()
waterDay = numpy.zeros(days)
waterSum = 0
i = 0
for row in records:
  logger.debug("Time = %s", row[0])
  waterDay[i] = float(row[1])
  waterSum += waterDay[i]
  logger.debug("Watered day %d = %.1f liters per m2", i, waterDay[i])
  i = i + 1

# Now calculate the Evaporation of the period

# Supporting routine from http://python.hydrology-amsterdam.nl/moduledoc/_modules/evaplib.html#Em
# Hosted at https://github.com/Kirubaharan/hydrology/blob/master/checkdam/meteolib.py

def es_calc(airtemp= numpy.array([])):
  # Input:
  #    - airtemp: (array of) measured air temperature [Celsius] 
  # Output:
  #    - es: (array of) saturated vapour pressure [Pa]

  # Determine length of array
  n = numpy.size(airtemp)
  # Check if we have a single (array) value or an array
  if n < 2:
    # Calculate saturated vapour pressures, distinguish between water/ice
    if airtemp < 0:
      # Calculate saturation vapour pressure for ice
      log_pi = - 9.09718 * (273.16 / (airtemp + 273.15) - 1.0) \
               - 3.56654 * math.log10(273.16 / (airtemp + 273.15)) \
               + 0.876793 * (1.0 - (airtemp + 273.15) / 273.16) \
               + math.log10(6.1071)
      es = math.pow(10, log_pi)   
    else:
      # Calculate saturation vapour pressure for water
      log_pw = 10.79574 * (1.0 - 273.16 / (airtemp + 273.15)) \
               - 5.02800 * math.log10((airtemp + 273.15) / 273.16) \
               + 1.50475E-4 * (1 - math.pow(10, (-8.2969 * ((airtemp +\
               273.15) / 273.16 - 1.0)))) + 0.42873E-3 * \
               (math.pow(10, (+4.76955 * (1.0 - 273.16\
               / (airtemp + 273.15)))) - 1) + 0.78614
      es = math.pow(10, log_pw)
  else:   # Dealing with an array     
    logger.debug("es_calc - Array (size %d)", n)
    # Initiate the output array
    es = numpy.zeros(n)
    # Calculate saturated vapour pressures, distinguish between water/ice
    for i in range(0, n):              
      if airtemp[i] < 0:
        logger.debug("es_calc - Airtemp below zero")
        # Saturation vapour pressure equation for ice
        log_pi = - 9.09718 * (273.16 / (airtemp[i] + 273.15) - 1.0) \
                 - 3.56654 * math.log10(273.16 / (airtemp[i] + 273.15)) \
                 + 0.876793 * (1.0 - (airtemp[i] + 273.15) / 273.16) \
                 + math.log10(6.1071)
        es[i] = math.pow(10, log_pi)
      else:
        logger.debug("es_calc - Airtemp above zero (%.1f)", airtemp[i])
        # Calculate saturation vapour pressure for water  
        log_pw = 10.79574 * (1.0 - 273.16 / (airtemp[i] + 273.15)) \
                 - 5.02800 * math.log10((airtemp[i] + 273.15) / 273.16) \
                 + 1.50475E-4 * (1 - math.pow(10, (-8.2969\
                 * ((airtemp[i] + 273.15) / 273.16 - 1.0)))) + 0.42873E-3\
                 * (math.pow(10, (+4.76955 * (1.0 - 273.16\
                 / (airtemp[i] + 273.15)))) - 1) + 0.78614
        es[i] = pow(10, log_pw)
  # Convert from hPa to Pa
  es = es * 100.0
  logger.debug("es_calc - Returning es in Pa (e.g. %d)", es[0])
  return es # in Pa

def Delta_calc(airtemp= numpy.array([])):
  #    Input:
  #      - airtemp: (array of) air temperature [Celsius]
  #  Output:
  #      - Delta: (array of) slope of saturated vapour curve [Pa K-1]

  # Determine length of array
  n = numpy.size(airtemp)
  # Check if we have a single value or an array
  if n < 2:   # Dealing with single value...
    # calculate vapour pressure
    es = es_calc(airtemp) # in Pa
    # Convert es (Pa) to kPa
    es = es / 1000.0
    # Calculate Delta
    Delta = es * 4098.0 / math.pow((airtemp + 237.3), 2)*1000
  else:   # Dealing with an array         
    logger.debug("Delta_calc - Array (size %d)", n)
    # Initiate the output arrays
    Delta = numpy.zeros(n)
    # calculate vapour pressure
    es = es_calc(airtemp) # in Pa
    # Convert es (Pa) to kPa
    es = es / 1000.0
    # Calculate Delta
    for i in range(0, n):
      Delta[i] = es[i] * 4098.0 / math.pow((airtemp[i] + 237.3), 2)*1000
  logger.debug("Delta_calc - Returning Delta in Pa/K (e.g. %.1f)", Delta[0])
  return Delta # in Pa/K

def ea_calc(airtemp= numpy.array([]),\
            rh= numpy.array([])):
  # Input:
  #    - airtemp: array of measured air temperatures [Celsius]
  #    - rh: Relative humidity [%]
  # Output:
  #    - ea: array of actual vapour pressure [Pa]

  # Determine length of array
  n = numpy.size(airtemp)
  if n < 2:   # Dealing with single value...    
    # Calculate saturation vapour pressures
    es = es_calc(airtemp)
    # Calculate actual vapour pressure
    eact = float(rh) / 100.0 * es
  else:   # Dealing with an array
    logger.debug("ea_calc - Array (size %d)", n)
    # Initiate the output arrays
    eact = numpy.zeros(n)
    # Calculate saturation vapour pressures
    es = es_calc(airtemp)
    for i in range(0, n):
      # Calculate actual vapour pressure
      eact[i] = float(rh[i]) / 100.0 * es[i]
  logger.debug("ea_calc - Returning eact in Pa (e.g. %d)", eact[0])
  return eact # in Pa

def cp_calc(airtemp= numpy.array([]),\
            rh= numpy.array([]),\
            airpress= numpy.array([])):
  # Input:
  #    - airtemp: (array of) air temperature [Celsius]
  #    - rh: (array of) relative humidity data [%]
  #    - airpress: (array of) air pressure data [Pa]
  # Output:
  #    cp: array of saturated c_p values [J kg-1 K-1]

  # Determine length of array
  n = numpy.size(airtemp)
  # Check if we have a single value or an array
  if n < 2:   # Dealing with single value...
    # calculate vapour pressures
    eact = ea_calc(airtemp, rh)
    # Calculate cp
    cp = 0.24 * 4185.5 * (1 + 0.8 * (0.622 * eact / (airpress - eact)))
  else:   # Dealing with an array
    logger.debug("cp_calc - Array (size %d)", n)
    # Initiate the output arrays
    cp = numpy.zeros(n)
    # calculate vapour pressures
    eact = ea_calc(airtemp, rh)
    # Calculate cp
    for i in range(0, n):
      cp[i] = 0.24 * 4185.5 * (1 + 0.8 * (0.622 * eact[i] / (airpress[i] - eact[i])))
  logger.debug("cp_calc - Returning cp in J/kg/K (e.g. %.1f)", cp[0])
  return cp # in J/kg/K

def L_calc(airtemp= numpy.array([])):
  # Input:
  #    - airtemp: (array of) air temperature [Celsius]
  # Output:
  #    - L: (array of) lambda [J kg-1 K-1]

  # Determine length of array
  n = numpy.size(airtemp)
  # Check if we have a single value or an array
  if n < 2:   # Dealing with single value...
    # Calculate lambda
    L = 4185.5 * (751.78 - 0.5655 * (airtemp + 273.15))
  else:   # Dealing with an array
    logger.debug("L_calc - Array (size %d)", n)
    # Initiate the output arrays
    L = numpy.zeros(n)    
    # Calculate lambda
    for i in range(0, n):
      L[i] = 4185.5 * (751.78 - 0.5655 * (airtemp[i] + 273.15))
  logger.debug("L_calc - Returning Lambda in J/kg (e.g. %.1f)", L[0])
  return L # in J/kg

def gamma_calc(airtemp= numpy.array([]),\
               rh= numpy.array([]),\
               airpress=numpy.array([])):
  # Input:
  #    - airtemp: array of measured air temperature [Celsius]
  #    - rh: array of relative humidity values[%]
  #    - airpress: array of air pressure data [Pa]        
  # Output:
  #    - gamma: array of psychrometric constant values [Pa\K]

  # Determine length of array
  n = numpy.size(airtemp)
  # Check if we have a single value or an array
  if n < 2:   # Dealing with single value...
    cp = cp_calc(airtemp, rh, airpress)
    L = L_calc(airtemp)
    # Calculate gamma
    gamma = cp * airpress / (0.622 * L)
  else:   # Dealing with an array
    logger.debug("gamma_calc - Array (size %d)", n)
    # Initiate the output arrays
    gamma = numpy.zeros(n)
    # Calculate cp and Lambda values
    cp = cp_calc(airtemp, rh, airpress)
    L = L_calc(airtemp)
    # Calculate gamma
    for i in range(0, n):
      gamma[i] = cp[i] * airpress[i] / (0.622 * L[i])
  logger.debug("gamma_calc - Returning Gamma in Pa\K (e.g. %.1f)", gamma[0])
  return gamma # in Pa\K

def Em(airtemp = numpy.array([]),\
       rh = numpy.array([]),\
       airpress = numpy.array([]),\
       Rs = numpy.array([])):

  # airtemp: (array of) daily average air temperatures [Celsius]
  # rh: (array of) daily average relative humidity values [%]
  # airpress: (array of) daily average air pressure data [Pa]
  # Rs (Kin in formula): (array of) average daily incoming solar radiation [J m-2 day-1]
  #
  # output is (array of) Makkink evaporation values [mm day-1]

  # Calculate Delta and gamma constants
  DELTA =  Delta_calc(airtemp)
  gamma =  gamma_calc(airtemp,rh,airpress)
  Lambda = L_calc(airtemp)
  # Determine length of array
  l = numpy.size(airtemp)
  # Check if we have a single value or an array
  if l < 2:   # Dealing with single value...
    logger.debug("Em - Single value")
    # calculate Em [mm/day]
    Em = 0.65 * DELTA/(DELTA + gamma) * Rs / Lambda
  else:   # Dealing with an array         
    # Initiate output array
    logger.debug("Em - Array (size %d)", l)
    Em = numpy.zeros(l)
    for i in range(0,l):   
      # calculate Em [mm/day]
      Em[i]= 0.65*DELTA[i]/(DELTA[i]+gamma[i])*Rs[i]/Lambda[i]
      logger.debug("Em = %.3f (Delta = %.1f, gamma = %.1f, Rs = %.1f, Lambda = %.1f)", Em[i], DELTA[i], gamma[i], Rs[i], Lambda[i])
  logger.debug("Em - Returning Em in mm/day (e.g. %.3f)", Em[0])
  return Em

# Calculate Evaporation with Makkink formula
evap = Em(tempDay, humidityDay, pressureDay, radiationDay)

evapSum = numpy.sum(evap)
logger.info("Evaporation = %.1f mm in last %d days", evapSum, days)
logger.debug("              (%s)", str(numpy.around(evap, 3)))
logger.info("Rainfall    = %.1f mm in last %d days", rainSum, days)
logger.info("Watering    = %.1f mm in last %d days", waterSum, days)

# Now calculate shortage = evaporation - rain - watering
net_evap = evapSum - rainSum - waterSum
print("Net Evaporation = %.1f mm in last %d days" % (net_evap, days))
if net_evap <= 1:
  print("No need for irrigation (<= 1mm / 1 liter per m2)")
  # Close irrigation database
  if (db.is_connected()):
    db.close()
    cursor.close()
  logger.info("MySQL connection is closed")
  print("Done.")
  sys.exit(1)

# Continue with irrigation
logger.info("Need to add %.1f liters per m2 to garden", net_evap)

from datetime import datetime
if (not emulating):
  import RPi.GPIO as GPIO
  import smbus

class FlowMeter():
  ''' Class representing the flow meter sensor which handles input pulses
      and calculates current flow rate (L/min) measurement
  '''
  
  def __init__(self):
    logger.debug("Flow init, setting last_time to now, and rate to 0")
    self.flow_rate = 0.0
    self.last_time = datetime.now()
  
  def pulseCallback(self, p):
    ''' Callback that is executed with each pulse 
        received from the sensor 
    '''
  
    logger.debug("pulseCallback: Flowing!") 
    # Calculate the time difference since last pulse recieved
    current_time = datetime.now()
    diff = (current_time - self.last_time).total_seconds()

    if(diff < 2):       
      # Calculate current flow rate
      hertz = 1. / diff
      self.flow_rate = hertz / 7.5
      logger.debug("Rate: %f (diff %f s)" % (self.flow_rate, diff))
    else:
      # Took too long, setting rates to 0
      self.flow_rate = 0.0
      logger.debug("Took too long (%f s), setting flow rate to 0" % diff)
   
    # Reset time of last pulse
    self.last_time = current_time

  def getFlowRate(self):
    ''' Return the current flow rate measurement. 
        If a pulse has not been received in last second, 
        assume that flow has stopped and set flow rate to 0.0
    '''

    logger.debug("getFlowRate:")
    current_time = datetime.now()
    diff = (current_time - self.last_time).total_seconds()
    if (diff >= 2):
      # Took too long, setting rates to 0
      logger.debug("Took too long (%.0f s), setting flow rate to 100" % diff)
      self.flow_rate = 100.0
    if (diff <= 0.01):
      logger.debug("Took too short (%f s), setting flow rate to 100" % diff)
      self.flow_rate = 100.0

    return self.flow_rate

if (not emulating):
  # Settings for Relay board 1 (solenoid)
  Relay_1_BUS = 1
  Relay_1_ADDR = 0x10
  bus = smbus.SMBus(Relay_1_BUS)
  Relay_1_ON = 0xFF
  Relay_1_OFF = 0x00

  # Settings for Flow meter GPIO pins
  Flow_1_PIN = 7
  Flow_2_PIN = 7
  Flow_3_PIN = 7
  GPIO.setmode(GPIO.BOARD)
  GPIO.setup(Flow_1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
  GPIO.setup(Flow_2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
  GPIO.setup(Flow_3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

  # Settings for Relay board 2 (ball valves)
  Relay_2_1_PIN = 35
  Relay_2_2_PIN = 36
  GPIO.setmode(GPIO.BOARD)
  GPIO.setup(Relay_2_1_PIN, GPIO.OUT, initial=GPIO.LOW)
  GPIO.setup(Relay_2_2_PIN, GPIO.OUT, initial=GPIO.LOW)

# Init FlowMeter instance and pulse callback
flow_meter_1 = FlowMeter()
flow_meter_2 = FlowMeter()
flow_meter_3 = FlowMeter()

if (not emulating):
  GPIO.add_event_detect(Flow_1_PIN, GPIO.RISING, callback=flow_meter_1.pulseCallback, bouncetime=20)
  GPIO.add_event_detect(Flow_2_PIN, GPIO.RISING, callback=flow_meter_2.pulseCallback, bouncetime=20)
  GPIO.add_event_detect(Flow_3_PIN, GPIO.RISING, callback=flow_meter_3.pulseCallback, bouncetime=20)

# Use drinking water, not rain barrel
use_barrel = False

# Zone 1: Grass sweat (Valve Relay 1_1, Flow 1)
if use_barrel: print("Watering Grass (sweat pipes) from barrel...")
else: print("Watering Grass (sweat pipes) from drinking water...")
valve = 1
area = 10 * 8
liters = area * net_evap
logger.info("Need to put %.0f liters on the grass", liters)   

# start water source
if use_barrel:
  logger.info("Setting RAIN (barrel) water ON")
  if (not emulating): GPIO.output(Relay_2_2_PIN, GPIO.HIGH)
else:
  logger.info("Setting DRINKING water ON")
  if (not emulating): GPIO.output(Relay_2_1_PIN, GPIO.HIGH)
logger.info("Waiting 15 seconds for valve to fully open...")
sleep(15)

# start irrigation flow
logger.info("Setting grass relay %d to ON", valve)   
if (not emulating): bus.write_byte_data(Relay_1_ADDR, 1, Relay_1_ON)
start_time = datetime.now()
actual_liters = 0.0

# Get current timestamp and flow meter reading
flow_rate_1 = flow_meter_1.getFlowRate()
logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate_1)
duration = liters / flow_rate_1 * 60
logger.info("Stopping in about %d seconds", duration)
previous_time = start_time

while duration > 0:
  sleep(min(60, duration))
  current_time = datetime.now()
  current_seconds = (current_time - previous_time).total_seconds()
  flow_rate_1 = flow_meter_1.getFlowRate()
  logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate_1)
  actual_liters += current_seconds / 60 * flow_rate_1
  duration = (liters - actual_liters) / flow_rate_1 * 60 
  if duration > 0:
    logger.info("Watered %.0f liters, %.0f liters remaining (ready in about %d seconds)", actual_liters, liters - actual_liters, duration)
    previous_time = current_time
  else:
    logger.info("Watered %.0f liters", actual_liters)

# stop irrigation flow
logger.info("Setting grass relay %d to OFF", valve)   
if (not emulating): bus.write_byte_data(Relay_1_ADDR, 1, Relay_1_OFF)
stop_time = datetime.now()

# stop water source
if use_barrel:
  logger.info("Setting rain (barrel) water OFF")
  if (not emulating): GPIO.output(Relay_2_2_PIN, GPIO.LOW)
else:
  logger.info("Setting drinking water OFF")
  if (not emulating): GPIO.output(Relay_2_1_PIN, GPIO.LOW)
logger.info("Waiting 15 seconds for valve to fully close...")
sleep(15)

# Calculate actual watering
diff_time = (stop_time - start_time).total_seconds()
watering = diff_time * flow_rate_1 / 60
watering_mm = watering / area
logger.debug("Actual watering: time %d seconds, %.0f liters = %.1f mm", diff_time, watering, watering_mm)

sleep(10)

# Zone 2: Front garden drip (Valve Relay 1_2, Flow 2)
if use_barrel: print("Watering Front garden (drip pipes) from barrel...")
else: print("Watering Front garden (drip pipes) from drinking water...")
valve = 2
area = 10 * 4 + 8 * 4
liters = area * net_evap
logger.info("Need to put %.0f liters on the front garden", liters)   

# start water source
if use_barrel:
  logger.info("Setting RAIN (barrel) water ON")
  if (not emulating): GPIO.output(Relay_2_2_PIN, GPIO.HIGH)
else:
  logger.info("Setting DRINKING water ON")
  if (not emulating): GPIO.output(Relay_2_1_PIN, GPIO.HIGH)
logger.info("Waiting 15 seconds for valve to fully open...")
sleep(15)

# start irrigation flow
logger.info("Setting front garden relay %d to ON", valve)   
if (not emulating): bus.write_byte_data(Relay_1_ADDR, 2, Relay_1_ON)
start_time = datetime.now()
actual_liters = 0.0

# Get current timestamp and flow meter reading
flow_rate_2 = flow_meter_2.getFlowRate()
logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate_2)
duration = liters / flow_rate_2 * 60
logger.info("Stopping in about %d seconds", duration)
previous_time = start_time

while duration > 0:
  sleep(min(60, duration))
  current_time = datetime.now()
  current_seconds = (current_time - previous_time).total_seconds()
  flow_rate_2 = flow_meter_2.getFlowRate()
  logger.debug("Flow rate: %.0f liter(s) per minute", flow_rate_2)
  actual_liters += current_seconds / 60 * flow_rate_2
  duration = (liters - actual_liters) / flow_rate_2 * 60 
  if duration > 0:
    logger.info("Watered %.0f liters, %.0f liters remaining (ready in about %d seconds)", actual_liters, liters - actual_liters, duration)
    previous_time = current_time
  else:
    logger.info("Watered %.0f liters", actual_liters)

# stop flow
logger.info("Setting front garden relay %d to OFF", valve)   
if (not emulating): bus.write_byte_data(Relay_1_ADDR, 2, Relay_1_OFF)
stop_time = datetime.now()

# stop water source
if use_barrel:
  logger.info("Setting rain (barrel) water OFF")
  if (not emulating): GPIO.output(Relay_2_2_PIN, GPIO.LOW)
else:
  logger.info("Setting drinking water OFF")
  if (not emulating): GPIO.output(Relay_2_1_PIN, GPIO.LOW)
logger.info("Waiting 15 seconds for valve to fully close...")
sleep(15)


# Add irrigation amount (mm) to database
query = "INSERT INTO irrigated (dateTime, watered) VALUES (%s, %s)"
insert_time = time.time()
insert_water = watering_mm
values = (insert_time, insert_water)
logger.debug("Query: %s", query)
logger.debug("Values: %d, %f", insert_time, insert_water)
if (not emulating):
  logger.debug("Adding to database")
  cursor.execute(query, values)
  db.commit()

# Close irrigation database
if (db.is_connected()):
  db.close()
  cursor.close()
  logger.info("MySQL connection is closed")

print("Done.")
