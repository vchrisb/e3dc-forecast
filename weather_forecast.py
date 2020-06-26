import requests
import json
import os
import re
import time
import collections
import datetime
import logging
from statistics import mean
from ratelimit import limits, RateLimitException
from backoff import on_exception, expo


#logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(message)s")

polling_cycle = 15
readings = 20
sliding_average_grid = collections.deque(maxlen=readings)
sliding_average_L1 = collections.deque(maxlen=readings)
sliding_average_house = collections.deque(maxlen=readings)
sliding_average_ac = collections.deque(maxlen=readings)
sliding_average_acCurrent = collections.deque(maxlen=readings)

# Sample Basic Auth Url with login values as username and password
url_base = os.getenv("REST_URL")
url_poll = url_base + "/api/poll"
url_power_data = url_base + "/api/power_data"
url_power_settings = url_base + "/api/power_settings"
url_info = url_base + "/api/system_info"
url_battery = url_base + "/api/battery_data"
url_pvi = url_base + "/api/pvi_data"

user = os.getenv("REST_USERNAME")
passwd = os.getenv("REST_PASSWORD")
auth_values = (user, passwd)


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
@limits(calls=12, period=3600)
def forecast():

    logging.info("Getting weather forecast")
    request = requests.get(url_weather)

    if request.status_code == 429:
        period_remaining = (60 - datetime.datetime.now(datetime.timezone.utc).minute) * 60
        raise RateLimitException('API response: {}'.format(request.status_code), period_remaining)
    
    forecast = request.json()
    today_iso = datetime.date.today().isoformat()
    expression = r"{} (\d\d)".format(today_iso)
    for item in forecast["result"]["watts"].items():
        m = re.match(expression,item[0])
        if m:
            watt_hours[int(m.group(1))] = item[1]

    watt_battery = 0
    for hour in range(datetime.datetime.utcnow().hour,13+1):
        if watt_hours[hour] >= 4600:
            watt_battery += watt_hours[hour] - 4600
    
    #watt_day = forecast["result"]["watt_hours_day"][today]
    watt_day = sum(watt_hours)
    return watt_hours, watt_day, watt_battery

@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
def set_powerlimits(powerLimitsUsed=False, maxChargePower=1500, weatherRegulatedChargeEnabled=False):        
    payload = { 'powerLimitsUsed': powerLimitsUsed, 'maxChargePower': maxChargePower, 'weatherRegulatedChargeEnabled': weatherRegulatedChargeEnabled}
    headers = {'Content-Type': 'application/json'}
    request = requests.post(url_power_settings, auth=auth_values, data=json.dumps(payload), headers=headers)
    if request.status_code == 200:
        logging.info("Power Limits set to {} and max charge power to {}".format(powerLimitsUsed, maxChargePower))
        return True
    else:
        logging.info("Failed to set powerlimits")
        return False

@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
def get_e3dc(url):
    logging.info("Requesting {}".format(url))
    return requests.get(url, auth=auth_values).json()

# Make a request to the endpoint using the correct auth values for info

response_info = get_e3dc(url_info)
deratePower = response_info["deratePower"]
installedPeakPower = response_info["installedPeakPower"] / 1000
maxChargePowerTotal = 1500

#weather forecast
lat = os.getenv("FORECAST_LAT")
lon = os.getenv("FORECAST_LON")
dec = os.getenv("FORECAST_DEC")
az = os.getenv("FORECAST_AZ")

kwp = installedPeakPower
url_weather = "https://api.forecast.solar/estimate/{}/{}/{}/{}/{}".format(lat, lon, dec, az, kwp)
watt_hours = [0]*24
watt_day = 0
watt_battery = 0

next_cycle = datetime.datetime.now(datetime.timezone.utc)
next_forecast = next_cycle

