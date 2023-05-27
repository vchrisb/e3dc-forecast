import collections
import datetime
import json
import logging
import os
import re
import time
from statistics import mean
from typing import Any, Collection, Tuple
from zoneinfo import ZoneInfo

import requests
from backoff import expo, on_exception
from ratelimit import RateLimitException, limits

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(message)s")

polling_cycle: int = 15
readings: int = 20
sliding_average_grid: Collection = collections.deque(maxlen=readings)
sliding_average_L1: Collection = collections.deque(maxlen=readings)
sliding_average_house: Collection = collections.deque(maxlen=readings)
sliding_average_ac: Collection = collections.deque(maxlen=readings)
sliding_average_acApparent: Collection = collections.deque(maxlen=readings)
sliding_average_acCurrent: Collection = collections.deque(maxlen=readings)

mean_house: float = 0.0
mean_grid: float = 0.0
mean_ac: float = 0.0
mean_acApparent: float = 0.0
mean_acCurrent: float = 0.0

reset: bool = True

# Sample Basic Auth Url with login values as username and password
url_base: str = os.environ["REST_URL"]
url_poll: str = url_base + "/api/poll"
url_powermeter_data: str = url_base + "/api/powermeter_data"
url_power_settings: str = url_base + "/api/power_settings"
url_info: str = url_base + "/api/system_info"
url_status: str = url_base + "/api/system_status"
url_battery: str = url_base + "/api/battery_data"
url_pvi: str = url_base + "/api/pvi_data"

user: str = os.environ["REST_USERNAME"]
passwd: str = os.environ["REST_PASSWORD"]
auth_values: tuple = (user, passwd)

zone: ZoneInfo = ZoneInfo("Europe/Berlin")


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
@limits(calls=12, period=3600)
def forecast() -> Tuple[list[int], int, int]:
    logging.info("Getting weather forecast")
    request = requests.get(url_weather, timeout=30)

    if request.status_code == 429:
        period_remaining = (60 - datetime.datetime.now(zone).minute) * 60
        raise RateLimitException(
            "API response: {}".format(request.status_code), period_remaining
        )

    forecast = request.json()
    today_iso = datetime.date.today().isoformat()
    expression = r"{} (\d\d)".format(today_iso)
    watt_hours: list[int] = [0] * 24
    for item in forecast["result"]["watts"].items():
        m = re.match(expression, item[0])
        if m:
            watt_hours[int(m.group(1))] = int(item[1])

    watt_battery = 0
    for hour in range(datetime.datetime.now(zone).hour, 13 + 1):
        if watt_hours[hour] >= 4600:
            watt_battery += watt_hours[hour] - 4600

    # watt_day = forecast["result"]["watt_hours_day"][today]
    watt_day = sum(watt_hours)
    return watt_hours, watt_day, watt_battery


@on_exception(expo, requests.exceptions.RequestException, max_tries=8)
def set_powerlimits(
    powerLimitsUsed: bool = False,
    maxChargePower: int | None = None,
    weatherRegulatedChargeEnabled: bool = False,
):
    if maxChargePower is None:
        maxChargePower = maxChargePowerTotal
    payload = {
        "powerLimitsUsed": powerLimitsUsed,
        "maxChargePower": maxChargePower,
        "weatherRegulatedChargeEnabled": weatherRegulatedChargeEnabled,
    }
    headers = {"Content-Type": "application/json"}
    request = requests.post(
        url_power_settings,
        auth=auth_values,
        data=json.dumps(payload),
        headers=headers,
        timeout=20,
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
    return requests.get(url, auth=auth_values, timeout=20).json()


# Make a request to the endpoint using the correct auth values for info

response_info: Any = get_e3dc(url_info)
deratePower: int = response_info["deratePower"]
installedPeakPower: int = response_info["installedPeakPower"]
maxChargePowerTotal: int = response_info["maxBatChargePower"]

# weather forecast
lat: str = os.environ["FORECAST_LAT"]
lon: str = os.environ["FORECAST_LON"]
dec: str = os.environ["FORECAST_DEC"]
az: str = os.environ["FORECAST_AZ"]

url_weather: str = "https://api.forecast.solar/estimate/{}/{}/{}/{}/{}".format(
    lat, lon, dec, az, installedPeakPower / 1000
)
watt_hours: list[int] = [0] * 24
watt_day: int = 0
watt_battery: int = 0

next_cycle: datetime.datetime = datetime.datetime.now(zone)
next_forecast: datetime.datetime = next_cycle
seconds_to_sleep: int

logging.info("Derate Power: {}".format(deratePower))
logging.info("Installed Peak Power: {}".format(installedPeakPower))
logging.info("Max Charge Power: {}".format(maxChargePowerTotal))
logging.info("Forecast Solar: {}".format(url_weather))

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

        reset = True

        time.sleep((future - now).total_seconds())

    if next_forecast < datetime.datetime.now(zone):
        next_forecast = datetime.datetime.now(zone) + datetime.timedelta(0, 1800)
        try:
            watt_hours, watt_day, watt_battery = forecast()  # type: ignore
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
    acApparentPower: float = response_pvi["phases"]["0"]["apparentPower"]
    acPower: float = response_pvi["phases"]["0"]["power"]
    acCurrent: float = response_pvi["phases"]["0"]["current"]

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

        logging.info("SoC: {}".format(stateOfCharge))
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
            reset = True

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
                seconds_to_sleep = int(
                    (
                        datetime.datetime.now(zone).replace(
                            hour=13, minute=0, second=0, microsecond=0
                        )
                        - datetime.datetime.now(zone)
                    ).total_seconds()
                )
                powerLimitsUsed = False
                maxChargePower = maxChargePowerTotal
                logging.info("max charge power reached")
        # elif mean_acCurrent <= 10.0: #mean_ac <=
        #     logging.info("line limit below 90%, decreasing charge power")
        #     if maxChargePower > 0:
        #         powerLimitsUsed = True
        #         maxChargePower = maxChargePower - 50
        #     else:
        #         logging.info("charge disabled")
        # if it is the beginning of a new day or after a pod restart
        elif powerLimitsUsed is False and reset and stateOfCharge < 85:
            logging.info("enable powerLimits and set max charge to 0")
            powerLimitsUsed = True
            maxChargePower = 0
            reset = False
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
