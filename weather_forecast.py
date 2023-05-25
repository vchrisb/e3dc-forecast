import collections
import datetime
import json
import logging
import os
import re
import time
from statistics import mean
from typing import Any, Collection
from zoneinfo import ZoneInfo

import requests
from backoff import expo, on_exception
from ratelimit import RateLimitException, limits

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(message)s")

polling_cycle: int = 15
readings: int = 20
sliding_average_grid: Collection
sliding_average_L1: Collection
sliding_average_house: Collection
sliding_average_ac: Collection
sliding_average_acApparent: Collection
sliding_average_acCurrent: Collection

mean_house: float
mean_grid: float
mean_ac: float
mean_acApparent: float
mean_acCurrent: float

newDay: bool

# Sample Basic Auth Url with login values as username and password
url_base: str = os.getenv("REST_URL", default="https://localhost")
url_poll: str = url_base + "/api/poll"
url_powermeter_data: str = url_base + "/api/powermeter_data"
url_power_settings: str = url_base + "/api/power_settings"
url_info: str = url_base + "/api/system_info"
url_status: str = url_base + "/api/system_status"
url_battery: str = url_base + "/api/battery_data"
url_pvi: str = url_base + "/api/pvi_data"

user: str = os.getenv("REST_USERNAME")
passwd: str = os.getenv("REST_PASSWORD")
auth_values: tuple = (user, passwd)

zone: str = ZoneInfo("Europe/Berlin")


def init_values():
    global sliding_average_grid
    global sliding_average_L1
    global sliding_average_house
    global sliding_average_ac
    global sliding_average_acApparent
    global sliding_average_acCurrent

    global mean_house
    global mean_grid
    global mean_ac
    global mean_acApparent
    global mean_acCurrent

    global newDay

    sliding_average_grid = collections.deque(maxlen=readings)
    sliding_average_L1 = collections.deque(maxlen=readings)
    sliding_average_house = collections.deque(maxlen=readings)
    sliding_average_ac = collections.deque(maxlen=readings)
    sliding_average_acApparent = collections.deque(maxlen=readings)
    sliding_average_acCurrent = collections.deque(maxlen=readings)

    mean_house = 0.0
    mean_grid = 0.0
    mean_ac = 0.0
    mean_acApparent = 0.0
    mean_acCurrent = 0.0

    newDay = True


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
@limits(calls=12, period=3600)
def forecast():
    logging.info("Getting weather forecast")
    request = requests.get(url_weather)

    if request.status_code == 429:
        period_remaining = (60 - datetime.datetime.now(zone).minute) * 60
        raise RateLimitException(
            "API response: {}".format(request.status_code), period_remaining
        )

    forecast = request.json()
    today_iso = datetime.date.today().isoformat()
    expression = r"{} (\d\d)".format(today_iso)
    for item in forecast["result"]["watts"].items():
        m = re.match(expression, item[0])
        if m:
            watt_hours[int(m.group(1))] = item[1]

    watt_battery = 0
    for hour in range(datetime.datetime.now(zone).hour, 13 + 1):
        if watt_hours[hour] >= 4600:
            watt_battery += watt_hours[hour] - 4600

    # watt_day = forecast["result"]["watt_hours_day"][today]
    watt_day = sum(watt_hours)
    return watt_hours, watt_day, watt_battery


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
def set_powerlimits(
    powerLimitsUsed=False, maxChargePower=1500, weatherRegulatedChargeEnabled=False
):
    payload = {
        "powerLimitsUsed": powerLimitsUsed,
        "maxChargePower": maxChargePower,
        "weatherRegulatedChargeEnabled": weatherRegulatedChargeEnabled,
    }
    headers = {"Content-Type": "application/json"}
    request = requests.post(
        url_power_settings, auth=auth_values, data=json.dumps(payload), headers=headers
    )
    if request.status_code == 200:
        logging.info(
            "Power Limits set to {} and max charge power to {}".format(
                powerLimitsUsed, maxChargePower
            )
        )
        return True
    else:
        logging.info("Failed to set powerlimits")
        return False


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
def get_e3dc(url):
    logging.info("Requesting {}".format(url))
    return requests.get(url, auth=auth_values).json()