while(True):

    if next_forecast < datetime.datetime.now(datetime.timezone.utc):
        next_forecast = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(0,600)
        try:
            watt_hours, watt_day, watt_battery = forecast()
        except RateLimitException:
            logging.info("Ratelimit")
            pass
        except requests.exceptions.RequestException:
            logging.info("Connection Error")
            pass           

    # Make a request to the endpoint using the correct auth values
    #response_battery = requests.get(url_battery, auth=auth_values).json()
    #free_battery = response_battery["moduleVoltage"] * (response_battery["usuableCapacity"] - response_battery["usuableRemainingCapacity"])
    
    #print(free_battery)
    logging.info("Watt Hours: {}".format(watt_hours))
    logging.info("Watt Day: {}".format(watt_day))
    logging.info("Watt Battery: {}".format(watt_battery))

    # Make a request to the endpoint using the correct auth values
    response_poll = get_e3dc(url_poll)

    # Convert JSON to dict and print
    house = response_poll["consumption"]["house"]
    battery = response_poll["consumption"]["battery"]
    grid = response_poll["production"]["grid"]
    solar = response_poll["production"]["solar"]
    stateOfCharge = response_poll["stateOfCharge"]

    if battery < 0:
        logging.info("skipping cycle due to battery discharge")
        time.sleep(polling_cycle)
        continue
        #acPower = acPower + battery

    sliding_average_house.append(house)
    sliding_average_grid.append(grid)

    mean_house = round(mean(sliding_average_house),2)
    mean_grid = round(mean(sliding_average_grid),2)

    # power_data
    response_power_data = get_e3dc(url_power_data)
    L1 = response_power_data["power"]["L1"]
    sliding_average_L1.append(L1)

    mean_L1 = round(mean(sliding_average_L1),2)

    # pvi
    response_pvi = get_e3dc(url_pvi)
    acPower = response_pvi["acPower"]
    acCurrent = response_pvi["acCurrent"]

    sliding_average_ac.append(acPower)
    sliding_average_acCurrent.append(acCurrent)

    mean_ac = round(mean(sliding_average_ac),2)
    mean_acCurrent = round(mean(sliding_average_acCurrent),2)

    # get power_settings
    response_power = get_e3dc(url_power_settings)
    powerLimitsUsed = response_power["powerLimitsUsed"]
    maxChargePower = response_power["maxChargePower"]

    logging.info("Grid: {}".format(mean_grid))
    logging.info("L1: {}".format(mean_L1))
    logging.info("House: {}".format(mean_house))
    logging.info("AC: {}".format(mean_ac))
    logging.info("AC Current: {}".format(mean_acCurrent))


    if next_cycle < datetime.datetime.now(datetime.timezone.utc):
        logging.info("next cycle")
        # if it is after 13 o'clock, try disabling powerlimits and set next_cylce to nextday 24 o'clock
        if datetime.time(12,0,0) <= datetime.datetime.now(datetime.timezone.utc).time() < datetime.time(23,59,59):
            next_cycle = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
            logging.info("between 12 and 5 o'clock UTC, disable powerLimits")
            powerLimitsUsed = False   

        elif datetime.time(0,0,0) <= datetime.datetime.now(datetime.timezone.utc).time() < datetime.time(7,30,0) and mean_ac < 4300:
            print(datetime.datetime.now(datetime.timezone.utc).hour)
            logging.info("between 0:00 and 7:30 o'clock UTC, enable powerLimits")
            powerLimitsUsed = True
            maxChargePower = 0

        elif sum(watt_hours[0:15]) < 25000:
            logging.info("skipping as forcasted only {} until 14 o´clock UTC".format(sum(watt_hours[0:15])))
            logging.info("disable powerLimits")
            powerLimitsUsed = False

        #keep charging level at at least 10%
        #elif stateOfCharge < 10:
        #    logging.info("below 10% SoC, disable powerlimits")
        #    powerLimitsUsed = False
        
        #elif mean_grid >= 0.997 * deratePower or mean_ac >= 0.995 * 4600:
        elif mean_grid <= -0.997 * deratePower or mean_acCurrent >= 19.5 or mean_ac >= 4450:
            logging.info("derate or line limit reaching, increasing charge power")
            if maxChargePower < maxChargePowerTotal:
                powerLimitsUsed = True
                maxChargePower = maxChargePower + 100
            else:
                powerLimitsUsed = False
                logging.info("max charge power reached")
        elif mean_acCurrent <= 10.0: #mean_ac <= 
            logging.info("line limit below 90%, decreasing charge power")
            if maxChargePower > 0:
                powerLimitsUsed = True
                maxChargePower = maxChargePower - 100
            else:
                logging.info("charge disabled")            
        else:
            pass

        if response_power["powerLimitsUsed"] != powerLimitsUsed or response_power["maxChargePower"] != maxChargePower:
            set_powerlimits(powerLimitsUsed, maxChargePower)
            next_cycle = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(0,180) 
    
    # polling_cycle
    time.sleep(polling_cycle)


