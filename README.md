# e3dc-forecast

## Run

```
export REST_URL="https://e3dc-rest.domain.local"
export REST_USERNAME="admin"
export REST_PASSWORD="admin"
docker run --name e3dc-forecast -e REST_URL -e REST_USERNAME -e REST_PASSWORD vchrisb/e3dc-forecast
```