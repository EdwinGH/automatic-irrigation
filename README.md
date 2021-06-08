# Automatic Irrigation
 Raspberry Pi script for automatic irrigation based on weatherstation data.
 
 * Python script to be launched preferably during night time
 * Reads Weatherstation data of past days from WeeWX database
 * Calculates with Makkink formula the net evaporation (evaporation - rain - watering)
 * Maintains database of amount of watering done
 * Steers relay boards on Raspberry Pi to open and close valves
 * Supports multiple water sources (barrel, drinking water)
 * SUpports multiple irrigation zones with area (m2), shadow (%), flow requirements (drip or sprinkler system)
 * Measures the flow rate to calculate liters of watering
 * Writes the amount of millimeter watered in database

Still in beta phase; all parts functional, but needs to be finetuned.
