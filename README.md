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

## Electronic circuit used
See https://github.com/EdwinGH/automatic-irrigation/blob/main/circuit.png

## Dependencies
The following packages are needed:
* python3-mysql.connector
* python3-numpy
Also for mailing the results (see the crontab) the msmtp package was installed

## How to run
Crontab on Raspberry Pi to start and monitor the process:

    0  7   *   *   *   /usr/bin/python3 /home/pi/irrigation/daily-irrigation.py -l debug -f /home/pi/irrigation/daily-irrigation.log -s 192.168.10.10 -u USER -p Pass |/usr/bin/mail -s "Pi Irrigation result" user@host
    0  8   *   *   *   OUTPUT=`/bin/ps -eaf |grep -i irrigation |grep python|grep -v sh` && echo "$OUTPUT" | /usr/bin/mail -s "Pi Irrigation running" user@host
    0  9   *   *   *   /usr/bin/pkill -f 'irrigation' && /usr/bin/mail -s "Pi Irrigation Killed process" user@host`

So irrigation starts at 7AM, with MySQL Weewx weather database on 192.168.10.10 and username USER and password Pass. It captures debug messages locally in a file, and sends a mail to user@host with the normal output.
At 8AM it sends a mail if still running
And at 9AM it sends a graceful SigTerm to close the valves (with SystemExit exception handling in the Python code).
