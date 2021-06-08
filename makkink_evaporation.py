#!/usr/bin/env python
#
# Routines from http://python.hydrology-amsterdam.nl/moduledoc/_modules/evaplib.html#Em
# Hosted at https://github.com/Kirubaharan/hydrology/blob/master/checkdam/meteolib.py
#
# Release 2021-04-22 Updated to numpy and added logging
#
# Author E Zuidema
#
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

progname='makkink_evaporation.py'
version = "2021-04-22"

import sys
import logging
import numpy
import math

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
    # Initiate the output array
    es = numpy.zeros(n)
    # Calculate saturated vapour pressures, distinguish between water/ice
    for i in range(0, n):
      if airtemp[i] < 0:
        # Saturation vapour pressure equation for ice
        log_pi = - 9.09718 * (273.16 / (airtemp[i] + 273.15) - 1.0) \
                 - 3.56654 * math.log10(273.16 / (airtemp[i] + 273.15)) \
                 + 0.876793 * (1.0 - (airtemp[i] + 273.15) / 273.16) \
                 + math.log10(6.1071)
        es[i] = math.pow(10, log_pi)
      else:
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
    # Initiate the output arrays
    Delta = numpy.zeros(n)
    # calculate vapour pressure
    es = es_calc(airtemp) # in Pa
    # Convert es (Pa) to kPa
    es = es / 1000.0
    # Calculate Delta
    for i in range(0, n):
      Delta[i] = es[i] * 4098.0 / math.pow((airtemp[i] + 237.3), 2)*1000
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
    # Initiate the output arrays
    eact = numpy.zeros(n)
    # Calculate saturation vapour pressures
    es = es_calc(airtemp)
    for i in range(0, n):
      # Calculate actual vapour pressure
      eact[i] = float(rh[i]) / 100.0 * es[i]
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
    # Initiate the output arrays
    cp = numpy.zeros(n)
    # calculate vapour pressures
    eact = ea_calc(airtemp, rh)
    # Calculate cp
    for i in range(0, n):
      cp[i] = 0.24 * 4185.5 * (1 + 0.8 * (0.622 * eact[i] / (airpress[i] - eact[i])))
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
    # Initiate the output arrays
    L = numpy.zeros(n)
    # Calculate lambda
    for i in range(0, n):
      L[i] = 4185.5 * (751.78 - 0.5655 * (airtemp[i] + 273.15))
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
    # Initiate the output arrays
    gamma = numpy.zeros(n)
    # Calculate cp and Lambda values
    cp = cp_calc(airtemp, rh, airpress)
    L = L_calc(airtemp)
    # Calculate gamma
    for i in range(0, n):
      gamma[i] = cp[i] * airpress[i] / (0.622 * L[i])
  return gamma # in Pa\K

def Em(logger, \
       airtemp = numpy.array([]),\
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
