# Automatic Irrigation
 Raspberry Pi script for automatic irrigation based on weatherstation data.
 
 * Python script to be launched preferably during night time
 * Reads Weatherstation data of past days from WeeWX database
 * Calculates with Makkink formula the net evaporation (evaporation - rain - watering)
 * Maintains database of amount of watering done
 * Steers relay boards on Raspberry Pi to open and close valves
 * Supports multiple water sources (barrel, drinking water)
 * Supports multiple irrigation zones with area (m2), shadow (%), flow requirements (drip or sprinkler system)
 * Measures the flow rate to calculate liters of watering
 * Writes the amount of millimeter watered in database

Still in beta phase; all parts functional, but needs to be finetuned.

# How to run
Crontab on Raspberry Pi to start and monitor the process:
[  0  7   *   *   *   /usr/bin/python3 /home/pi/irrigation/daily-irrigation.py -l debug -f /home/pi/irrigation/daily-irrigation.log -s 192.168.10.10 -u USER -p Pass |/usr/bin/mail -s "Pi Irrigation result" user@host
  0  8   *   *   *   OUTPUT=`/bin/ps -eaf |grep -i irrigation |grep python|grep -v sh` && echo "$OUTPUT" | /usr/bin/mail -s "Pi Irrigation running" user@host
  0  9   *   *   *   /usr/bin/pkill -f 'irrigation' && /usr/bin/mail -s "Pi Irrigation Killed process" user@host]
  
  