# Make a request to the endpoint using the correct auth values for info

response_info: Any = get_e3dc(url_info)
deratePower: int = response_info["deratePower"]
installedPeakPower: int = response_info["installedPeakPower"] / 1000
maxChargePowerTotal = 1500

# weather forecast
lat: str = os.getenv("FORECAST_LAT")
lon: str = os.getenv("FORECAST_LON")
dec: str = os.getenv("FORECAST_DEC")
az: str = os.getenv("FORECAST_AZ")

url_weather: str = "https://api.forecast.solar/estimate/{}/{}/{}/{}/{}".format(
    lat, lon, dec, az, installedPeakPower
)
watt_hours: list[int] = [0] * 24
watt_day: int = 0
watt_battery: int = 0

next_cycle: datetime = datetime.datetime.now(zone)
next_forecast: datetime = next_cycle
seconds_to_sleep: int

init_values()

while True:
    seconds_to_sleep = 0
    # if it is after 13 o'clock, try disabling powerlimits and and sleep until 5
    if (
        not datetime.time(5, 0, 0)
        <= datetime.datetime.now(zone).time()
        < datetime.time(13, 0, 0)
    ):
        now = datetime.datetime.now(zone)
        future = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if now.hour >= 5:
            future += datetime.timedelta(days=1)
        logging.info("between 13 and 5 o'clock, disable powerLimits")
        set_powerlimits(False)
        init_values()
        time.sleep((future - now).total_seconds())

    if next_forecast < datetime.datetime.now(zone):
        next_forecast = datetime.datetime.now(zone) + datetime.timedelta(0, 1800)
        try:
            watt_hours, watt_day, watt_battery = forecast()
        except RateLimitException:
            logging.info("Ratelimit")
            pass
        except requests.exceptions.RequestException:
            logging.info("Connection Error")
            pass

    # Make a request to the endpoint using the correct auth values
    # response_battery = requests.get(url_battery, auth=auth_values).json()
    # free_battery = response_battery["moduleVoltage"] * (response_battery["usuableCapacity"] - response_battery["usuableRemainingCapacity"])

    # print(free_battery)

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
        # acPower = acPower + battery

    sliding_average_house.append(house)
    sliding_average_grid.append(grid)

    mean_house = round(mean(sliding_average_house), 2)
    mean_grid = round(mean(sliding_average_grid), 2)

    # power_data
    # response_power_data = get_e3dc(url_powermeter_data)
    # L1 = response_power_data["power"]["L1"]
    # sliding_average_L1.append(L1)

    # mean_L1 = round(mean(sliding_average_L1), 2)

    # pvi
    response_pvi = get_e3dc(url_pvi)
    acApparentPower = response_pvi["phases"]["0"]["apparentPower"]
    acPower = response_pvi["phases"]["0"]["power"]
    acCurrent = response_pvi["phases"]["0"]["current"]

    # need rework
    # if mean_acApparent > 0 and acApparentPower > 0:
    #     # try to detect clouds
    #     if (acApparentPower / mean_acApparent) < 0.8:
    #         logging.info("AC Apparent: {}".format(acApparentPower))
    #         logging.info("AC Apparent mean: {}".format(mean_acApparent))
    #         logging.info("Likely clouds. Waiting for next cycle.")
    #         time.sleep(polling_cycle)
    #         continue

    sliding_average_ac.append(acPower)
    sliding_average_acApparent.append(acApparentPower)
    sliding_average_acCurrent.append(acCurrent)

    mean_ac = round(mean(sliding_average_ac), 2)
    mean_acApparent = round(mean(sliding_average_acApparent), 2)
    mean_acCurrent = round(mean(sliding_average_acCurrent), 2)

    # get power_settings
    response_power = get_e3dc(url_power_settings)
    powerLimitsUsed = response_power["powerLimitsUsed"]
    maxChargePower: int = response_power["maxChargePower"]

    # get system status
    response_info = get_e3dc(url_status)
    pvDerated = response_info["pvDerated"]

    if next_cycle < datetime.datetime.now(zone):
        logging.info("### next cycle")

        logging.info("Forecast Watt Hours: {}".format(watt_hours))
        logging.info("Forecast Watt Day: {}".format(watt_day))
        logging.info("Forecast Watt Battery: {}".format(watt_battery))

        logging.info("Grid: {}".format(mean_grid))
        # logging.info("L1: {}".format(mean_L1))
        logging.info("House: {}".format(mean_house))
        logging.info("AC mean: {}".format(mean_ac))
        logging.info("AC: {}".format(acPower))
        logging.info("AC Apparent mean: {}".format(mean_acApparent))
        logging.info("AC Apparent: {}".format(acApparentPower))
        logging.info("AC Current mean: {}".format(mean_acCurrent))
        logging.info("AC Current: {}".format(acCurrent))
        logging.info("PV Derated: {}".format(pvDerated))
        logging.info("Power Limits Used: {}".format(powerLimitsUsed))
        logging.info("Max Charge Power: {}".format(maxChargePower))

        if sum(watt_hours[0:15]) < 25000:
            logging.info(
                "skipping as forcasted only {} until 14 o´clock".format(
                    sum(watt_hours[0:15])
                )
            )
            logging.info("disable powerLimits, reassess in one hour")
            powerLimitsUsed = False
            seconds_to_sleep = 3600

        # keep charging level at at least 10%
        elif stateOfCharge < 10:
            logging.info("below 10% SoC, disable powerlimits")
            powerLimitsUsed = False

        # elif mean_grid >= 0.997 * deratePower or mean_ac >= 0.995 * 4600:
        elif (
            #    mean_grid <= -0.997 * deratePower
            #    or mean_acCurrent >= 19.5
            pvDerated
            or mean_acApparent >= 4400
        ):
            logging.info("derate or line limit reaching, increasing charge power")
            maxChargePower = round(maxChargePower + 200, -2)
            if maxChargePower < maxChargePowerTotal:
                powerLimitsUsed = True
            else:
                seconds_to_sleep = (
                    datetime.datetime.now(zone)
                    - datetime.datetime.now(zone).replace(
                        hour=13, minute=0, second=0, microsecond=0
                    )
                ).total_seconds()
                powerLimitsUsed = False
                logging.info("max charge power reached")
        # elif mean_acCurrent <= 10.0: #mean_ac <=
        #     logging.info("line limit below 90%, decreasing charge power")
        #     if maxChargePower > 0:
        #         powerLimitsUsed = True
        #         maxChargePower = maxChargePower - 50
        #     else:
        #         logging.info("charge disabled")
        elif powerLimitsUsed is False and newDay:
            logging.info("enable powerLimits and set max charge to 0")
            powerLimitsUsed = True
            maxChargePower = 0
            newDay = False
        else:
            logging.info("nothing to do")

        if (
            response_power["powerLimitsUsed"] != powerLimitsUsed
            or response_power["maxChargePower"] != maxChargePower
        ):
            set_powerlimits(powerLimitsUsed, maxChargePower)
            # clear sliding_average_acApparent
            sliding_average_acApparent = collections.deque(maxlen=readings)
            # wait until 50% of the sliding_average_acApparent updates
            next_cycle = datetime.datetime.now(zone) + datetime.timedelta(
                0, (readings / 2) * polling_cycle
            )
    if seconds_to_sleep > 0:
        logging.info("Sleeping {} seconds".format(seconds_to_sleep))
        time.sleep(seconds_to_sleep)
    # polling_cycle
    time.sleep(polling_cycle)
