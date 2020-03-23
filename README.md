# e3dc-forecast

## Run Docker

```
export REST_URL="https://e3dc-rest.domain.local"
export REST_USERNAME="admin"
export REST_PASSWORD="admin"
export FORECAST_LAT=""
export FORECAST_LON=""
export FORECAST_DEC=""
export FORECAST_AZ=""
docker run --name e3dc-forecast -e REST_URL -e REST_USERNAME -e REST_PASSWORD -e FORECAST_LAT -e FORECAST_LON -e FORECAST_DEC -e FORECAST_AZ vchrisb/e3dc-forecast
```

## Run Kubernetes

Create Secret:
```
kubectl create secret generic e3dc-forecast-secret --from-literal=username='admin' --from-literal=password='admin' --from-literal=url='https://e3dc-rest.domain.local' --from-literal=lat='' --from-literal=lon='' --from-literal=dec='' --from-literal=az=''
```

Deploy with Ingress Controller:
```
kubectl apply -f ingress.yml -f service-ingress.yml -f deplyoment.yml
```